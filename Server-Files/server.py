import urllib.parse
import secrets
import requests
import traceback
from datetime import datetime, timedelta

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount  # ADD Mount here
from starlette.responses import JSONResponse, HTMLResponse
from starlette.requests import Request

import config
import database
import auth

from mcp_tools import tools_drive, tools_gmail, tools_calendar, tools_tasks

from config import CLIENT_ID, CLIENT_SECRET, SCOPES, REDIRECT_URI
from database import (
    create_user, 
    get_user_by_email, 
    store_tokens, 
    update_last_login, 
    create_session,
    log_action,
    return_connection,
    get_db_connection
)
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse as StarletteJSONResponse

# import tools_tasks 

# Initialize MCP
mcp = FastMCP("Google Drive, Gmail, Calendar & Tasks MCP")

# --- Register Drive Tools ---
mcp.tool()(tools_drive.list_drive_files)
mcp.tool()(tools_drive.search_drive_files)
mcp.tool()(tools_drive.read_file_by_name)
mcp.tool()(tools_drive.read_file_content)
mcp.tool()(tools_drive.update_document_content)
mcp.tool()(tools_drive.update_document_by_name)

# --- Register Gmail Tools ---
mcp.tool()(tools_gmail.list_emails)
mcp.tool()(tools_gmail.search_emails)
mcp.tool()(tools_gmail.read_email)
mcp.tool()(tools_gmail.send_email)
mcp.tool()(tools_gmail.mark_email_as_read)
mcp.tool()(tools_gmail.mark_email_as_unread)

# --- Register Calendar Tools ---
mcp.tool()(tools_calendar.list_calendar_events)
mcp.tool()(tools_calendar.create_calendar_event)
mcp.tool()(tools_calendar.update_calendar_event)
mcp.tool()(tools_calendar.delete_calendar_event)
mcp.tool()(tools_calendar.search_calendar_events)

# --- Register Tasks Tools ---
mcp.tool()(tools_tasks.list_task_lists)
mcp.tool()(tools_tasks.list_tasks)
mcp.tool()(tools_tasks.create_task)
mcp.tool()(tools_tasks.create_task_from_email)
mcp.tool()(tools_tasks.add_emails_to_tasks)
mcp.tool()(tools_tasks.create_task_from_email_search)
mcp.tool()(tools_tasks.update_task)
mcp.tool()(tools_tasks.complete_task)
mcp.tool()(tools_tasks.delete_task)

# --- Compound Tools ---
mcp.tool()(tools_tasks.create_task_from_email)
mcp.tool()(tools_tasks.add_emails_to_tasks)
mcp.tool()(tools_tasks.create_task_from_email_search)
mcp.tool()(tools_tasks.get_auth_status)

# --- Starlette App & OAuth Routes ---
mcp_asgi = mcp.http_app(path='/mcp')

async def start_auth(request: StarletteRequest):
    """Start the Google OAuth2 flow"""
    from google_auth_oauthlib.flow import Flow
    
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    
    auth_url, state = flow.authorization_url(
        access_type="offline", 
        prompt="consent"
    )
    
    return StarletteJSONResponse({"auth_url": auth_url})
    
async def auth_page(request: StarletteRequest):
    """HTML page to initiate OAuth flow"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Google OAuth Authentication</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 600px;
                margin: 50px auto;
                padding: 20px;
            }
            button {
                background-color: #4285f4;
                color: white;
                padding: 12px 24px;
                border: none;
                border-radius: 4px;
                font-size: 16px;
                cursor: pointer;
            }
            button:hover {
                background-color: #357ae8;
            }
            .info {
                background-color: #f0f0f0;
                padding: 15px;
                border-radius: 4px;
                margin-top: 20px;
            }
        </style>
    </head>
    <body>
        <h1>Google OAuth Authentication</h1>
        <p>Click the button below to authenticate with Google:</p>
        <button onclick="startAuth()">Authenticate with Google</button>
        
        <div class="info">
            <h3>What happens next:</h3>
            <ol>
                <li>You'll be redirected to Google to sign in</li>
                <li>Grant permissions to the application</li>
                <li>You'll be redirected back with your API key</li>
                <li>Save your API key - it won't be shown again!</li>
            </ol>
        </div>
        
        <script>
            async function startAuth() {
                try {
                    const response = await fetch('/auth');
                    const data = await response.json();
                    window.location.href = data.auth_url;
                } catch (error) {
                    alert('Error starting authentication: ' + error);
                }
            }
        </script>
    </body>
    </html>
    """
    from starlette.responses import HTMLResponse
    return HTMLResponse(content=html_content)

