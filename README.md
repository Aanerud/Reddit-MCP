# MCP Reddit Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/introduction) server that provides tools for fetching Reddit content. Works with Claude Desktop, Microsoft Copilot Studio, Power Automate, and any MCP-compatible client.

## Features

- **8 Reddit Tools**: Fetch hot, new, rising, and top posts from any subreddit
- **Multiple Connection Methods**:
  - MCP Streamable HTTP (for Copilot Studio, Claude Desktop)
  - REST API with OpenAPI spec (for Power Automate, direct HTTP)
- **Topic Aggregation**: Fetch posts from multiple related subreddits by topic
- **Comment Trees**: Get post content with threaded comments
- **Subreddit Info**: Get subscriber counts and descriptions

## Available Tools

| Tool | Description |
|------|-------------|
| `reddit_hot` | Get hot posts from a subreddit |
| `reddit_new` | Get newest posts from a subreddit |
| `reddit_rising` | Get rising/trending posts |
| `reddit_top` | Get top posts by time period |
| `reddit_front` | Get Reddit front page posts |
| `reddit_post` | Get post content with comments |
| `reddit_topic` | Get posts from topic-related subreddits |
| `reddit_info` | Get subreddit info (subscribers, description) |

---

## Quick Start

### Prerequisites

1. **Reddit API Credentials**: Create an app at https://www.reddit.com/prefs/apps
   - Choose "script" type
   - Set redirect URI to `http://localhost:8080`
   - Note your Client ID and Client Secret

2. **Generate Refresh Token**:
   ```bash
   pip install praw
   python get_refresh_token.py
   ```

3. **Create `.env` file**:
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

---

## Local Development

### Running Locally

```bash
# Install dependencies
pip install uv
uv sync

# Run the server
uv run uvicorn mcp_reddit.web_server:app --host 0.0.0.0 --port 8000
```

The server will be available at:
- REST API: http://localhost:8000
- API Docs: http://localhost:8000/docs
- MCP Endpoint: http://localhost:8000/mcp/ (requires MCP_API_KEY)

### Using with Claude Desktop (Local)

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "reddit": {
      "command": "uv",
      "args": ["run", "uvicorn", "mcp_reddit.web_server:app", "--port", "8000"],
      "cwd": "/path/to/mcp-reddit",
      "env": {
        "REDDIT_CLIENT_ID": "your_client_id",
        "REDDIT_CLIENT_SECRET": "your_client_secret",
        "REDDIT_REFRESH_TOKEN": "your_refresh_token",
        "MCP_API_KEY": "your_api_key"
      }
    }
  }
}
```

Or use `mcp-remote` to connect to a running server:

```json
{
  "mcpServers": {
    "reddit": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "http://localhost:8000/mcp/",
        "--header",
        "X-API-Key: your_api_key"
      ]
    }
  }
}
```

---

## Azure Deployment

### Option 1: Automated Deployment Script

```bash
chmod +x deploy-to-azure.sh
./deploy-to-azure.sh
```

The script will:
1. Create a resource group
2. Create Azure Container Registry
3. Build and push the Docker image
4. Create Container Apps environment
5. Deploy the container with your Reddit credentials

### Option 2: Manual Deployment

#### 1. Create Azure Resources

```bash
# Set variables
RESOURCE_GROUP="mcp-reddit-rg"
LOCATION="westeurope"  # or your preferred region
ACR_NAME="yourregistryname"

# Create resource group
az group create --name $RESOURCE_GROUP --location $LOCATION

# Create container registry
az acr create --name $ACR_NAME --resource-group $RESOURCE_GROUP --sku Basic --admin-enabled true

# Create container apps environment
az containerapp env create --name mcp-reddit-env --resource-group $RESOURCE_GROUP --location $LOCATION
```

#### 2. Build and Push Image

```bash
az acr build --registry $ACR_NAME --image mcp-reddit:latest --file Dockerfile.azure .
```

#### 3. Deploy Container App

```bash
# Get ACR password
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)

