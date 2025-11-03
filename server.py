from fastapi import FastAPI, Request, HTTPException, Header
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
from datetime import datetime, timedelta
import pymssql
from cryptography.fernet import Fernet
import hashlib
import json

load_dotenv()

# Environment variables
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")

# Azure SQL Database connection
AZURE_SQL_SERVER = os.getenv("AZURE_SQL_SERVER")  # e.g., "myserver.database.windows.net"
AZURE_SQL_DATABASE = os.getenv("AZURE_SQL_DATABASE")  # e.g., "gdrive_mcp_db"
AZURE_SQL_USERNAME = os.getenv("AZURE_SQL_USERNAME")
AZURE_SQL_PASSWORD = os.getenv("AZURE_SQL_PASSWORD")

# Encryption key for tokens (store this securely in Azure Key Vault in production)
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")  # Generate with: Fernet.generate_key()
if not ENCRYPTION_KEY:
    print("WARNING: No ENCRYPTION_KEY found, generating temporary key (DO NOT USE IN PRODUCTION)")
    ENCRYPTION_KEY = Fernet.generate_key()
else:
    ENCRYPTION_KEY = ENCRYPTION_KEY.encode()

cipher_suite = Fernet(ENCRYPTION_KEY)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents"
]

# Database connection string
def get_db_connection():
    """Create a connection to Azure SQL Database using pymssql"""
    # Extract server name without port and domain suffix
    server = AZURE_SQL_SERVER
    if '.database.windows.net' in server:
        server = server.split('.database.windows.net')[0]
    
    try:
        conn = pymssql.connect(
            server=server + '.database.windows.net',
            user=AZURE_SQL_USERNAME,
            password=AZURE_SQL_PASSWORD,
            database=AZURE_SQL_DATABASE,
            port=1433,
            timeout=30,
            login_timeout=30,
            as_dict=False,
            tds_version='7.0'  # Specify TDS version for better Azure SQL compatibility
        )
        return conn
    except pymssql.Error as e:
        print(f"Database connection error: {e}")
        raise

# Database helper functions
def encrypt_token(token_data: dict) -> str:
    """Encrypt token data for storage"""
    json_data = json.dumps(token_data)
    encrypted = cipher_suite.encrypt(json_data.encode())
    return encrypted.decode()

def decrypt_token(encrypted_data: str) -> dict:
    """Decrypt token data from storage"""
    decrypted = cipher_suite.decrypt(encrypted_data.encode())
    return json.loads(decrypted.decode())

def hash_api_key(api_key: str) -> str:
    """Hash API key for storage"""
    return hashlib.sha256(api_key.encode()).hexdigest()

# Database operations
def create_user(email: str, display_name: str) -> str:
    """Create a new user and return user_id"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    user_id = secrets.token_urlsafe(16)
    api_key = secrets.token_urlsafe(32)
    api_key_hash = hash_api_key(api_key)
    
    cursor.execute("""
        INSERT INTO users (user_id, email, display_name, api_key_hash, created_at, last_login, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (user_id, email, display_name, api_key_hash, datetime.utcnow(), datetime.utcnow(), 1))
    
    conn.commit()
    conn.close()
    
    return user_id, api_key

