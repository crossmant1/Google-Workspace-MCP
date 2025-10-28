from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from dotenv import load_dotenv
import os
import requests
import io
import traceback
import asyncio
from typing import Dict, Optional
import secrets

load_dotenv()

# Environment variables
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents"
]

# Multi-user token storage: {user_id: {token_data, email}}
user_tokens: Dict[str, dict] = {}

# Session management: {session_token: user_id}
sessions: Dict[str, str] = {}

# Create MCP instance
mcp = FastMCP("Google Drive MCP")

def get_user_token(user_id: str) -> Optional[dict]:
    """Get token for a specific user"""
    user_data = user_tokens.get(user_id)
    if user_data:
        return user_data.get("token")
    return None

def get_user_from_session(session_token: str) -> Optional[str]:
    """Get user_id from session token"""
    return sessions.get(session_token)

# --- MCP Tools ---
@mcp.tool()
async def list_drive_files(user_id: str, max_results: int = 20) -> dict:
    """List files from Google Drive
    
    Args:
        user_id: The user identifier (session token or user ID)
        max_results: Maximum number of files to return (default: 20, max: 100)
    """
    # Try as session token first, then as user_id
    actual_user_id = get_user_from_session(user_id) or user_id
    stored_token = get_user_token(actual_user_id)
    
    if not stored_token:
        return {"error": f"User {user_id} not authenticated. Please authenticate first at /auth?user_id={user_id}"}

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        max_results = min(max_results, 100)
        
        creds = Credentials(
            token=stored_token.get("access_token"),
            refresh_token=stored_token.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            scopes=SCOPES,
        )

        service = build("drive", "v3", credentials=creds)
        res = service.files().list(
            pageSize=max_results, 
            fields="files(id,name,mimeType,modifiedTime,size)"
        ).execute()
        
        files = res.get("files", [])
        return {
            "success": True,
            "user_id": actual_user_id,
            "count": len(files),
            "files": files
        }
    except Exception as e:
        return {"error": str(e), "user_id": actual_user_id, "traceback": traceback.format_exc()}