# Generate MCP API key
MCP_API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Create container app
az containerapp create \
  --name mcp-reddit-server \
  --resource-group $RESOURCE_GROUP \
  --environment mcp-reddit-env \
  --image $ACR_NAME.azurecr.io/mcp-reddit:latest \
  --registry-server $ACR_NAME.azurecr.io \
  --registry-username $ACR_NAME \
  --registry-password "$ACR_PASSWORD" \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 5 \
  --cpu 0.5 \
  --memory 1Gi \
  --secrets \
    reddit-client-id="YOUR_CLIENT_ID" \
    reddit-client-secret="YOUR_CLIENT_SECRET" \
    reddit-refresh-token="YOUR_REFRESH_TOKEN" \
    mcp-api-key="$MCP_API_KEY" \
  --env-vars \
    REDDIT_CLIENT_ID=secretref:reddit-client-id \
    REDDIT_CLIENT_SECRET=secretref:reddit-client-secret \
    REDDIT_REFRESH_TOKEN=secretref:reddit-refresh-token \
    MCP_API_KEY=secretref:mcp-api-key
```

#### 4. Get Your Server URL

```bash
az containerapp show --name mcp-reddit-server --resource-group $RESOURCE_GROUP --query "properties.configuration.ingress.fqdn" -o tsv
```

#### 5. (Optional) Add Custom Domain

```bash
# Add custom domain
az containerapp hostname add \
  --name mcp-reddit-server \
  --resource-group $RESOURCE_GROUP \
  --hostname your-domain.com

# Bind SSL certificate
az containerapp hostname bind \
  --name mcp-reddit-server \
  --resource-group $RESOURCE_GROUP \
  --hostname your-domain.com \
  --environment mcp-reddit-env \
  --validation-method CNAME
```

---

## Connection Methods

### 1. MCP Streamable HTTP (Authenticated)

For **Microsoft Copilot Studio** and **Claude Desktop**:

| Setting | Value |
|---------|-------|
| URL | `https://your-server.com/mcp/` |
| Auth | API Key |
| Header | `X-API-Key` |

**Important**: The URL must have a trailing slash (`/mcp/`)

### 2. REST API / OpenAPI (No Auth Required)

For **Power Automate** and **direct HTTP calls**:

| Setting | Value |
|---------|-------|
| Base URL | `https://your-server.com` |
| OpenAPI Spec | `https://your-server.com/openapi-3.0.json` |
| Docs | `https://your-server.com/docs` |

**REST Endpoints**:
- `POST /api/hot-threads` - Get hot posts
- `POST /api/post-content` - Get post with comments
- `POST /api/topic-latest` - Get posts by topic
- `POST /api/front-page` - Get front page posts
- `POST /api/subreddit-posts-by-time` - Get top posts by time
- `POST /api/subreddit-new-posts` - Get new posts
- `POST /api/subreddit-rising-posts` - Get rising posts
- `POST /api/subreddit-info` - Get subreddit info
- `GET /api/topics` - List available topics

---

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `REDDIT_CLIENT_ID` | Yes | Reddit app client ID |
| `REDDIT_CLIENT_SECRET` | Yes | Reddit app client secret |
| `REDDIT_REFRESH_TOKEN` | Yes | Reddit OAuth refresh token |
| `MCP_API_KEY` | No | API key for MCP endpoint authentication |

### Topic Categories

The server includes predefined topic categories in `list.txt`:
- Programming
- Tech News
- AI/ML
- Gaming
- Science
- And more...

Edit `list.txt` to customize topic-to-subreddit mappings.

---

## Updating Deployment

```bash
# Rebuild image
az acr build --registry $ACR_NAME --image mcp-reddit:latest --file Dockerfile.azure .

# Force new revision
az containerapp update \
  --name mcp-reddit-server \
  --resource-group $RESOURCE_GROUP \
  --image $ACR_NAME.azurecr.io/mcp-reddit:latest \
  --set-env-vars FORCE_UPDATE="$(date +%s)"
```

---

## Cleanup

```bash
# Delete all Azure resources
az group delete --name mcp-reddit-rg --yes --no-wait
```

---

## License

MIT License - see [LICENSE](LICENSE) for details.

## Credits

Based on [mcp-reddit](https://github.com/adhikasp/mcp-reddit) by adhikasp.
