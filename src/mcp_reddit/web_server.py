import os
import json
import secrets
from contextlib import asynccontextmanager
from typing import Optional, Dict, List, Tuple
from redditwarp.ASYNC import Client
from redditwarp.models.submission_ASYNC import LinkPost, TextPost, GalleryPost
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send
import logging
import asyncio
from collections import defaultdict

# Import the MCP server for Streamable HTTP endpoint
from mcp_reddit.reddit_fetcher import mcp as reddit_mcp

# MCP API Key for authentication (required for /mcp endpoint)
MCP_API_KEY = os.getenv("MCP_API_KEY")

# Create MCP HTTP app if API key is configured
mcp_http_app = None
if MCP_API_KEY:
    mcp_http_app = reddit_mcp.http_app(path="/")


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    """Combined lifespan that handles both FastAPI and MCP app lifecycles."""
    if mcp_http_app is not None:
        # Run MCP app's lifespan alongside FastAPI
        async with mcp_http_app.lifespan(mcp_http_app):
            yield
    else:
        yield


# Initialize FastAPI app with combined lifespan
app = FastAPI(
    title="Reddit MCP API",
    description="A REST API for fetching Reddit content, compatible with Power Automate and Copilot Studio MCP",
    version="2.0.0",
    lifespan=combined_lifespan
)


class MCPApiKeyMiddleware:
    """
    ASGI Middleware to validate API Key for MCP Streamable HTTP endpoint.
    Copilot Studio sends the API key in the X-API-Key header.
    """
    def __init__(self, app: ASGIApp, api_key: str):
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http":
            # Extract headers
            headers = dict(scope.get("headers", []))
            api_key_header = headers.get(b"x-api-key", b"").decode("utf-8")

            # Validate API key using constant-time comparison
            if not api_key_header or not secrets.compare_digest(api_key_header, self.api_key):
                # Return 401 Unauthorized
                response = Response(
                    content='{"error": "Invalid or missing API key"}',
                    status_code=401,
                    media_type="application/json"
                )
                await response(scope, receive, send)
                return

        # API key valid, proceed with request
        await self.app(scope, receive, send)

# Reddit credentials from environment
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET") 
REDDIT_REFRESH_TOKEN = os.getenv("REDDIT_REFRESH_TOKEN")

CREDS = [x for x in [REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_REFRESH_TOKEN] if x]

if not CREDS:
    raise ValueError("Reddit API credentials not found in environment variables")

client = Client(*CREDS)
logging.getLogger().setLevel(logging.WARNING)