async def _read_file_content_helper(user_id: str, file_id: str) -> dict:
    """Helper function to read file content - used by multiple tools"""
    actual_user_id = get_user_from_session(user_id) or user_id
    stored_token = get_user_token(actual_user_id)
    
    if not stored_token:
        return {"error": f"User {user_id} not authenticated. Please authenticate first at /auth?user_id={user_id}"}

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload

        creds = Credentials(
            token=stored_token.get("access_token"),
            refresh_token=stored_token.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            scopes=SCOPES,
        )

        service = build("drive", "v3", credentials=creds)
        
        # Get file metadata
        file_metadata = service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size,modifiedTime,webViewLink"
        ).execute()
        
        mime_type = file_metadata.get("mimeType", "")
        
        # Handle Google Workspace files (Docs, Sheets, Slides)
        if mime_type.startswith("application/vnd.google-apps"):
            export_formats = {
                "application/vnd.google-apps.document": "text/plain",
                "application/vnd.google-apps.spreadsheet": "text/csv",
                "application/vnd.google-apps.presentation": "text/plain",
            }
            
            if mime_type in export_formats:
                request = service.files().export_media(
                    fileId=file_id,
                    mimeType=export_formats[mime_type]
                )
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                
                done = False
                while not done:
                    status, done = downloader.next_chunk()
                
                content = fh.getvalue().decode("utf-8", errors="replace")
                return {
                    "success": True,
                    "user_id": actual_user_id,
                    "file_id": file_id,
                    "name": file_metadata["name"],
                    "mimeType": mime_type,
                    "exported_as": export_formats[mime_type],
                    "size": len(content),
                    "content": content
                }
            else:
                return {
                    "success": False,
                    "error": f"Google Workspace file type '{mime_type}' cannot be exported as text",
                    "user_id": actual_user_id,
                    "file_id": file_id,
                    "name": file_metadata["name"],
                    "webViewLink": file_metadata.get("webViewLink")
                }
        
        # Handle regular files
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        
        done = False
        while not done:
            status, done = downloader.next_chunk()
        
        content_bytes = fh.getvalue()
        
        # Try to decode as text for common text formats
        text_mime_types = [
            "text/", "application/json", "application/xml",
            "application/javascript", "application/x-python"
        ]
        
        if any(mime_type.startswith(t) for t in text_mime_types):
            try:
                content = content_bytes.decode("utf-8")
                return {
                    "success": True,
                    "user_id": actual_user_id,
                    "file_id": file_id,
                    "name": file_metadata["name"],
                    "mimeType": mime_type,
                    "size": len(content_bytes),
                    "content": content
                }
            except UnicodeDecodeError:
                pass
        
        # For binary files, return metadata only
        return {
            "success": True,
            "user_id": actual_user_id,
            "file_id": file_id,
            "name": file_metadata["name"],
            "mimeType": mime_type,
            "size": file_metadata.get("size"),
            "content": None,
            "message": "Binary file - content not displayed. Use webViewLink to access.",
            "webViewLink": file_metadata.get("webViewLink")
        }
        
    except Exception as e:
        return {"error": str(e), "user_id": actual_user_id, "file_id": file_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def search_drive_files(user_id: str, query: str, max_results: int = 10) -> dict:
    """Search for files in Google Drive by name
    
    Args:
        user_id: The user identifier (session token or user ID)
        query: Search query (file name to search for)
        max_results: Maximum number of results to return (default: 10)
    """
    actual_user_id = get_user_from_session(user_id) or user_id
    stored_token = get_user_token(actual_user_id)
    
    if not stored_token:
        return {"error": f"User {user_id} not authenticated. Please authenticate first at /auth?user_id={user_id}"}

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(
            token=stored_token.get("access_token"),
            refresh_token=stored_token.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            scopes=SCOPES,
        )

        service = build("drive", "v3", credentials=creds)
        safe_query = query.replace("'", "\\'")
        res = service.files().list(
            q=f"name contains '{safe_query}'",
            pageSize=min(max_results, 100),
            fields="files(id,name,mimeType,modifiedTime,size)"
        ).execute()
        
        files = res.get("files", [])
        return {
            "success": True,
            "user_id": actual_user_id,
            "query": query,
            "count": len(files),
            "files": files
        }
    except Exception as e:
        return {"error": str(e), "user_id": actual_user_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def read_file_by_name(user_id: str, file_name: str) -> dict:
    """Read the contents of a file from Google Drive by searching for its name
    
    Args:
        user_id: The user identifier (session token or user ID)
        file_name: The name of the file to search for and read
    
    Returns:
        Dictionary containing file metadata and content. If multiple files match, reads the first one.
    """
    actual_user_id = get_user_from_session(user_id) or user_id
    stored_token = get_user_token(actual_user_id)
    
    if not stored_token:
        return {"error": f"User {user_id} not authenticated. Please authenticate first at /auth?user_id={user_id}"}

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(
            token=stored_token.get("access_token"),
            refresh_token=stored_token.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            scopes=SCOPES,
        )

        service = build("drive", "v3", credentials=creds)
        safe_query = file_name.replace("'", "\\'")
        
        # Search for the file
        res = service.files().list(
            q=f"name contains '{safe_query}'",
            pageSize=10,
            fields="files(id,name,mimeType)"
        ).execute()
        
        files = res.get("files", [])
        
        if not files:
            return {
                "success": False,
                "error": f"No files found matching '{file_name}'",
                "user_id": actual_user_id,
                "searched_for": file_name
            }
        
        # Use the first matching file
        file_id = files[0]["id"]
        
        if len(files) > 1:
            match_info = {
                "note": f"Found {len(files)} matching files, reading the first one: '{files[0]['name']}'",
                "other_matches": [{"id": f["id"], "name": f["name"]} for f in files[1:]]
            }
        else:
            match_info = {}
        
        # Now read the file content using the helper function
        result = await _read_file_content_helper(user_id, file_id)
        result.update(match_info)
        return result
        
    except Exception as e:
        return {"error": str(e), "user_id": actual_user_id, "searched_for": file_name, "traceback": traceback.format_exc()}

@mcp.tool()
async def read_file_content(user_id: str, file_id: str) -> dict:
    """Read the contents of a specific file from Google Drive using its file ID
    
    Args:
        user_id: The user identifier (session token or user ID)
        file_id: The Google Drive file ID (not the file name) to read
    
    Returns:
        Dictionary containing file metadata and content (for text files) or download info (for binary files)
    """
    return await _read_file_content_helper(user_id, file_id)

@mcp.tool()
async def update_document_content(user_id: str, file_id: str, new_content: str) -> dict:
    """Update the contents of a Google Docs document
    
    Args:
        user_id: The user identifier (session token or user ID)
        file_id: The Google Drive file ID of the document to update
        new_content: The new text content to write to the document (replaces all existing content)
    
    Returns:
        Dictionary with success status and updated file information
    """
    actual_user_id = get_user_from_session(user_id) or user_id
    stored_token = get_user_token(actual_user_id)
    
    if not stored_token:
        return {"error": f"User {user_id} not authenticated. Please authenticate first at /auth?user_id={user_id}"}

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError

        print(f"\n=== UPDATE DOCUMENT DEBUG ===")
        print(f"User ID: {actual_user_id}")
        print(f"File ID: {file_id}")
        print(f"Content length: {len(new_content)} chars")
        print(f"Token present: {stored_token is not None}")
        print(f"Scopes configured: {SCOPES}")

        creds = Credentials(
            token=stored_token.get("access_token"),
            refresh_token=stored_token.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            scopes=SCOPES,
        )

        print(f"Credentials created, valid: {creds.valid}")
        print(f"Token: {creds.token[:20]}..." if creds.token else "No token")

        # Get file metadata to check type
        drive_service = build("drive", "v3", credentials=creds)
        print("Drive service built successfully")
        
        file_metadata = drive_service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,capabilities"
        ).execute()
        
        print(f"File metadata retrieved: {file_metadata.get('name')}")
        print(f"MIME type: {file_metadata.get('mimeType')}")
        print(f"Capabilities: {file_metadata.get('capabilities')}")
        
        mime_type = file_metadata.get("mimeType", "")
        
        # Check if we can edit
        capabilities = file_metadata.get("capabilities", {})
        can_edit = capabilities.get("canEdit", False)
        print(f"Can edit: {can_edit}")
        
        if not can_edit:
            return {
                "success": False,
                "error": "You do not have edit permissions for this document",
                "user_id": actual_user_id,
                "file_id": file_id,
                "name": file_metadata["name"],
                "capabilities": capabilities
            }
        
        # Handle Google Docs
        if mime_type == "application/vnd.google-apps.document":
            print("Building Docs service...")
            docs_service = build("docs", "v1", credentials=creds)
            print("Docs service built successfully")
            
            # Get the current document to find the end index
            print("Fetching document structure...")
            doc = docs_service.documents().get(documentId=file_id).execute()
            print(f"Document retrieved: {doc.get('title')}")
            
            content_length = doc.get('body').get('content')[-1].get('endIndex') - 1
            print(f"Current content length: {content_length}")
            
            # Delete all existing content and insert new content
            requests_payload = [
                {
                    'deleteContentRange': {
                        'range': {
                            'startIndex': 1,
                            'endIndex': content_length
                        }
                    }
                },
                {
                    'insertText': {
                        'location': {
                            'index': 1
                        },
                        'text': new_content
                    }
                }
            ]
            
            print(f"Sending batchUpdate request...")
            
            result = docs_service.documents().batchUpdate(
                documentId=file_id,
                body={'requests': requests_payload}
            ).execute()
            
            print(f"BatchUpdate result: {result}")
            print("=== UPDATE COMPLETE ===\n")
            
            return {
                "success": True,
                "user_id": actual_user_id,
                "file_id": file_id,
                "name": file_metadata["name"],
                "message": "Document updated successfully",
                "content_length": len(new_content),
                "api_response": result
            }
        else:
            return {
                "success": False,
                "error": f"File type '{mime_type}' is not a Google Doc. Only Google Docs can be edited with this tool.",
                "user_id": actual_user_id,
                "file_id": file_id,
                "name": file_metadata["name"]
            }
        
    except HttpError as e:
        error_details = {
            "error_type": "HttpError",
            "status_code": e.resp.status,
            "reason": e.resp.reason,
            "error_details": e.error_details if hasattr(e, 'error_details') else str(e),
            "user_id": actual_user_id,
            "file_id": file_id,
            "traceback": traceback.format_exc()
        }
        print(f"HTTP Error occurred: {error_details}")
        return error_details
    except Exception as e:
        error_details = {
            "error_type": type(e).__name__,
            "error": str(e),
            "user_id": actual_user_id,
            "file_id": file_id,
            "traceback": traceback.format_exc()
        }
        print(f"Exception occurred: {error_details}")
        return error_details

@mcp.tool()
async def update_document_by_name(user_id: str, file_name: str, new_content: str) -> dict:
    """Update the contents of a Google Docs document by searching for its name
    
    Args:
        user_id: The user identifier (session token or user ID)
        file_name: The name of the document to search for and update
        new_content: The new text content to write to the document (replaces all existing content)
    
    Returns:
        Dictionary with success status and updated file information
    """
    actual_user_id = get_user_from_session(user_id) or user_id
    stored_token = get_user_token(actual_user_id)
    
    if not stored_token:
        return {"error": f"User {user_id} not authenticated. Please authenticate first at /auth?user_id={user_id}"}

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError

        creds = Credentials(
            token=stored_token.get("access_token"),
            refresh_token=stored_token.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            scopes=SCOPES,
        )

        service = build("drive", "v3", credentials=creds)
        safe_query = file_name.replace("'", "\\'")
        
        # Search for Google Docs with matching name
        res = service.files().list(
            q=f"name contains '{safe_query}' and mimeType='application/vnd.google-apps.document'",
            pageSize=10,
            fields="files(id,name,mimeType)"
        ).execute()
        
        files = res.get("files", [])
        
        if not files:
            return {
                "success": False,
                "error": f"No Google Docs found matching '{file_name}'",
                "user_id": actual_user_id,
                "searched_for": file_name
            }
        
        # Use the first matching file
        file_id = files[0]["id"]
        
        print(f"Found file: {files[0]['name']} (ID: {file_id})")
        
        # Build the result with match info
        result = {
            "user_id": actual_user_id,
            "searched_for": file_name,
            "matched_file": files[0]["name"]
        }
        
        if len(files) > 1:
            result["note"] = f"Found {len(files)} matching documents, updating the first one: '{files[0]['name']}'"
            result["other_matches"] = [{"id": f["id"], "name": f["name"]} for f in files[1:]]
        
        # Update the document by directly implementing the logic here
        # (Can't call another @mcp.tool() from within a tool)
        print(f"\n=== UPDATE DOCUMENT DEBUG (from update_by_name) ===")
        print(f"File ID: {file_id}")
        print(f"Content length: {len(new_content)} chars")
        
        # Get file metadata to check type and permissions
        file_metadata = service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,capabilities"
        ).execute()
        
        print(f"File metadata retrieved: {file_metadata.get('name')}")
        print(f"MIME type: {file_metadata.get('mimeType')}")
        
        mime_type = file_metadata.get("mimeType", "")
        capabilities = file_metadata.get("capabilities", {})
        can_edit = capabilities.get("canEdit", False)
        
        print(f"Can edit: {can_edit}")
        
        if not can_edit:
            result.update({
                "success": False,
                "error": "You do not have edit permissions for this document",
                "file_id": file_id,
                "name": file_metadata["name"],
                "capabilities": capabilities
            })
            return result
        
        if mime_type != "application/vnd.google-apps.document":
            result.update({
                "success": False,
                "error": f"File type '{mime_type}' is not a Google Doc.",
                "file_id": file_id,
                "name": file_metadata["name"]
            })
            return result
        
        # Build Docs service and update
        docs_service = build("docs", "v1", credentials=creds)
        doc = docs_service.documents().get(documentId=file_id).execute()
        content_length = doc.get('body').get('content')[-1].get('endIndex') - 1
        
        print(f"Current content length: {content_length}")
        
        requests_payload = [
            {
                'deleteContentRange': {
                    'range': {
                        'startIndex': 1,
                        'endIndex': content_length
                    }
                }
            },
            {
                'insertText': {
                    'location': {
                        'index': 1
                    },
                    'text': new_content
                }
            }
        ]
        
        print(f"Sending batchUpdate request...")
        api_result = docs_service.documents().batchUpdate(
            documentId=file_id,
            body={'requests': requests_payload}
        ).execute()
        
        print(f"BatchUpdate successful!")
        print("=== UPDATE COMPLETE ===\n")
        
        result.update({
            "success": True,
            "file_id": file_id,
            "name": file_metadata["name"],
            "message": "Document updated successfully",
            "content_length": len(new_content),
            "api_response": api_result
        })
        
        return result
        
    except Exception as e:
        return {
            "error": str(e),
            "user_id": actual_user_id,
            "searched_for": file_name,
            "traceback": traceback.format_exc()
        }

