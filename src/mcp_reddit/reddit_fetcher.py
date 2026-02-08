import os
from typing import Optional, Dict, List, Tuple
from redditwarp.ASYNC import Client
from redditwarp.models.submission_ASYNC import LinkPost, TextPost, GalleryPost
from fastmcp import FastMCP
import logging
import asyncio
from collections import defaultdict

mcp = FastMCP("Reddit MCP")

REDDIT_CLIENT_ID=os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET=os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_REFRESH_TOKEN=os.getenv("REDDIT_REFRESH_TOKEN")

CREDS = [x for x in [REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_REFRESH_TOKEN] if x]

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
    """Minimal filtering - mainly exclude obvious spam/irrelevant content, keep most posts for LLM analysis"""
    # Always include text posts
    if isinstance(submission, TextPost):
        return True
    
    # Include most link posts, only filter out obvious spam/irrelevant
    if isinstance(submission, LinkPost):
        url = getattr(submission, 'url', '').lower() if hasattr(submission, 'url') else ""
        domain = getattr(submission, 'domain', '').lower() if hasattr(submission, 'domain') else ""
        
        # Only filter out obvious spam/irrelevant domains - keep most content
        spam_patterns = [
            'spam', 'casino', 'gambling', 'porn', 'xxx', 'adult',
            'malware', 'phishing', 'scam'
        ]
        
        content_text = f"{url} {domain}"
        if any(pattern in content_text for pattern in spam_patterns):
            return False
    
    # Include gallery posts and other content - let LLM decide relevance
    return True