async def oauth_callback(request: StarletteRequest):
    """Handle the OAuth2 callback from Google"""
    from google_auth_oauthlib.flow import Flow
    import jwt  # You may need to: pip install PyJWT

    code = request.query_params.get("code")
    if not code:
        return StarletteJSONResponse({"error": "No code found in callback"}, status_code=400)
    
    try:
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            },
            scopes=SCOPES,
            redirect_uri=REDIRECT_URI,
        )
        
        flow.fetch_token(code=code)
        
        creds = flow.credentials
        token_data = {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
            "expires_in": (creds.expiry - datetime.utcnow()).total_seconds()
        }

        # CHANGED: Get user info from ID token instead of API call
        id_token = creds.id_token
        if not id_token:
            return StarletteJSONResponse(
                {"error": "No ID token received from Google"}, 
                status_code=500
            )
        
        # Decode the ID token (no verification needed since it came directly from Google)
        user_info = jwt.decode(id_token, options={"verify_signature": False})
        
        email = user_info.get("email")
        display_name = user_info.get("name", email)  # Fallback to email if no name
        
        if not email:
            return StarletteJSONResponse(
                {"error": "Could not retrieve email from ID token"}, 
                status_code=500
            )

        # Rest of your code remains the same...
        user = get_user_by_email(email)
        if user:
            user_id = user["user_id"]
            api_key = "REUSED"
        else:
            user_id, api_key = create_user(email, display_name)
        
        store_tokens(user_id, token_data, SCOPES)
        update_last_login(user_id)
        
        session_token = create_session(
            user_id, 
            request.client.host, 
            request.headers.get("User-Agent", "Unknown")
        )
        
        log_action(user_id, "oauth_callback", True, "auth", f"User {email} authenticated", request.client.host)

        response_body = {
            "message": "Authentication successful!",
            "user_id": user_id,
            "email": email,
            "session_token": session_token
        }
        
        if api_key != "REUSED":
            response_body["api_key"] = api_key
            response_body["api_key_message"] = "SAVE THIS API KEY. It will not be shown again."
        else:
            response_body["api_key_message"] = "API key already provisioned. Check your records."

        return StarletteJSONResponse(response_body)

    except Exception as e:
        traceback.print_exc()
        log_action("N/A", "oauth_callback", False, "auth", str(e), request.client.host)
        return StarletteJSONResponse({"error": str(e), "traceback": traceback.format_exc()}, status_code=500)
    
async def health(request: StarletteRequest):
    """Health check endpoint, including DB connection"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        return_connection(conn)
        db_status = "connected"
    except Exception as e:
        db_status = f"disconnected: {e}"

    return StarletteJSONResponse({
        "status": "ok",
        "database": db_status
    })

async def root(request: StarletteRequest):
    """Root endpoint"""
    return StarletteJSONResponse({
        "service": "Google Drive, Gmail, Calendar & Tasks MCP Server",
        "database_backend": "Azure SQL (pyodbc)",
        "endpoints": {
            "auth": "/auth - Start OAuth flow (returns auth_url)",
            "callback": "/oauth2callback - OAuth callback (handles redirect)",
            "health": "/health - Health check (includes DB)",
            "mcp": "/mcp/ - MCP protocol endpoint (POST only)"
        },
        "available_tools": [
            "Drive: list_drive_files, search_drive_files, read_file_by_name, read_file_content, update_document_content, update_document_by_name",
            "Gmail: list_emails, read_email, send_email, search_emails, mark_email_as_read, mark_email_as_unread",
            "Calendar: list_calendar_events, create_calendar_event, update_calendar_event, delete_calendar_event, search_calendar_events",
            "Tasks: list_task_lists, list_tasks, create_task, create_task_from_email, add_emails_to_tasks, create_task_from_email_search, update_task, complete_task, delete_task",
            "Auth: get_auth_status"
        ],
        "usage": {
            "step_1": "Visit /auth to get the authentication URL",
            "step_2": "Complete OAuth flow in browser",
            "step_3": "Save your API key from the callback response",
            "step_4": "Use your API key in all MCP tool calls"
        }
    })
    
app = Starlette(
    routes=[
        Route("/", root),
        Route("/start-auth", auth_page),
        Route("/auth", start_auth),
        Route("/oauth2callback", oauth_callback),
        Route("/health", health),
        Mount("/", mcp_asgi),  # Mount MCP at root - it handles /mcp/ path itself
    ],
    lifespan=mcp_asgi.lifespan,  # CRITICAL: Use mcp_asgi's lifespan
)

if __name__ == "__main__":
    mcp.run()