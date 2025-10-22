from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from dotenv import load_dotenv
import os
import requests

load_dotenv()

# Environment variables
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "owner@example.com")
SCOPES = ["https://www.googleapis.com/auth/drive.metadata.readonly"]

# In-memory token storage for single user
stored_token = None

# Create MCP instance
mcp = FastMCP("Google Drive MCP")

# --- MCP Tools ---
@mcp.tool()
async def list_drive_files(max_results: int = 20) -> dict:
    """List files from Google Drive
    
    Args:
        max_results: Maximum number of files to return (default: 20, max: 100)
    """
    if not stored_token:
        return {"error": "No Google account connected. Please authenticate first at /auth"}

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
            "count": len(files),
            "files": files
        }
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def search_drive_files(query: str, max_results: int = 10) -> dict:
    """Search for files in Google Drive by name
    
    Args:
        query: Search query (file name to search for)
        max_results: Maximum number of results to return (default: 10)
    """
    if not stored_token:
        return {"error": "No Google account connected. Please authenticate first at /auth"}

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
            "query": query,
            "count": len(files),
            "files": files
        }
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def get_auth_status() -> dict:
    """Check if the server is authenticated with Google Drive"""
    return {
        "authenticated": stored_token is not None,
        "owner": OWNER_EMAIL if stored_token else None,
        "message": "Connected to Google Drive" if stored_token else "Not authenticated. Please visit /auth to connect."
    }

# Create wrapper FastAPI app for OAuth
app = FastAPI(title="Google Drive MCP Server")

# OAuth endpoints
@app.get("/auth")
def start_auth():
    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        raise HTTPException(status_code=500, detail="OAuth environment variables missing")

    from urllib.parse import urlencode
    params = urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    })
    return {"auth_url": f"https://accounts.google.com/o/oauth2/v2/auth?{params}"}

@app.get("/oauth2callback")
def oauth_callback(request: Request):
    global stored_token
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")

    token_resp = requests.post("https://oauth2.googleapis.com/token", data={
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    })

    if token_resp.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Token exchange failed: {token_resp.text}")

    stored_token = token_resp.json()
    return JSONResponse({"status": "connected", "owner": OWNER_EMAIL})

@app.get("/health")
def health():
    return {
        "status": "ok", 
        "authenticated": stored_token is not None,
        "owner": OWNER_EMAIL
    }

# Get the MCP ASGI app
mcp_app = mcp

# Middleware to route MCP requests to the MCP app
class MCPRouter(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Let OAuth routes through
        if request.url.path in ["/auth", "/oauth2callback", "/health"]:
            return await call_next(request)
        
        # Everything else goes to MCP
        return await mcp_app(request.scope, request.receive, request._send)

app.add_middleware(MCPRouter)

# Export for uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