@mcp.tool()
async def get_auth_status(user_id: str) -> dict:
    """Check if a user is authenticated with Google Drive and get their info
    
    Args:
        user_id: The user identifier (session token or user ID)
    """
    actual_user_id = get_user_from_session(user_id) or user_id
    user_data = user_tokens.get(actual_user_id)
    
    status = {
        "authenticated": user_data is not None,
        "user_id": actual_user_id,
        "scopes": SCOPES,
        "message": "Connected to Google Drive" if user_data else f"Not authenticated. Please visit /auth?user_id={user_id} to connect."
    }
    
    if user_data:
        status["email"] = user_data.get("email")
        status["display_name"] = user_data.get("display_name")
        
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            
            stored_token = user_data.get("token")
            creds = Credentials(
                token=stored_token.get("access_token"),
                refresh_token=stored_token.get("refresh_token"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                scopes=SCOPES,
            )
            
            # Get info about the authenticated user
            drive_service = build("drive", "v3", credentials=creds)
            about = drive_service.about().get(fields="user").execute()
            
            status["authenticated_user"] = {
                "email": about.get("user", {}).get("emailAddress"),
                "display_name": about.get("user", {}).get("displayName")
            }
        except Exception as e:
            status["error_getting_user_info"] = str(e)
    
    return status

@mcp.tool()
async def list_authenticated_users() -> dict:
    """List all authenticated users (admin function)"""
    users = []
    for user_id, data in user_tokens.items():
        users.append({
            "user_id": user_id,
            "email": data.get("email"),
            "display_name": data.get("display_name")
        })
    
    return {
        "success": True,
        "total_users": len(users),
        "users": users
    }

# Create the MCP ASGI app - this creates a Starlette app with the MCP endpoint at /mcp/
mcp_asgi = mcp.http_app(path='/mcp')

# Create a Starlette app to combine everything
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse as StarletteJSONResponse, RedirectResponse

# Define OAuth routes
async def start_auth(request):
    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        return StarletteJSONResponse({"error": "OAuth environment variables missing"}, status_code=500)

    # Get or create user_id
    user_id = request.query_params.get("user_id")
    if not user_id:
        user_id = secrets.token_urlsafe(16)
    
    # Store user_id in state parameter for OAuth callback
    from urllib.parse import urlencode
    params = urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": user_id,  # Pass user_id through OAuth flow
    })
    
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{params}"
    return StarletteJSONResponse({
        "auth_url": auth_url,
        "user_id": user_id,
        "message": "Visit the auth_url to authenticate. Save your user_id to access your files."
    })

