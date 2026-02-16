import sys
import os
print(f"Python version: {sys.version}")
print(f"Current directory: {os.getcwd()}")
print(f"sys.path: {sys.path}")
print(f"Directory contents: {os.listdir('.')}")
if os.path.exists('mcp_tools'):
    print(f"mcp_tools contents: {os.listdir('mcp_tools')}")

import urllib.parse
import secrets
import requests
import traceback
from datetime import datetime, timedelta

from contextlib import asynccontextmanager
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount  
from starlette.responses import JSONResponse, HTMLResponse
from starlette.requests import Request

import mcpserver.config as config
import mcpserver.database as database
import mcpserver.auth as auth

from mcpserver.mcp_tools import tools_drive, tools_gmail, tools_calendar, tools_tasks, tools_docs, tools_slides, tools_sheets

from mcpserver.config import CLIENT_ID, CLIENT_SECRET, SCOPES, REDIRECT_URI
from mcpserver.database import (
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
mcp = FastMCP("Google Drive, Gmail, Calendar & Tasks MCP", host="127.0.0.1", port=8000)

# --- Register Drive Tools ---
mcp.tool()(tools_drive.list_drive_files)
mcp.tool()(tools_drive.search_drive_files)
mcp.tool()(tools_drive.read_file_by_name)
mcp.tool()(tools_drive.read_file_content)

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

# --- Register Docs Tools ---
mcp.tool()(tools_docs.update_document_content)
mcp.tool()(tools_docs.update_document_by_name)
mcp.tool()(tools_docs.create_document)
mcp.tool()(tools_docs.delete_document)
mcp.tool()(tools_docs.delete_document_by_name)
mcp.tool()(tools_docs.append_to_document)
mcp.tool()(tools_docs.append_to_document_by_name)
mcp.tool()(tools_docs.insert_text_at_position)
mcp.tool()(tools_docs.rename_document)

# --- Register Slides Tools ---
mcp.tool()(tools_slides.read_presentation)
mcp.tool()(tools_slides.list_slides)
mcp.tool()(tools_slides.delete_presentation)
mcp.tool()(tools_slides.rename_presentation)
mcp.tool()(tools_slides.duplicate_presentation)
mcp.tool()(tools_slides.get_presentation_metadata)
mcp.tool()(tools_slides.export_presentation_as_pdf)

#--- Register Sheets Tools ---
mcp.tool()(tools_sheets.read_spreadsheet)
mcp.tool()(tools_sheets.read_range)
mcp.tool()(tools_sheets.get_sheet_names)
mcp.tool()(tools_sheets.get_spreadsheet_metadata)
mcp.tool()(tools_sheets.update_cell)
mcp.tool()(tools_sheets.update_range)
mcp.tool()(tools_sheets.append_row)
mcp.tool()(tools_sheets.clear_range)
mcp.tool()(tools_sheets.create_sheet)
mcp.tool()(tools_sheets.delete_sheet)
mcp.tool()(tools_sheets.rename_sheet)
mcp.tool()(tools_sheets.duplicate_sheet)
mcp.tool()(tools_sheets.create_spreadsheet)
mcp.tool()(tools_sheets.delete_spreadsheet)
mcp.tool()(tools_sheets.rename_spreadsheet)
mcp.tool()(tools_sheets.duplicate_spreadsheet)
mcp.tool()(tools_sheets.find_in_sheet)

# --- Compound Tools ---
mcp.tool()(tools_tasks.create_task_from_email)
mcp.tool()(tools_tasks.add_emails_to_tasks)
mcp.tool()(tools_tasks.create_task_from_email_search)
mcp.tool()(tools_tasks.get_auth_status)

# --- Register Auth Tools ---
mcp.tool()(auth.check_google_auth)

app= mcp.streamable_http_app()

@app.route("/auth", methods=["GET"])
async def start_auth(request: StarletteRequest):
    """Start the Google OAuth2 flow with email parameter"""
    from google_auth_oauthlib.flow import Flow
    
    # Get email from query parameter
    email = request.query_params.get("email")
    
    if not email:
        return StarletteJSONResponse(
            {"error": "Email parameter is required. Use: /auth?email=user@example.com"}, 
            status_code=400
        )
    
    # Sanitize email
    email = email.lower().strip()
    
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
    
    # Include email in state parameter
    auth_url, state = flow.authorization_url(
        access_type="offline", 
        prompt="consent",
        state=email
    )
    
    log_action("N/A", "start_auth", True, "api", f"Auth started for: {email}", request.client.host)
    
    return StarletteJSONResponse({
        "auth_url": auth_url,
        "email": email,
        "message": "Visit auth_url to complete Google authentication"
    })

@app.route("/start-auth", methods=["GET"])    
async def auth_page(request: StarletteRequest):
    """HTML page to initiate OAuth flow - requires email input"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Google OAuth Authentication</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
            input { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 4px; font-size: 16px; }
            button { background-color: #4285f4; color: white; padding: 12px 24px; border: none; border-radius: 4px; font-size: 16px; cursor: pointer; width: 100%; }
            button:hover { background-color: #357ae8; }
            button:disabled { background-color: #ccc; cursor: not-allowed; }
            .info { background-color: #f0f0f0; padding: 15px; border-radius: 4px; margin-top: 20px; }
            .error { color: red; margin-top: 10px; }
        </style>
    </head>
    <body>
        <h1>Google OAuth Authentication</h1>
        <p>Enter your email address to authenticate with Google:</p>
        
        <input type="email" id="emailInput" placeholder="your.email@example.com" autocomplete="email" />
        <button onclick="startAuth()" id="authButton">Authenticate with Google</button>
        <div id="errorMsg" class="error"></div>
        
        <div class="info">
            <h3>What happens next:</h3>
            <ol>
                <li>Enter your email address above</li>
                <li>Click "Authenticate with Google"</li>
                <li>You'll be redirected to Google to sign in</li>
                <li>Grant permissions to the application</li>
                <li>You'll be redirected back - all done!</li>
                <li>Use your email in MCP tools to access Google services</li>
            </ol>
        </div>
        
        <script>
            const emailInput = document.getElementById('emailInput');
            const authButton = document.getElementById('authButton');
            const errorMsg = document.getElementById('errorMsg');
            
            const urlParams = new URLSearchParams(window.location.search);
            const emailParam = urlParams.get('email');
            if (emailParam) { emailInput.value = emailParam; }
            
            async function startAuth() {
                const email = emailInput.value.trim();
                
                if (!email) { errorMsg.textContent = 'Please enter your email address'; return; }
                
                const emailRegex = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/;
                if (!emailRegex.test(email)) { errorMsg.textContent = 'Please enter a valid email address'; return; }
                
                errorMsg.textContent = '';
                authButton.disabled = true;
                authButton.textContent = 'Redirecting...';
                
                try {
                    const response = await fetch('/auth?email=' + encodeURIComponent(email));
                    const data = await response.json();
                    
                    if (data.error) {
                        errorMsg.textContent = 'Error: ' + data.error;
                        authButton.disabled = false;
                        authButton.textContent = 'Authenticate with Google';
                        return;
                    }
                    
                    window.location.href = data.auth_url;
                } catch (error) {
                    errorMsg.textContent = 'Error starting authentication: ' + error;
                    authButton.disabled = false;
                    authButton.textContent = 'Authenticate with Google';
                }
            }
            
            emailInput.addEventListener('keypress', function(e) { if (e.key === 'Enter') { startAuth(); } });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.route("/oauth2callback", methods=["GET"])
async def oauth_callback(request: StarletteRequest):
    """Handle the OAuth2 callback from Google - NO API KEY RETURNED"""
    from google_auth_oauthlib.flow import Flow
    import jwt

    code = request.query_params.get("code")
    state = request.query_params.get("state")  # This contains the email
    
    if not code:
        return StarletteJSONResponse({"error": "No code found in callback"}, status_code=400)
    
    if not state:
        return StarletteJSONResponse({"error": "No state (email) found in callback"}, status_code=400)
    
    # The state parameter contains the email
    email = state.lower().strip()
    
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

        id_token = creds.id_token
        if not id_token:
            return StarletteJSONResponse(
                {"error": "No ID token received from Google"}, 
                status_code=500
            )
        
        user_info = jwt.decode(id_token, options={"verify_signature": False})
        
        token_email = user_info.get("email")
        display_name = user_info.get("name", email)
        
        if not token_email:
            return StarletteJSONResponse(
                {"error": "Could not retrieve email from ID token"}, 
                status_code=500
            )
        
        # Verify email match
        if token_email.lower() != email:
            return StarletteJSONResponse(
                {"error": f"Email mismatch: expected {email}, got {token_email}"}, 
                status_code=400
            )

        user = get_user_by_email(email)
        if user:
            user_id = user["user_id"]
            user_existed = True
        else:
            user_id = create_user(email, display_name)  # NO API KEY
            user_existed = False
        
        store_tokens(user_id, token_data, SCOPES)
        update_last_login(user_id)
        
        session_token = create_session(
            user_id, 
            request.client.host, 
            request.headers.get("User-Agent", "Unknown")
        )
        
        log_action(user_id, "oauth_callback", True, "auth", f"User {email} authenticated", request.client.host)

        return StarletteJSONResponse({
            "success": True,
            "message": "Authentication successful! You can now use your email with the MCP tools.",
            "email": email,
            "user_id": user_id,
            "display_name": display_name,
            "session_token": session_token,
            "user_existed": user_existed,
            "next_step": "Use your email in MCP tool calls to access Google services"
        })

    except Exception as e:
        traceback.print_exc()
        log_action("N/A", "oauth_callback", False, "auth", str(e), request.client.host)
        return StarletteJSONResponse({
            "error": str(e), 
            "traceback": traceback.format_exc()
        }, status_code=500)

@app.route("/check-auth", methods=["GET"])
async def check_auth_status(request: StarletteRequest):
    """Check if a user's email is authenticated in the database"""
    try:
        email = request.query_params.get("email")
        
        if not email:
            return StarletteJSONResponse(
                {"error": "Email parameter is required"}, 
                status_code=400
            )
        
        email = email.lower().strip()
        
        user = get_user_by_email(email)
        
        if not user:
            log_action("N/A", "check_auth_status", False, "api", f"User not found: {email}", request.client.host)
            return StarletteJSONResponse({
                "authenticated": False,
                "email": email,
                "message": "User not found - need to complete OAuth"
            })
        
        user_id = user["user_id"]
        token_data = database.get_user_tokens(user_id)
        
        if not token_data:
            log_action(user_id, "check_auth_status", False, "api", f"No tokens for: {email}", request.client.host)
            return StarletteJSONResponse({
                "authenticated": False,
                "email": email,
                "user_id": user_id,
                "message": "User exists but not authenticated with Google - need OAuth"
            })
        
        token_expiry = token_data.get("token_expiry")
        expiry_str = token_expiry.isoformat() if token_expiry else None
        
        log_action(user_id, "check_auth_status", True, "api", f"Auth check for: {email}", request.client.host)
        return StarletteJSONResponse({
            "authenticated": True,
            "email": email,
            "user_id": user_id,
            "display_name": user.get("display_name"),
            "is_active": user.get("is_active"),
            "scopes": token_data.get("scopes", []),
            "token_expiry": expiry_str
        })
        
    except Exception as e:
        traceback.print_exc()
        log_action("N/A", "check_auth_status", False, "api", str(e), request.client.host)
        return StarletteJSONResponse(
            {"error": str(e), "traceback": traceback.format_exc()}, 
            status_code=500
        )
@app.route("/health", methods=["GET"])
async def health(request: StarletteRequest):
    """Health check endpoint, including DB connection"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(buffered=True)
        cursor.execute("SELECT 1")
        cursor.fetchall()
        cursor.close()
        return_connection(conn)
        db_status = "connected"
    except Exception as e:
        db_status = f"disconnected: {e}"

    return StarletteJSONResponse({
        "status": "ok",
        "database": db_status
    })

@app.route("/", methods=["GET"])
async def root(request: StarletteRequest):
    """Root endpoint with updated documentation"""
    return StarletteJSONResponse({
        "service": "Google Drive, Gmail, Calendar & Tasks MCP Server",
        "database_backend": "Azure SQL (pyodbc)",
        "authentication": "Email-based (no API keys required)",
        "endpoints": {
            "auth": "/auth?email=user@example.com - Start OAuth flow for an email",
            "start_auth_page": "/start-auth - HTML page to start OAuth (with email input)",
            "callback": "/oauth2callback - OAuth callback (handles redirect)",
            "check_auth": "/check-auth?email=user@example.com - Check if email is authenticated",
            "health": "/health - Health check (includes DB)",
            "mcp": "/mcp/ - MCP protocol endpoint (POST only)"
        },
        "available_tools": [
            "Auth: check_google_auth - Check authentication status before using other tools",
            "Drive: list_drive_files, search_drive_files, read_file_by_name, read_file_content, update_document_content, update_document_by_name",
            "Gmail: list_emails, read_email, send_email, search_emails, mark_email_as_read, mark_email_as_unread",
            "Calendar: list_calendar_events, create_calendar_event, update_calendar_event, delete_calendar_event, search_calendar_events",
            "Tasks: list_task_lists, list_tasks, create_task, create_task_from_email, add_emails_to_tasks, create_task_from_email_search, update_task, complete_task, delete_task"
        ],
        "usage": {
            "step_1": "AI Agent calls check_google_auth with user's email",
            "step_2a": "If authenticated=true, agent can use all tools with email",
            "step_2b": "If authenticated=false, user visits auth_url to complete OAuth",
            "step_3": "After OAuth, agent retries and tools work immediately",
            "note": "No API keys needed - just use email in all tool calls"
        }
    })

    
#app = Starlette(
#    routes=[
#        Route("/", root),
#        Route("/start-auth", auth_page),
#        Route("/auth", start_auth),
#        Route("/oauth2callback", oauth_callback),
#        Route("/check-auth", check_auth_status, methods=["GET"]),  # NEW
#        Route("/health", health),
#    ],
#)

if __name__ == "__main__":
    mcp.run()