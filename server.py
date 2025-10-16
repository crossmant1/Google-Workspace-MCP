from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastmcp import MCPServer
from dotenv import load_dotenv
import os, requests, json


load_dotenv()


app = FastAPI(title="MCP Google Server (Single User)")
mcp = MCPServer(app)


CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "owner@example.com")


SCOPES = ["https://www.googleapis.com/auth/drive.metadata.readonly"]


# Store the token in memory for the single user
stored_token = None


@app.get("/")
def root():
    return {"status": "running", "owner": OWNER_EMAIL}


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
uvicorn.run(app, host="0.0.0.0", port=8000)