def get_user_by_email(email: str) -> Optional[dict]:
    """Get user by email"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT user_id, email, display_name, is_active FROM users WHERE email = %s", (email,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "user_id": row[0],
            "email": row[1],
            "display_name": row[2],
            "is_active": row[3]
        }
    return None

def get_user_by_api_key(api_key: str) -> Optional[str]:
    """Get user_id by API key"""
    api_key_hash = hash_api_key(api_key)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT user_id FROM users WHERE api_key_hash = %s AND is_active = 1", (api_key_hash,))
    row = cursor.fetchone()
    conn.close()
    
    return row[0] if row else None

def store_tokens(user_id: str, token_data: dict, scopes: list):
    """Store or update tokens for a user"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    encrypted_access = encrypt_token({"token": token_data.get("access_token")})
    encrypted_refresh = encrypt_token({"token": token_data.get("refresh_token")})
    
    # Calculate expiry (usually 1 hour from now)
    expires_in = token_data.get("expires_in", 3600)
    token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
    
    scopes_str = " ".join(scopes)
    
    # Check if token already exists
    cursor.execute("SELECT user_id FROM tokens WHERE user_id = %s", (user_id,))
    exists = cursor.fetchone()
    
    if exists:
        cursor.execute("""
            UPDATE tokens
            SET access_token = %s, refresh_token = %s, token_expiry = %s, scopes = %s, updated_at = %s
            WHERE user_id = %s
        """, (encrypted_access, encrypted_refresh, token_expiry, scopes_str, datetime.utcnow(), user_id))
    else:
        cursor.execute("""
            INSERT INTO tokens (user_id, access_token, refresh_token, token_expiry, scopes, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, encrypted_access, encrypted_refresh, token_expiry, scopes_str, datetime.utcnow()))
    
    conn.commit()
    conn.close()

def get_user_tokens(user_id: str) -> Optional[dict]:
    """Get decrypted tokens for a user"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT access_token, refresh_token, token_expiry, scopes
        FROM tokens
        WHERE user_id = %s
    """, (user_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        access_token_data = decrypt_token(row[0])
        refresh_token_data = decrypt_token(row[1])
        
        return {
            "access_token": access_token_data.get("token"),
            "refresh_token": refresh_token_data.get("token"),
            "token_expiry": row[2],
            "scopes": row[3].split()
        }
    return None

def create_session(user_id: str, ip_address: str, user_agent: str) -> str:
    """Create a new session and return session token"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    session_token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(days=30)
    
    cursor.execute("""
        INSERT INTO sessions (session_token, user_id, created_at, expires_at, ip_address, user_agent)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (session_token, user_id, datetime.utcnow(), expires_at, ip_address, user_agent))
    
    conn.commit()
    conn.close()
    
    return session_token

def get_user_from_session(session_token: str) -> Optional[str]:
    """Get user_id from session token if valid"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT user_id FROM sessions
        WHERE session_token = %s AND expires_at > %s
    """, (session_token, datetime.utcnow()))
    
    row = cursor.fetchone()
    conn.close()
    
    return row[0] if row else None

def log_action(user_id: str, action: str, success: bool, ip_address: str, details: str = None):
    """Log an action to audit log"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO audit_logs (user_id, action, timestamp, success, ip_address, details)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (user_id, action, datetime.utcnow(), success, ip_address, details))
    
    conn.commit()
    conn.close()

def update_last_login(user_id: str):
    """Update user's last login timestamp"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("UPDATE users SET last_login = %s WHERE user_id = %s", (datetime.utcnow(), user_id))
    
    conn.commit()
    conn.close()

# Authentication helper
async def verify_api_key(api_key: Optional[str]) -> Optional[str]:
    """Verify API key and return user_id"""
    if not api_key:
        return None
    return get_user_by_api_key(api_key)

# Create MCP instance
mcp = FastMCP("Google Drive MCP")

# --- MCP Tools ---
@mcp.tool()
async def list_drive_files(api_key: str, max_results: int = 20) -> dict:
    """List files from Google Drive
    
    Args:
        api_key: User's API key for authentication
        max_results: Maximum number of files to return (default: 20, max: 100)
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}
    
    token_data = get_user_tokens(user_id)
    if not token_data:
        return {"error": "User not authenticated. Please authenticate first."}

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        max_results = min(max_results, 100)
        
        creds = Credentials(
            token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
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
        
        # Log the action
        log_action(user_id, "list_drive_files", True, "mcp_tool", f"Listed {len(files)} files")
        
        return {
            "success": True,
            "user_id": user_id,
            "count": len(files),
            "files": files
        }
    except Exception as e:
        log_action(user_id, "list_drive_files", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def search_drive_files(api_key: str, query: str, max_results: int = 10) -> dict:
    """Search for files in Google Drive by name
    
    Args:
        api_key: User's API key for authentication
        query: Search query (file name to search for)
        max_results: Maximum number of results to return (default: 10)
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}
    
    token_data = get_user_tokens(user_id)
    if not token_data:
        return {"error": "User not authenticated"}

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

        service = build("drive", "v3", credentials=creds)
        safe_query = query.replace("'", "\\'")
        res = service.files().list(
            q=f"name contains '{safe_query}'",
            pageSize=min(max_results, 100),
            fields="files(id,name,mimeType,modifiedTime,size)"
        ).execute()
        
        files = res.get("files", [])
        log_action(user_id, "search_drive_files", True, "mcp_tool", f"Query: {query}")
        
        return {
            "success": True,
            "user_id": user_id,
            "query": query,
            "count": len(files),
            "files": files
        }
    except Exception as e:
        log_action(user_id, "search_drive_files", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def read_file_content(api_key: str, file_id: str) -> dict:
    """Read the contents of a specific file from Google Drive
    
    Args:
        api_key: User's API key for authentication
        file_id: The Google Drive file ID to read
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}
    
    token_data = get_user_tokens(user_id)
    if not token_data:
        return {"error": "User not authenticated"}

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload

        creds = Credentials(
            token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            scopes=SCOPES,
        )

        service = build("drive", "v3", credentials=creds)
        
        file_metadata = service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size"
        ).execute()
        
        mime_type = file_metadata.get("mimeType", "")
        
        # Handle Google Docs
        if mime_type == "application/vnd.google-apps.document":
            request = service.files().export_media(
                fileId=file_id,
                mimeType="text/plain"
            )
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            
            done = False
            while not done:
                status, done = downloader.next_chunk()
            
            content = fh.getvalue().decode("utf-8", errors="replace")
            
            log_action(user_id, "read_file_content", True, "mcp_tool", f"File: {file_id}")
            
            return {
                "success": True,
                "user_id": user_id,
                "file_id": file_id,
                "name": file_metadata["name"],
                "mimeType": mime_type,
                "content": content
            }
        else:
            return {
                "success": False,
                "error": f"File type '{mime_type}' not supported for text reading",
                "user_id": user_id
            }
        
    except Exception as e:
        log_action(user_id, "read_file_content", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def update_document_content(api_key: str, file_id: str, new_content: str) -> dict:
    """Update the contents of a Google Docs document
    
    Args:
        api_key: User's API key for authentication
        file_id: The Google Drive file ID of the document to update
        new_content: The new text content
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}
    
    token_data = get_user_tokens(user_id)
    if not token_data:
        return {"error": "User not authenticated"}

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError

        creds = Credentials(
            token=token_data.get("access_token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            scopes=SCOPES,
        )

        drive_service = build("drive", "v3", credentials=creds)
        file_metadata = drive_service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,capabilities"
        ).execute()
        
        mime_type = file_metadata.get("mimeType", "")
        capabilities = file_metadata.get("capabilities", {})
        can_edit = capabilities.get("canEdit", False)
        
        if not can_edit:
            log_action(user_id, "update_document_content", False, "mcp_tool", "No edit permission")
            return {
                "success": False,
                "error": "You do not have edit permissions for this document",
                "user_id": user_id,
                "file_id": file_id
            }
        
        if mime_type == "application/vnd.google-apps.document":
            docs_service = build("docs", "v1", credentials=creds)
            doc = docs_service.documents().get(documentId=file_id).execute()
            content_length = doc.get('body').get('content')[-1].get('endIndex') - 1
            
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
            
            result = docs_service.documents().batchUpdate(
                documentId=file_id,
                body={'requests': requests_payload}
            ).execute()
            
            log_action(user_id, "update_document_content", True, "mcp_tool", f"File: {file_id}")
            
            return {
                "success": True,
                "user_id": user_id,
                "file_id": file_id,
                "name": file_metadata["name"],
                "message": "Document updated successfully",
                "content_length": len(new_content)
            }
        else:
            return {
                "success": False,
                "error": f"File type '{mime_type}' is not a Google Doc",
                "user_id": user_id
            }
        
    except HttpError as e:
        log_action(user_id, "update_document_content", False, "mcp_tool", str(e))
        return {
            "error_type": "HttpError",
            "status_code": e.resp.status,
            "error": str(e),
            "user_id": user_id,
            "file_id": file_id
        }
    except Exception as e:
        log_action(user_id, "update_document_content", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def get_auth_status(api_key: str) -> dict:
    """Check authentication status
    
    Args:
        api_key: User's API key
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"authenticated": False, "error": "Invalid API key"}
    
    token_data = get_user_tokens(user_id)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT email, display_name, last_login FROM users WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            "authenticated": token_data is not None,
            "user_id": user_id,
            "email": row[0],
            "display_name": row[1],
            "last_login": row[2].isoformat() if row[2] else None,
            "has_valid_token": token_data is not None
        }
    
    return {"authenticated": False, "error": "User not found"}