def _load_topic_mapping() -> Dict[str, List[str]]:
    """Load topic to subreddit mapping from list.txt"""
    topic_mapping = {}
    current_topic = None
    
    try:
        # Get the directory where this script is located
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        list_file = os.path.join(script_dir, 'list.txt')
        
        with open(list_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') and line.endswith('#') == False:
                    # Topic header
                    current_topic = line[1:].strip()
                    topic_mapping[current_topic] = []
                elif line.startswith('/r/') and current_topic:
                    # Subreddit entry - clean it up
                    subreddit = line.replace('/r/', '').replace('/', '').strip()
                    if subreddit:
                        topic_mapping[current_topic].append(subreddit)
        
        return topic_mapping
    except Exception as e:
        logging.error(f"Error loading topic mapping: {e}")
        return {}

def _is_readable_content(submission) -> bool:
    """Filter for readable content (text posts and news articles)"""
    if isinstance(submission, TextPost):
        return True
    
    if isinstance(submission, LinkPost):
        # Check if it's likely a news article or readable content
        url = submission.permalink.lower() if submission.permalink else ""
        domain = getattr(submission, 'domain', '').lower() if hasattr(submission, 'domain') else ""
        
        # Common news/article domains and patterns
        readable_patterns = [
            'news', 'article', 'blog', 'medium.com', 'arxiv.org', 'github.com',
            'techcrunch.com', 'theverge.com', 'arstechnica.com', 'wired.com',
            'reuters.com', 'bbc.com', 'cnn.com', 'npr.org', 'nytimes.com',
            'washingtonpost.com', 'guardian.com', 'economist.com'
        ]
        
        # Filter out image/video content
        image_video_patterns = [
            'jpg', 'jpeg', 'png', 'gif', 'webp', 'mp4', 'webm', 'youtube.com',
            'youtu.be', 'tiktok.com', 'instagram.com', 'imgur.com', 'v.redd.it',
            'i.redd.it', 'gfycat.com', 'streamable.com'
        ]
        
        # Check if it contains image/video patterns
        content_text = f"{url} {domain}".lower()
        if any(pattern in content_text for pattern in image_video_patterns):
            return False
            
        # Check if it contains readable patterns or is a self-post
        if any(pattern in content_text for pattern in readable_patterns):
            return True
            
        # If title suggests it's discussion/text content
        title = submission.title.lower()
        discussion_words = ['discussion', 'question', 'ask', 'help', 'thoughts', 'opinion', 'analysis', 'review']
        if any(word in title for word in discussion_words):
            return True
    
    return False

async def _fetch_filtered_posts(subreddit: str, limit: int) -> List[Dict]:
    """Helper function to fetch and filter posts from a single subreddit"""
    posts = []
    try:
        count = 0
        async for submission in client.p.subreddit.pull.hot(subreddit):
            if count >= limit * 2:  # Fetch extra to account for filtering
                break
                
            if _is_readable_content(submission):
                post_data = {
                    'title': submission.title,
                    'score': submission.score,
                    'comment_count': submission.comment_count,
                    'author': submission.author_display_name or '[deleted]',
                    'type': _get_post_type(submission),
                    'content': _get_content(submission) or '',
                    'permalink': submission.permalink
                }
                posts.append(post_data)
                
                if len(posts) >= limit:
                    break
            count += 1
            
    except Exception as e:
        logging.warning(f"Error fetching from r/{subreddit}: {e}")
    
    return posts

# Pydantic models for request/response
class HotThreadsRequest(BaseModel):
    subreddit: str
    limit: int = 10

class PostContentRequest(BaseModel):
    post_id: str
    comment_limit: int = 20
    comment_depth: int = 3

class RedditPost(BaseModel):
    title: str
    score: int
    comments: int
    author: str
    post_type: str
    content: Optional[str]
    link: str

class HotThreadsResponse(BaseModel):
    subreddit: str
    posts: list[RedditPost]

class PostContentResponse(BaseModel):
    post_id: str
    title: str
    score: int
    author: str
    post_type: str
    content: Optional[str]
    comments: str

class FrontPageRequest(BaseModel):
    sort: str = "hot"
    limit: int = 10
    time_filter: str = "day"

class SubredditPostsByTimeRequest(BaseModel):
    subreddit: str
    time_period: str = "week"
    limit: int = 10

class SubredditNewPostsRequest(BaseModel):
    subreddit: str
    limit: int = 10

class SubredditRisingPostsRequest(BaseModel):
    subreddit: str
    limit: int = 10

class SubredditInfoRequest(BaseModel):
    subreddit: str

class TopicLatestRequest(BaseModel):
    topic: str
    limit: int = 50
    max_subreddits: int = 20  # Set to 999 to query ALL subreddits in topic

class FrontPageResponse(BaseModel):
    sort: str
    time_filter: str
    posts: list[RedditPost]

class SubredditInfoResponse(BaseModel):
    subreddit: str
    subscribers: int
    title: str
    description: str
    created_at: str
    nsfw: bool
    subreddit_type: str

class TopicPost(BaseModel):
    title: str
    score: int
    comments: int
    author: str
    post_type: str
    content: Optional[str]
    link: str
    source_subreddit: str
    created_utc: float
    url: str
    domain: str
    upvote_ratio: float
    is_self: bool
    flair: str

class TopicLatestResponse(BaseModel):
    topic: str
    total_posts: int
    subreddits_queried: int
    posts: List[TopicPost]

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint for Azure Container Apps"""
    return {"status": "healthy", "service": "Reddit MCP API"}

# Custom OpenAPI 3.0.3 endpoint for Power Automate compatibility  
@app.get("/openapi-3.0.json")
async def get_openapi_30():
    """Get OpenAPI 3.0.3 specification compatible with Power Automate - serves openapi-new.json"""
    try:
        # Load the openapi.json file
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        openapi_file = os.path.join(script_dir, 'openapi.json')
        
        with open(openapi_file, 'r') as f:
            openapi_spec = json.load(f)
        
        return JSONResponse(content=openapi_spec)
    except Exception as e:
        logging.error(f"Error loading openapi-new.json: {e}")
        # Fallback to basic spec if file not found
        fallback_spec = {
            "openapi": "3.0.3",
            "info": {
                "title": "Reddit MCP API",
                "description": "A REST API for fetching Reddit content",
                "version": "1.0.0"
            },
            "servers": [
                {
                    "url": "https://mcp-reddit-server.livelygrass-7c00d7ab.eastus.azurecontainerapps.io",
                    "description": "Production server"
                }
            ],
            "paths": {},
            "components": {"schemas": {}}
        }
        return JSONResponse(content=fallback_spec)

@app.get("/openapi-3.0.json-old") 
async def get_openapi_30_old():
    """Get basic OpenAPI 3.0.3 specification (legacy endpoint)"""
    openapi_30_spec = {
        "openapi": "3.0.3",
        "info": {
            "title": "Reddit MCP API",
            "description": "A REST API for fetching Reddit content, compatible with Power Automate and Microsoft Copilot Studio",
            "version": "1.0.0",
            "contact": {
                "name": "Reddit MCP Server"
            }
        },
        "servers": [
            {
                "url": "https://mcp-reddit-server.livelygrass-7c00d7ab.eastus.azurecontainerapps.io",
                "description": "Production server"
            }
        ],
        "paths": {
            "/health": {
                "get": {
                    "summary": "Health Check",
                    "description": "Check if the API is running and healthy",
                    "operationId": "healthCheck",
                    "responses": {
                        "200": {
                            "description": "API is healthy",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {
                                                "type": "string",
                                                "example": "healthy"
                                            },
                                            "service": {
                                                "type": "string",
                                                "example": "Reddit MCP API"
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/api/hot-threads": {
                "post": {
                    "summary": "Get Hot Threads from Subreddit",
                    "description": "Fetch hot threads from a specified subreddit with Reddit content",
                    "operationId": "getHotThreads",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["subreddit"],
                                    "properties": {
                                        "subreddit": {
                                            "type": "string",
                                            "description": "Name of the subreddit (without r/ prefix)",
                                            "example": "programming"
                                        },
                                        "limit": {
                                            "type": "integer",
                                            "description": "Number of posts to fetch",
                                            "default": 10,
                                            "minimum": 1,
                                            "maximum": 100
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "List of hot threads from the subreddit",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "subreddit": {
                                                "type": "string",
                                                "description": "The subreddit name"
                                            },
                                            "posts": {
                                                "type": "array",
                                                "items": {
                                                    "type": "object",
                                                    "properties": {
                                                        "title": {
                                                            "type": "string",
                                                            "description": "Post title"
                                                        },
                                                        "score": {
                                                            "type": "integer",
                                                            "description": "Post score (upvotes minus downvotes)"
                                                        },
                                                        "comments": {
                                                            "type": "integer",
                                                            "description": "Number of comments"
                                                        },
                                                        "author": {
                                                            "type": "string",
                                                            "description": "Post author username"
                                                        },
                                                        "post_type": {
                                                            "type": "string",
                                                            "enum": ["text", "link", "gallery", "unknown"],
                                                            "description": "Type of post"
                                                        },
                                                        "content": {
                                                            "type": "string",
                                                            "description": "Post content (text, URL, or gallery link)"
                                                        },
                                                        "link": {
                                                            "type": "string",
                                                            "description": "Reddit permalink to the post"
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        "422": {
                            "description": "Validation Error",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "detail": {
                                                "type": "string"
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        "500": {
                            "description": "Internal Server Error",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "detail": {
                                                "type": "string"
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/api/post-content": {
                "post": {
                    "summary": "Get Reddit Post Content",
                    "description": "Fetch detailed content of a specific Reddit post including comments",
                    "operationId": "getPostContent",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["post_id"],
                                    "properties": {
                                        "post_id": {
                                            "type": "string",
                                            "description": "Reddit post ID (without any prefixes)",
                                            "example": "1mubl8b"
                                        },
                                        "comment_limit": {
                                            "type": "integer",
                                            "description": "Number of top-level comments to fetch",
                                            "default": 20,
                                            "minimum": 0,
                                            "maximum": 100
                                        },
                                        "comment_depth": {
                                            "type": "integer",
                                            "description": "Maximum depth of comment tree to traverse",
                                            "default": 3,
                                            "minimum": 0,
                                            "maximum": 10
                                        }
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Detailed post content with comments",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "post_id": {
                                                "type": "string",
                                                "description": "The post ID"
                                            },
                                            "title": {
                                                "type": "string",
                                                "description": "Post title"
                                            },
                                            "score": {
                                                "type": "integer",
                                                "description": "Post score (upvotes minus downvotes)"
                                            },
                                            "author": {
                                                "type": "string",
                                                "description": "Post author username"
                                            },
                                            "post_type": {
                                                "type": "string",
                                                "enum": ["text", "link", "gallery", "unknown"],
                                                "description": "Type of post"
                                            },
                                            "content": {
                                                "type": "string",
                                                "description": "Post content (text, URL, or gallery link)"
                                            },
                                            "comments": {
                                                "type": "string",
                                                "description": "Formatted comment tree as text"
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        "422": {
                            "description": "Validation Error",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "detail": {
                                                "type": "string"
                                            }
                                        }
                                    }
                                }
                            }
                        },
                        "500": {
                            "description": "Internal Server Error",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "detail": {
                                                "type": "string"
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {}
        }
    }
    return JSONResponse(content=openapi_30_spec)

# Root endpoint
@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "message": "Reddit MCP API",
        "version": "2.0.0",
        "endpoints": {
            "rest_api": {
                "hot_threads": "/api/hot-threads",
                "post_content": "/api/post-content",
                "front_page": "/api/front-page",
                "subreddit_by_time": "/api/subreddit-posts-by-time",
                "subreddit_new": "/api/subreddit-new-posts",
                "subreddit_rising": "/api/subreddit-rising-posts",
                "subreddit_info": "/api/subreddit-info",
                "topic_latest": "/api/topic-latest",
                "topics": "/api/topics"
            },
            "mcp_protocol": {
                "streamable_http": "/mcp" if MCP_API_KEY else "(disabled - set MCP_API_KEY)",
                "info": "/mcp-info"
            },
            "utilities": {
                "health": "/health",
                "openapi": "/openapi.json",
                "openapi_30": "/openapi-3.0.json",
                "docs": "/docs"
            }
        },
        "integrations": {
            "power_automate": "Use /openapi-3.0.json for custom connector",
            "copilot_studio_mcp": "Use /mcp endpoint with X-API-Key header" if MCP_API_KEY else "Disabled"
        }
    }

@app.post("/api/hot-threads", response_model=HotThreadsResponse)
async def get_hot_threads(request: HotThreadsRequest):
    """
    Fetch hot threads from a subreddit
    Compatible with Power Automate HTTP connector
    """
    try:
        posts = []
        async for submission in client.p.subreddit.pull.hot(request.subreddit, request.limit):
            post = RedditPost(
                title=submission.title,
                score=submission.score,
                comments=submission.comment_count,
                author=submission.author_display_name or '[deleted]',
                post_type=_get_post_type(submission),
                content=_get_content(submission),
                link=f"https://reddit.com{submission.permalink}"
            )
            posts.append(post)

        return HotThreadsResponse(subreddit=request.subreddit, posts=posts)

    except Exception as e:
        logging.error(f"Error fetching hot threads: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching hot threads: {str(e)}")

@app.post("/api/post-content", response_model=PostContentResponse)
async def get_post_content(request: PostContentRequest):
    """
    Fetch detailed content of a specific post
    Compatible with Power Automate HTTP connector
    """
    try:
        submission = await client.p.submission.fetch(request.post_id)
        
        comments_content = ""
        comments = await client.p.comment_tree.fetch(
            request.post_id, 
            sort='top', 
            limit=request.comment_limit, 
            depth=request.comment_depth
        )
        
        if comments.children:
            comments_list = []
            for comment in comments.children:
                comments_list.append(_format_comment_tree(comment))
            comments_content = "\n\n".join(comments_list)
        else:
            comments_content = "No comments found."

        return PostContentResponse(
            post_id=request.post_id,
            title=submission.title,
            score=submission.score,
            author=submission.author_display_name or '[deleted]',
            post_type=_get_post_type(submission),
            content=_get_content(submission),
            comments=comments_content
        )

    except Exception as e:
        logging.error(f"Error fetching post content: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching post content: {str(e)}")

@app.post("/api/front-page", response_model=FrontPageResponse)
async def get_front_page_posts(request: FrontPageRequest):
    """
    Discover trending posts from Reddit's front page across all communities
    Compatible with Power Automate HTTP connector
    """
    try:
        posts = []
        if request.sort == "hot":
            async for submission in client.p.front.pull.hot(request.limit):
                posts.append(_create_reddit_post(submission))
        elif request.sort == "top":
            async for submission in client.p.front.pull.top(request.limit, time=request.time_filter):
                posts.append(_create_reddit_post(submission))
        elif request.sort == "new":
            async for submission in client.p.front.pull.new(request.limit):
                posts.append(_create_reddit_post(submission))
        else:
            raise HTTPException(status_code=400, detail="Invalid sort method. Use 'hot', 'top', or 'new'")

        return FrontPageResponse(sort=request.sort, time_filter=request.time_filter, posts=posts)

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching front page posts: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching front page posts: {str(e)}")

@app.post("/api/subreddit-posts-by-time", response_model=HotThreadsResponse)
async def get_subreddit_posts_by_time(request: SubredditPostsByTimeRequest):
    """
    Get top posts from a subreddit filtered by time to discover what was popular recently
    Compatible with Power Automate HTTP connector
    """
    try:
        posts = []
        async for submission in client.p.subreddit.pull.top(request.subreddit, request.limit, time=request.time_period):
            posts.append(_create_reddit_post(submission))

        return HotThreadsResponse(subreddit=request.subreddit, posts=posts)

    except Exception as e:
        logging.error(f"Error fetching subreddit posts by time: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching subreddit posts by time: {str(e)}")

@app.post("/api/subreddit-new-posts", response_model=HotThreadsResponse)
async def get_subreddit_new_posts(request: SubredditNewPostsRequest):
    """
    Get newest posts from a subreddit to discover fresh content and emerging trends
    Compatible with Power Automate HTTP connector
    """
    try:
        posts = []
        async for submission in client.p.subreddit.pull.new(request.subreddit, request.limit):
            posts.append(_create_reddit_post(submission))

        return HotThreadsResponse(subreddit=request.subreddit, posts=posts)

    except Exception as e:
        logging.error(f"Error fetching subreddit new posts: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching subreddit new posts: {str(e)}")

@app.post("/api/subreddit-rising-posts", response_model=HotThreadsResponse)
async def get_subreddit_rising_posts(request: SubredditRisingPostsRequest):
    """
    Get rising posts from a subreddit to catch trending content early before it becomes popular
    Compatible with Power Automate HTTP connector
    """
    try:
        posts = []
        async for submission in client.p.subreddit.pull.rising(request.subreddit, request.limit):
            posts.append(_create_reddit_post(submission))

        return HotThreadsResponse(subreddit=request.subreddit, posts=posts)

    except Exception as e:
        logging.error(f"Error fetching subreddit rising posts: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching subreddit rising posts: {str(e)}")

@app.post("/api/subreddit-info", response_model=SubredditInfoResponse)
async def get_subreddit_info(request: SubredditInfoRequest):
    """
    Get information about a subreddit including subscriber count, description, and activity level
    Compatible with Power Automate HTTP connector
    """
    try:
        subr = await client.p.subreddit.fetch_by_name(request.subreddit)
        
        return SubredditInfoResponse(
            subreddit=subr.name,
            subscribers=subr.subscriber_count,
            title=subr.title,
            description=subr.public_description or "No description available",
            created_at=str(subr.created_at),
            nsfw=getattr(subr, 'over18', False),
            subreddit_type=getattr(subr, 'subreddit_type', 'public')
        )

    except Exception as e:
        logging.error(f"Error fetching subreddit info: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching subreddit info: {str(e)}")

@app.post("/api/topic-latest", response_model=TopicLatestResponse)
async def get_topic_latest(request: TopicLatestRequest):
    """
    Fetch latest readable content from subreddits related to a topic
    Compatible with Power Automate HTTP connector
    """
    try:
        topic_mapping = _load_topic_mapping()
        
        if request.topic not in topic_mapping:
            available_topics = list(topic_mapping.keys())
            raise HTTPException(
                status_code=400, 
                detail=f"Topic '{request.topic}' not found. Available topics: {', '.join(available_topics[:10])}..."
            )
        
        # Query ALL subreddits if max_subreddits is high, otherwise limit
        all_topic_subreddits = topic_mapping[request.topic]
        if request.max_subreddits >= len(all_topic_subreddits):
            subreddits = all_topic_subreddits  # Use ALL subreddits
        else:
            subreddits = all_topic_subreddits[:request.max_subreddits]
        
        all_posts = []
        seen_titles = set()  # For deduplication
        
        # Fetch posts from multiple subreddits concurrently
        tasks = []
        for subreddit in subreddits:
            task = asyncio.create_task(_fetch_filtered_posts(subreddit, request.limit // len(subreddits) + 5))
            tasks.append((subreddit, task))
        
        # Wait for all tasks to complete
        for subreddit, task in tasks:
            try:
                posts = await task
                for post in posts:
                    # Simple deduplication by title
                    title_key = post['title'].lower().strip()
                    if title_key not in seen_titles:
                        seen_titles.add(title_key)
                        post_obj = TopicPost(
                            title=post['title'],
                            score=post['score'],
                            comments=post['comment_count'],
                            author=post['author'],
                            post_type=post['type'],
                            content=post['content'],
                            link=f"https://reddit.com{post['permalink']}",
                            source_subreddit=subreddit,
                            created_utc=post.get('created_utc', 0),
                            url=post.get('url', ''),
                            domain=post.get('domain', ''),
                            upvote_ratio=post.get('upvote_ratio', 0),
                            is_self=post.get('is_self', False),
                            flair=post.get('flair', '')
                        )
                        all_posts.append(post_obj)
            except Exception as e:
                logging.warning(f"Failed to fetch from r/{subreddit}: {e}")
                continue
        
        # Sort by recency (newest first) for LLM analysis
        all_posts.sort(key=lambda x: x.created_utc, reverse=True)
        
        # Limit to requested number
        top_posts = all_posts[:request.limit]
        
        return TopicLatestResponse(
            topic=request.topic,
            total_posts=len(top_posts),
            subreddits_queried=len(subreddits),
            posts=top_posts
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in get_topic_latest: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching topic latest: {str(e)}")

@app.get("/api/topics")
async def get_available_topics():
    """
    Get list of available topics
    """
    try:
        topic_mapping = _load_topic_mapping()
        topics = list(topic_mapping.keys())
        return {
            "topics": topics,
            "total_count": len(topics),
            "description": "Available topics for content aggregation"
        }
    except Exception as e:
        logging.error(f"Error getting topics: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting topics: {str(e)}")

def _create_reddit_post(submission) -> RedditPost:
    """Helper method to create RedditPost from submission"""
    return RedditPost(
        title=submission.title,
        score=submission.score,
        comments=submission.comment_count,
        author=submission.author_display_name or '[deleted]',
        post_type=_get_post_type(submission),
        content=_get_content(submission),
        link=f"https://reddit.com{submission.permalink}"
    )

def _format_comment_tree(comment_node, depth: int = 0) -> str:
    """Helper method to recursively format comment tree with proper indentation"""
    comment = comment_node.value
    indent = "-- " * depth
    content = (
        f"{indent}* Author: {comment.author_display_name or '[deleted]'}\n"
        f"{indent}  Score: {comment.score}\n"
        f"{indent}  {comment.body}\n"
    )

    for child in comment_node.children:
        content += "\n" + _format_comment_tree(child, depth + 1)

    return content

def _get_post_type(submission) -> str:
    """Helper method to determine post type"""
    if isinstance(submission, LinkPost):
        return 'link'
    elif isinstance(submission, TextPost):
        return 'text'
    elif isinstance(submission, GalleryPost):
        return 'gallery'
    return 'unknown'

def _get_content(submission) -> Optional[str]:
    """Helper method to extract post content based on type"""
    if isinstance(submission, LinkPost):
        return submission.permalink
    elif isinstance(submission, TextPost):
        return submission.body
    elif isinstance(submission, GalleryPost):
        return str(submission.gallery_link)
    return None


# =============================================================================
# MCP Streamable HTTP Endpoint for Copilot Studio
# =============================================================================
# This endpoint exposes the MCP protocol using Streamable HTTP transport,
# which is required by Microsoft Copilot Studio for native MCP integration.
#
# Authentication: API Key (X-API-Key header)
# Endpoint: /mcp
# =============================================================================

if MCP_API_KEY and mcp_http_app is not None:
    # Wrap with API key authentication middleware
    mcp_with_auth = MCPApiKeyMiddleware(mcp_http_app, MCP_API_KEY)

    # Mount at /mcp path - the MCP endpoint will be at /mcp/
    app.mount("/mcp", mcp_with_auth)

    logging.info("MCP Streamable HTTP endpoint mounted at /mcp (API key authentication enabled)")
else:
    logging.warning(
        "MCP_API_KEY not set - MCP Streamable HTTP endpoint is DISABLED. "
        "Set MCP_API_KEY environment variable to enable Copilot Studio integration."
    )


# MCP endpoint info for documentation
@app.get("/mcp-info")
async def mcp_info():
    """
    Information about the MCP Streamable HTTP endpoint for Copilot Studio integration.
    """
    mcp_enabled = MCP_API_KEY is not None
    return {
        "mcp_enabled": mcp_enabled,
        "endpoint": "/mcp" if mcp_enabled else None,
        "transport": "Streamable HTTP",
        "authentication": "API Key (X-API-Key header)" if mcp_enabled else None,
        "copilot_studio_compatible": mcp_enabled,
        "tools_available": [
            "reddit_topic",
            "reddit_hot",
            "reddit_post",
            "reddit_front",
            "reddit_top",
            "reddit_new",
            "reddit_rising",
            "reddit_info"
        ] if mcp_enabled else [],
        "setup_instructions": {
            "copilot_studio": "Use MCP Onboarding Wizard with Server URL: https://reddit.nstop.no/mcp/",
            "authentication_type": "API Key",
            "header_name": "X-API-Key",
            "note": "Trailing slash is required in the URL"
        } if mcp_enabled else {"error": "MCP_API_KEY environment variable not configured"}
    }