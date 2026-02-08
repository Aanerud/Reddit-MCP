#!/bin/bash

# Azure deployment script for MCP Reddit Server
set -e

# Configuration variables
RESOURCE_GROUP="mcp-reddit-rg"
LOCATION="westeurope"
ACR_NAME="mcpredditacr"
CONTAINER_APP_NAME="mcp-reddit-server"
CONTAINER_ENV_NAME="mcp-reddit-env"

echo "🚀 Starting Azure deployment for MCP Reddit Server..."

# Check if logged in to Azure
if ! az account show &> /dev/null; then
    echo "❌ Not logged in to Azure. Please run 'az login' first."
    exit 1
fi

# Create resource group
echo "📦 Creating resource group..."
az group create \
    --name $RESOURCE_GROUP \
    --location $LOCATION

# Create Azure Container Registry
echo "🏗️ Creating Azure Container Registry..."
az acr create \
    --resource-group $RESOURCE_GROUP \
    --name $ACR_NAME \
    --sku Basic \
    --admin-enabled true

# Get ACR login server
ACR_LOGIN_SERVER=$(az acr show --name $ACR_NAME --resource-group $RESOURCE_GROUP --query loginServer --output tsv)
echo "📍 ACR Login Server: $ACR_LOGIN_SERVER"

# Build and push Docker image
echo "🐳 Building and pushing Docker image..."
az acr build \
    --registry $ACR_NAME \
    --image mcp-reddit:latest \
    --file Dockerfile.azure \
    .

# Create Container Apps environment
echo "🌍 Creating Container Apps environment..."
az containerapp env create \
    --name $CONTAINER_ENV_NAME \
    --resource-group $RESOURCE_GROUP \
    --location $LOCATION

# Prompt for Reddit API credentials
echo ""
echo "🔑 Reddit API credentials required:"
echo "(Get these from https://www.reddit.com/prefs/apps)"
echo ""
read -p "Enter Reddit Client ID: " REDDIT_CLIENT_ID
read -s -p "Enter Reddit Client Secret: " REDDIT_CLIENT_SECRET
echo
read -s -p "Enter Reddit Refresh Token: " REDDIT_REFRESH_TOKEN
echo

# Generate MCP API Key
echo ""
echo "🔐 Generating MCP API Key..."
MCP_API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "Generated API Key: $MCP_API_KEY"
echo "(Save this - you'll need it to connect!)"

# Get ACR credentials
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)

# Create Container App
echo ""
echo "🚢 Creating Container App..."
az containerapp create \
    --name $CONTAINER_APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --environment $CONTAINER_ENV_NAME \
    --image $ACR_LOGIN_SERVER/mcp-reddit:latest \
    --target-port 8000 \
    --ingress external \
    --registry-server $ACR_LOGIN_SERVER \
    --registry-username $ACR_NAME \
    --registry-password "$ACR_PASSWORD" \
    --secrets \
        reddit-client-id="$REDDIT_CLIENT_ID" \
        reddit-client-secret="$REDDIT_CLIENT_SECRET" \
        reddit-refresh-token="$REDDIT_REFRESH_TOKEN" \
        mcp-api-key="$MCP_API_KEY" \
    --env-vars \
        REDDIT_CLIENT_ID=secretref:reddit-client-id \
        REDDIT_CLIENT_SECRET=secretref:reddit-client-secret \
        REDDIT_REFRESH_TOKEN=secretref:reddit-refresh-token \
        MCP_API_KEY=secretref:mcp-api-key \
    --cpu 0.5 \
    --memory 1Gi \
    --min-replicas 1 \
    --max-replicas 5

# Get the application URL
APP_URL=$(az containerapp show \
    --name $CONTAINER_APP_NAME \
    --resource-group $RESOURCE_GROUP \
    --query properties.configuration.ingress.fqdn \
    --output tsv)

echo ""
echo "✅ Deployment completed!"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🌐 Server URL: https://$APP_URL"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📡 Connection Methods:"
echo ""
echo "1. MCP Streamable HTTP (Copilot Studio / Claude Desktop):"
echo "   URL: https://$APP_URL/mcp/"
echo "   Auth: API Key"
echo "   Header: X-API-Key"
echo "   Key: $MCP_API_KEY"
echo ""
echo "2. REST API / OpenAPI (Power Automate):"
echo "   Base URL: https://$APP_URL"
echo "   OpenAPI: https://$APP_URL/openapi-3.0.json"
echo "   Docs: https://$APP_URL/docs"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🔧 To update the deployment:"
echo "az acr build --registry $ACR_NAME --image mcp-reddit:latest --file Dockerfile.azure ."
echo "az containerapp update --name $CONTAINER_APP_NAME --resource-group $RESOURCE_GROUP --image $ACR_LOGIN_SERVER/mcp-reddit:latest --set-env-vars FORCE_UPDATE=\"\$(date +%s)\""
echo ""
echo "🧹 To clean up resources:"
echo "az group delete --name $RESOURCE_GROUP --yes --no-wait"