# Create the MCP ASGI app
mcp_asgi = mcp.http_app(path='/mcp')

# Create Starlette app
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse as StarletteJSONResponse

async def start_auth(request):
    """Start OAuth flow"""
    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        return StarletteJSONResponse({"error": "OAuth environment variables missing"}, status_code=500)

    from urllib.parse import urlencode
    
    # Generate state token for CSRF protection
    state = secrets.token_urlsafe(32)
    
    params = urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    
    return StarletteJSONResponse({
        "auth_url": f"https://accounts.google.com/o/oauth2/v2/auth?{params}",
        "state": state,
        "message": "Visit the auth_url to authenticate"
    })

async def oauth_callback(request):
    """Handle OAuth callback"""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    
    if not code:
        return StarletteJSONResponse({"error": "Missing code"}, status_code=400)

    # Exchange code for tokens
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
    
    # Get user info from Google
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
        return StarletteJSONResponse({"error": f"Failed to get user info: {str(e)}"}, status_code=500)
    
    # Check if user exists
    existing_user = get_user_by_email(user_email)
    
    if existing_user:
        user_id = existing_user["user_id"]
        api_key = "***existing***"  # Don't expose existing API key
    else:
        # Create new user
        user_id, api_key = create_user(user_email, display_name)
    
    # Store tokens
    store_tokens(user_id, token_data, SCOPES)
    
    # Create session
    ip_address = request.client.host
    user_agent = request.headers.get("user-agent", "unknown")
    session_token = create_session(user_id, ip_address, user_agent)
    
    # Update last login
    update_last_login(user_id)
    
    # Log the authentication
    log_action(user_id, "oauth_callback", True, ip_address, "User authenticated")
    
    return StarletteJSONResponse({
        "status": "connected",
        "user_id": user_id,
        "email": user_email,
        "display_name": display_name,
        "api_key": api_key if api_key != "***existing***" else "Use existing API key",
        "session_token": session_token,
        "message": "IMPORTANT: Save your API key securely. You won't be able to see it again!"
    })

