#!/usr/bin/env python3
"""
Script to generate Reddit refresh token for the MCP server.

Prerequisites:
1. Create a Reddit app at https://www.reddit.com/prefs/apps
2. Choose "script" or "web app" type
3. Set redirect URI to http://localhost:8080
4. Copy your client ID and client secret
"""
import os

try:
    import praw
except ImportError:
    print("Please install praw: pip install praw")
    exit(1)

def get_refresh_token():
    """Generate a refresh token for Reddit API access"""

    # Get credentials from environment or prompt
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")

    if not client_id:
        client_id = input("Enter your Reddit Client ID: ").strip()
    if not client_secret:
        client_secret = input("Enter your Reddit Client Secret: ").strip()

    redirect_uri = "http://localhost:8080"

    # Create Reddit instance
    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        user_agent="MCP Reddit Server"
    )

    # Get authorization URL
    scopes = ["read"]  # We only need read access
    auth_url = reddit.auth.url(scopes, "temp_state", "permanent")

    print()
    print("=" * 60)
    print("STEP 1: Visit this URL to authorize the app:")
    print("=" * 60)
    print(auth_url)
    print()
    print("=" * 60)
    print("STEP 2: After authorizing, copy the 'code' from the redirect URL")
    print("=" * 60)
    print("The redirect URL looks like: http://localhost:8080/?state=temp_state&code=XXXXX")
    print()

    # Get the authorization code from user
    auth_code = input("Enter the 'code' parameter: ").strip()

    try:
        # Exchange auth code for refresh token
        refresh_token = reddit.auth.authorize(auth_code)

        print()
        print("=" * 60)
        print("SUCCESS! Your refresh token:")
        print("=" * 60)
        print(refresh_token)
        print()
        print("Add this to your .env file as:")
        print(f"REDDIT_REFRESH_TOKEN={refresh_token}")

        return refresh_token

    except Exception as e:
        print(f"Error getting refresh token: {e}")
        return None

if __name__ == "__main__":
    get_refresh_token()