async def oauth_callback(request):
    global user_tokens, sessions
    
    code = request.query_params.get("code")
    state = request.query_params.get("state")  # This is our user_id
    
    if not code:
        return StarletteJSONResponse({"error": "Missing code"}, status_code=400)
    
    if not state:
        return StarletteJSONResponse({"error": "Missing state (user_id)"}, status_code=400)

    token_resp = requests.post("https://oauth2.googleapis.com/token", data={
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    })

    if token_resp.status_code != 200:
        return StarletteJSONResponse({"error": f"Token exchange failed: {token_resp.text}"}, status_code=500)

    token_data = token_resp.json()
    
    # Get user info
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        
        creds = Credentials(
            token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            scopes=SCOPES,
        )
        
        drive_service = build("drive", "v3", credentials=creds)
        about = drive_service.about().get(fields="user").execute()
        user_email = about.get("user", {}).get("emailAddress")
        display_name = about.get("user", {}).get("displayName")
        
    except Exception as e:
        user_email = "unknown@example.com"
        display_name = "Unknown User"
        print(f"Error getting user info: {e}")
    
    # Store token with user info
    user_id = state  # user_id from state parameter
    user_tokens[user_id] = {
        "token": token_data,
        "email": user_email,
        "display_name": display_name
    }
    
    # Create a session token for easier access
    session_token = secrets.token_urlsafe(32)
    sessions[session_token] = user_id
    
    print(f"\n=== NEW USER AUTHENTICATED ===")
    print(f"User ID: {user_id}")
    print(f"Email: {user_email}")
    print(f"Session Token: {session_token}")
    print(f"Scope in token: {token_data.get('scope')}")
    print("===============================\n")
    
    return StarletteJSONResponse({
        "status": "connected",
        "user_id": user_id,
        "session_token": session_token,
        "email": user_email,
        "display_name": display_name,
        "scopes_granted": token_data.get('scope', '').split(),
        "message": "Save your user_id or session_token to access your Google Drive files"
    })