async def health(request):
    """Health check endpoint"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE is_active = 1")
        active_users = cursor.fetchone()[0]
        conn.close()
        
        return StarletteJSONResponse({
            "status": "ok",
            "database": "connected",
            "active_users": active_users
        })
    except Exception as e:
        return StarletteJSONResponse({
            "status": "error",
            "database": "disconnected",
            "error": str(e)
        }, status_code=500)

async def root(request):
    """Root endpoint"""
    return StarletteJSONResponse({
        "service": "Google Drive MCP Server with Azure SQL",
        "endpoints": {
            "auth": "/auth - Start OAuth flow",
            "callback": "/oauth2callback - OAuth callback",
            "health": "/health - Health check",
            "mcp": "/mcp/ - MCP protocol endpoint"
        },
        "usage": {
            "step_1": "Visit /auth to start authentication",
            "step_2": "Complete OAuth flow in browser",
            "step_3": "Save your API key from the callback response",
            "step_4": "Use your API key in all MCP tool calls"
        }
    })

async def my_ip(request):
    """Returns the current outbound IP of the Render service."""
    ip = requests.get("https://api.ipify.org?format=json").json()
    return StarletteJSONResponse(ip)

# Create main app
app = Starlette(
    routes=[
        Route("/", root),
        Route("/auth", start_auth),
        Route("/oauth2callback", oauth_callback),
        Route("/health", health),
        Route("/myip", my_ip),
        Mount("/", mcp_asgi),
    ],
    lifespan=mcp_asgi.lifespan,
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