@mcp.tool()
async def reddit_topic(topic: str, limit: int = 50, max_subreddits: int = 20) -> str:
    """
    Get posts from topic-related subreddits.

    Args:
        topic: Topic name (e.g. programming)
        limit: Posts to fetch (default: 50)
        max_subreddits: Max subreddits (default: 20)
    """
    try:
        topic_mapping = _load_topic_mapping()
        
        if topic not in topic_mapping:
            available_topics = list(topic_mapping.keys())
            return f"Topic '{topic}' not found. Available topics: {', '.join(available_topics[:10])}..."
        
        # Query ALL subreddits if max_subreddits is high, otherwise limit
        all_topic_subreddits = topic_mapping[topic]
        if max_subreddits >= len(all_topic_subreddits):
            subreddits = all_topic_subreddits  # Use ALL subreddits
        else:
            subreddits = all_topic_subreddits[:max_subreddits]
        
        all_posts = []
        seen_titles = set()  # For deduplication
        
        # Fetch posts from multiple subreddits concurrently
        tasks = []
        for subreddit in subreddits:
            task = asyncio.create_task(_fetch_filtered_posts(subreddit, limit // len(subreddits) + 5))
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
                        post['source_subreddit'] = subreddit
                        all_posts.append(post)
            except Exception as e:
                logging.warning(f"Failed to fetch from r/{subreddit}: {e}")
                continue
        
        # Provide comprehensive data for LLM analysis - sort by creation time (newest first)
        all_posts.sort(key=lambda x: x['created_utc'], reverse=True)
        
        # Limit to requested number
        top_posts = all_posts[:limit]
        
        if not top_posts:
            return f"No readable content found for topic '{topic}'"
        
        # Format comprehensive output for LLM analysis
        result_lines = [f"Latest content for topic: {topic} (from {len(set(p['source_subreddit'] for p in top_posts))} subreddits, sorted by recency)\n"]
        
        from datetime import datetime
        for i, post in enumerate(top_posts, 1):
            # Convert timestamp to human readable
            created_time = datetime.fromtimestamp(post['created_utc']).strftime('%Y-%m-%d %H:%M:%S') if post['created_utc'] > 0 else 'unknown'
            
            post_info = (
                f"{i}. [{post['source_subreddit']}] {post['title']}\n"
                f"   📊 Score: {post['score']} | 💬 Comments: {post['comment_count']} | 👤 Author: {post['author']}\n"
                f"   📅 Created: {created_time} | 📈 Upvote ratio: {post.get('upvote_ratio', 0):.2f}\n"
                f"   🏷️ Type: {post['type']} | Domain: {post.get('domain', 'self')} | Flair: {post.get('flair', 'none')}\n"
                f"   📝 Content: {post['content'][:300]}{'...' if len(post['content']) > 300 else ''}\n"
                f"   🔗 Link: https://reddit.com{post['permalink']}\n"
            )
            result_lines.append(post_info)
        
        return "\n".join(result_lines)
        
    except Exception as e:
        logging.error(f"Error in fetch_reddit_topic_latest: {e}")
        return f"An error occurred: {str(e)}"

async def _fetch_filtered_posts(subreddit: str, limit: int) -> List[Dict]:
    """Helper function to fetch comprehensive post data from a single subreddit for LLM analysis"""
    posts = []
    try:
        count = 0
        async for submission in client.p.subreddit.pull.hot(subreddit):
            if count >= limit * 3:  # Fetch more to account for minimal filtering
                break
                
            if _is_readable_content(submission):
                # Include comprehensive data for LLM analysis
                post_data = {
                    'title': submission.title,
                    'score': submission.score,
                    'comment_count': submission.comment_count,
                    'author': submission.author_display_name or '[deleted]',
                    'type': _get_post_type(submission),
                    'content': _get_content(submission) or '',
                    'permalink': submission.permalink,
                    'created_utc': submission.created_at.timestamp() if hasattr(submission, 'created_at') and submission.created_at else 0,
                    'url': getattr(submission, 'url', ''),
                    'domain': getattr(submission, 'domain', ''),
                    'upvote_ratio': getattr(submission, 'upvote_ratio', 0),
                    'is_self': isinstance(submission, TextPost),
                    'flair': getattr(submission, 'link_flair_text', '') or ''
                }
                posts.append(post_data)
                
                if len(posts) >= limit:
                    break
            count += 1
            
    except Exception as e:
        logging.warning(f"Error fetching from r/{subreddit}: {e}")
    
    return posts

@mcp.tool()
async def reddit_hot(subreddit: str, limit: int = 10) -> str:
    """
    Get hot posts from a subreddit.

    Args:
        subreddit: Subreddit name (without r/)
        limit: Posts to fetch (default: 10)
    """
    try:
        posts = []
        async for submission in client.p.subreddit.pull.hot(subreddit, limit):
            post_info = (
                f"Title: {submission.title}\n"
                f"Score: {submission.score}\n"
                f"Comments: {submission.comment_count}\n"
                f"Author: {submission.author_display_name or '[deleted]'}\n"
                f"Type: {_get_post_type(submission)}\n"
                f"Content: {_get_content(submission)}\n"
                f"Link: https://reddit.com{submission.permalink}\n"
                f"---"
            )
            posts.append(post_info)

        return "\n\n".join(posts)

    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        return f"An error occurred: {str(e)}"

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

@mcp.tool()
async def reddit_post(post_id: str, comment_limit: int = 20, comment_depth: int = 3) -> str:
    """
    Get post content and comments.

    Args:
        post_id: Reddit post ID
        comment_limit: Comments to fetch (default: 20)
        comment_depth: Thread depth (default: 3)
    """
    try:
        submission = await client.p.submission.fetch(post_id)

        content = (
            f"Title: {submission.title}\n"
            f"Score: {submission.score}\n"
            f"Author: {submission.author_display_name or '[deleted]'}\n"
            f"Type: {_get_post_type(submission)}\n"
            f"Content: {_get_content(submission)}\n"
        )

        comments = await client.p.comment_tree.fetch(post_id, sort='top', limit=comment_limit, depth=comment_depth)
        if comments.children:
            content += "\nComments:\n"
            for comment in comments.children:
                content += "\n" + _format_comment_tree(comment)
        else:
            content += "\nNo comments found."

        return content

    except Exception as e:
        return f"An error occurred: {str(e)}"

@mcp.tool()
async def reddit_front(sort: str = "hot", limit: int = 10, time_filter: str = "day") -> str:
    """
    Get Reddit front page posts.

    Args:
        sort: hot, top, or new (default: hot)
        limit: Posts to fetch (default: 10)
        time_filter: hour/day/week/month/year/all
    """
    try:
        posts = []
        if sort == "hot":
            async for submission in client.p.front.pull.hot(limit):
                posts.append(_format_post_info(submission))
        elif sort == "top":
            async for submission in client.p.front.pull.top(limit, time=time_filter):
                posts.append(_format_post_info(submission))
        elif sort == "new":
            async for submission in client.p.front.pull.new(limit):
                posts.append(_format_post_info(submission))
        else:
            return f"Invalid sort method: {sort}. Use 'hot', 'top', or 'new'"
        
        return "\n\n".join(posts)
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        return f"An error occurred: {str(e)}"

@mcp.tool()
async def reddit_top(subreddit: str, time_period: str = "week", limit: int = 10) -> str:
    """
    Get top posts from a subreddit by time.

    Args:
        subreddit: Subreddit name (without r/)
        time_period: hour/day/week/month/year/all
        limit: Posts to fetch (default: 10)
    """
    try:
        posts = []
        async for submission in client.p.subreddit.pull.top(subreddit, limit, time=time_period):
            posts.append(_format_post_info(submission))
        
        return f"Top posts from r/{subreddit} in the last {time_period}:\n\n" + "\n\n".join(posts)
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        return f"An error occurred: {str(e)}"

@mcp.tool()
async def reddit_new(subreddit: str, limit: int = 10) -> str:
    """
    Get newest posts from a subreddit.

    Args:
        subreddit: Subreddit name (without r/)
        limit: Posts to fetch (default: 10)
    """
    try:
        posts = []
        async for submission in client.p.subreddit.pull.new(subreddit, limit):
            posts.append(_format_post_info(submission))
        
        return f"Newest posts from r/{subreddit}:\n\n" + "\n\n".join(posts)
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        return f"An error occurred: {str(e)}"

@mcp.tool()
async def reddit_rising(subreddit: str, limit: int = 10) -> str:
    """
    Get rising/trending posts from a subreddit.

    Args:
        subreddit: Subreddit name (without r/)
        limit: Posts to fetch (default: 10)
    """
    try:
        posts = []
        async for submission in client.p.subreddit.pull.rising(subreddit, limit):
            posts.append(_format_post_info(submission))
        
        return f"Rising posts from r/{subreddit}:\n\n" + "\n\n".join(posts)
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        return f"An error occurred: {str(e)}"

@mcp.tool()
async def reddit_info(subreddit: str) -> str:
    """
    Get subreddit info (subscribers, description).

    Args:
        subreddit: Subreddit name (without r/)
    """
    try:
        subr = await client.p.subreddit.fetch_by_name(subreddit)
        info = (
            f"Subreddit: r/{subr.name}\n"
            f"Subscribers: {subr.subscriber_count:,}\n"
            f"Title: {subr.title}\n"
            f"Description: {subr.public_description or 'No description available'}\n"
            f"Created: {subr.created_at}\n"
            f"NSFW: {'Yes' if getattr(subr, 'over18', False) else 'No'}\n"
            f"Type: {getattr(subr, 'subreddit_type', 'public')}\n"
        )
        return info
    except Exception as e:
        logging.error(f"An error occurred: {str(e)}")
        return f"An error occurred: {str(e)}"

def _format_post_info(submission) -> str:
    """Helper method to format post information consistently"""
    return (
        f"Title: {submission.title}\n"
        f"Subreddit: r/{submission.subreddit.name}\n"
        f"Score: {submission.score}\n"
        f"Comments: {submission.comment_count}\n"
        f"Author: {submission.author_display_name or '[deleted]'}\n"
        f"Type: {_get_post_type(submission)}\n"
        f"Content: {_get_content(submission)}\n"
        f"Link: https://reddit.com{submission.permalink}\n"
        f"---"
    )

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