async def logout(request):
    """Logout a user by removing their token"""
    user_id = request.query_params.get("user_id")
    
    if not user_id:
        return StarletteJSONResponse({"error": "user_id parameter required"}, status_code=400)
    
    # Try to find and remove user
    actual_user_id = get_user_from_session(user_id) or user_id
    
    if actual_user_id in user_tokens:
        user_data = user_tokens.pop(actual_user_id)
        # Remove associated sessions
        sessions_to_remove = [s for s, uid in sessions.items() if uid == actual_user_id]
        for s in sessions_to_remove:
            sessions.pop(s, None)
        
        return StarletteJSONResponse({
            "status": "logged_out",
            "user_id": actual_user_id,
            "email": user_data.get("email")
        })
    else:
        return StarletteJSONResponse({
            "error": "User not found or not authenticated",
            "user_id": user_id
        }, status_code=404)

async def health(request):
    return StarletteJSONResponse({
        "status": "ok",
        "total_authenticated_users": len(user_tokens),
        "scopes_configured": SCOPES
    })

async def root(request):
    return StarletteJSONResponse({
        "service": "Google Drive MCP Server (Multi-User)",
        "endpoints": {
            "auth": "/auth?user_id=<optional> - Start OAuth flow (generates user_id if not provided)",
            "callback": "/oauth2callback - OAuth callback (automatic)",
            "logout": "/logout?user_id=<required> - Logout a user",
            "health": "/health - Health check",
            "mcp": "/mcp/ - MCP protocol endpoint (POST only)"
        },
        "authenticated_users": len(user_tokens),
        "usage": {
            "step_1": "Visit /auth to get a user_id and auth_url",
            "step_2": "Visit the auth_url in a browser to authenticate",
            "step_3": "You'll be redirected back with your user_id and session_token",
            "step_4": "Use your user_id or session_token in all MCP tool calls"
        }
    })

# Create the main app using Starlette and mount everything
app = Starlette(
    routes=[
        Route("/", root),
        Route("/auth", start_auth),
        Route("/oauth2callback", oauth_callback),
        Route("/logout", logout),
        Route("/health", health),
        Mount("/", mcp_asgi),  # Mount MCP at root - it will handle /mcp/ path
    ],
    lifespan=mcp_asgi.lifespan,  # CRITICAL: Pass MCP's lifespan
)

# Export for uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
