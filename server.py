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
import pyodbc  # <--- Changed from pymssql
from cryptography.fernet import Fernet
import hashlib
import json
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# For Starlette app
from starlette.applications import Starlette
from starlette.responses import JSONResponse as StarletteJSONResponse
from starlette.routing import Route, Mount
from starlette.requests import Request as StarletteRequest
import urllib.parse

load_dotenv()

# Environment variables
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")

# Azure SQL Database connection
AZURE_SQL_SERVER = os.getenv("AZURE_SQL_SERVER")
AZURE_SQL_DATABASE = os.getenv("AZURE_SQL_DATABASE")
AZURE_SQL_USERNAME = os.getenv("AZURE_SQL_USERNAME")
AZURE_SQL_PASSWORD = os.getenv("AZURE_SQL_PASSWORD")

# Encryption key for tokens
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    print("WARNING: No ENCRYPTION_KEY found, generating temporary key (DO NOT USE IN PRODUCTION)")
    ENCRYPTION_KEY = Fernet.generate_key()
else:
    ENCRYPTION_KEY = ENCRYPTION_KEY.encode()

cipher_suite = Fernet(ENCRYPTION_KEY)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks"
]

# Database connection
def get_db_connection():
    """Create a connection to Azure SQL Database using pyodbc"""
    # Clean server name
    server = AZURE_SQL_SERVER.replace('.database.windows.net', '').replace('tcp:', '')
    
    # Try multiple drivers in order of preference
    drivers = ["{ODBC Driver 18 for SQL Server}", "{ODBC Driver 17 for SQL Server}"]
    
    for driver in drivers:
        conn_str = (
            f"DRIVER={driver};"
            f"SERVER={server}.database.windows.net;"
            f"DATABASE={AZURE_SQL_DATABASE};"
            f"UID={AZURE_SQL_USERNAME};"
            f"PWD={AZURE_SQL_PASSWORD};"
            "Encrypt=yes;"
            "TrustServerCertificate=no;"
            "Connection Timeout=30;"
        )
        
        try:
            conn = pyodbc.connect(conn_str)
            return conn
        except pyodbc.Error as e:
            if driver == drivers[-1]:  # Last driver attempt
                print(f"Database connection failed with all drivers. Last error: {e}")
                raise
            # Try next driver
            continue

# Security helper functions
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
def create_user(email: str, display_name: str) -> tuple:
    """Create a new user and return user_id and api_key"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        user_id = secrets.token_urlsafe(16)
        api_key = secrets.token_urlsafe(32)
        api_key_hash = hash_api_key(api_key)
        
        cursor.execute("""
            INSERT INTO users (user_id, email, display_name, api_key_hash, created_at, last_login, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, email, display_name, api_key_hash, datetime.utcnow(), datetime.utcnow(), 1))
        
        conn.commit()
        return user_id, api_key
    finally:
        cursor.close()
        conn.close()
        
def get_user_by_email(email: str) -> Optional[dict]:
    """Get user by email"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT user_id, email, display_name, is_active FROM users WHERE email = ?", (email,))
        row = cursor.fetchone()
        
        if row:
            return {
                "user_id": row[0],
                "email": row[1],
                "display_name": row[2],
                "is_active": bool(row[3])
            }
        return None
    finally:
        cursor.close()
        conn.close()

def get_user_by_api_key(api_key: str) -> Optional[str]:
    """Get user_id by API key"""
    api_key_hash = hash_api_key(api_key)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT user_id FROM users WHERE api_key_hash = ? AND is_active = 1", (api_key_hash,))
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        cursor.close()
        conn.close()

def store_tokens(user_id: str, token_data: dict, scopes: list):
    """Store or update tokens for a user"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        encrypted_access = encrypt_token({"token": token_data.get("access_token")})
        encrypted_refresh = encrypt_token({"token": token_data.get("refresh_token")})
        
        expires_in = token_data.get("expires_in", 3600)
        token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
        
        scopes_str = " ".join(scopes)
        
        cursor.execute("SELECT user_id FROM tokens WHERE user_id = ?", (user_id,))
        exists = cursor.fetchone()
        
        if exists:
            cursor.execute("""
                UPDATE tokens
                SET access_token = ?, refresh_token = ?, token_expiry = ?, scopes = ?, updated_at = ?
                WHERE user_id = ?
            """, (encrypted_access, encrypted_refresh, token_expiry, scopes_str, datetime.utcnow(), user_id))
        else:
            cursor.execute("""
                INSERT INTO tokens (user_id, access_token, refresh_token, token_expiry, scopes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, encrypted_access, encrypted_refresh, token_expiry, scopes_str, datetime.utcnow()))
        
        conn.commit()
    finally:
        cursor.close()
        conn.close()

def get_user_tokens(user_id: str) -> Optional[dict]:
    """Get decrypted tokens for a user"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT access_token, refresh_token, token_expiry, scopes
            FROM tokens
            WHERE user_id = ?
        """, (user_id,))
        
        row = cursor.fetchone()
        
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
    finally:
        cursor.close()
        conn.close()

def create_session(user_id: str, ip_address: str, user_agent: str) -> str:
    """Create a new session and return session token"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(days=30)
        
        cursor.execute("""
            INSERT INTO sessions (session_token, user_id, created_at, expires_at, ip_address, user_agent)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_token, user_id, datetime.utcnow(), expires_at, ip_address, user_agent))
        
        conn.commit()
        return session_token
    finally:
        cursor.close()
        conn.close()

def get_user_from_session(session_token: str) -> Optional[str]:
    """Get user_id from session token if valid"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            SELECT user_id FROM sessions
            WHERE session_token = ? AND expires_at > ?
        """, (session_token, datetime.utcnow()))
        
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        cursor.close()
        conn.close()

def log_action(user_id: str, action: str, success: bool, source: str, details: str, ip_address: str = "N/A"):
    """Log an action to the audit_logs table"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Truncate details if too long
            if len(details) > 1024:
                details = details[:1021] + "..."
            
            # Convert boolean to int for SQL Server
            success_int = 1 if success else 0
                
            cursor.execute("""
                INSERT INTO audit_logs (user_id, action, timestamp, success, ip_address, source, details)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, action, datetime.utcnow(), success_int, ip_address, source, details))
            
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    except Exception as e:
        print(f"Failed to log action: {e}")

def update_last_login(user_id: str):
    """Update user's last login timestamp"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("UPDATE users SET last_login = ? WHERE user_id = ?", (datetime.utcnow(), user_id))
        conn.commit()
    finally:
        cursor.close()
        conn.close()

# Authentication helper
async def verify_api_key(api_key: Optional[str]) -> Optional[str]:
    """Verify API key and return user_id"""
    if not api_key:
        return None
    return get_user_by_api_key(api_key)

# Credentials helper
def _get_credentials(user_id: str):
    """Helper to create Google credentials from user's stored token"""
    from google.oauth2.credentials import Credentials
    
    token_data = get_user_tokens(user_id)
    if not token_data:
        return None
    
    return Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=token_data.get("scopes", SCOPES),
    )

# --- MCP SETUP ---
mcp = FastMCP("Google Drive, Gmail, Calendar & Tasks MCP")

# --- HELPER FUNCTIONS ---

async def _read_file_content_helper(user_id: str, file_id: str) -> dict:
    """Helper function to read file content - used by multiple tools"""
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload

        creds = _get_credentials(user_id)
        service = build("drive", "v3", credentials=creds)
        
        file_metadata = service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size,modifiedTime,webViewLink"
        ).execute()
        
        mime_type = file_metadata.get("mimeType", "")
        
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
                    "user_id": user_id,
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
                    "file_id": file_id,
                    "name": file_metadata["name"],
                    "webViewLink": file_metadata.get("webViewLink")
                }
        
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        
        done = False
        while not done:
            status, done = downloader.next_chunk()
        
        content_bytes = fh.getvalue()
        
        text_mime_types = [
            "text/", "application/json", "application/xml",
            "application/javascript", "application/x-python"
        ]
        
        if any(mime_type.startswith(t) for t in text_mime_types):
            try:
                content = content_bytes.decode("utf-8")
                return {
                    "success": True,
                    "user_id": user_id,
                    "file_id": file_id,
                    "name": file_metadata["name"],
                    "mimeType": mime_type,
                    "size": len(content_bytes),
                    "content": content
                }
            except UnicodeDecodeError:
                pass
        
        return {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "name": file_metadata["name"],
            "mimeType": mime_type,
            "size": file_metadata.get("size"),
            "content": None,
            "message": "Binary file - content not displayed.",
            "webViewLink": file_metadata.get("webViewLink")
        }
        
    except Exception as e:
        return {"error": str(e), "user_id": user_id, "file_id": file_id, "traceback": traceback.format_exc()}


# --- DRIVE TOOLS ---

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

    try:
        from googleapiclient.discovery import build

        max_results = min(max_results, 100)
        creds = _get_credentials(user_id)
        service = build("drive", "v3", credentials=creds)
        
        res = service.files().list(
            pageSize=max_results, 
            fields="files(id,name,mimeType,modifiedTime,size)"
        ).execute()
        
        files = res.get("files", [])
        log_action(user_id, "list_drive_files", True, "mcp_tool", f"Found {len(files)} files")
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

    try:
        from googleapiclient.discovery import build

        creds = _get_credentials(user_id)
        service = build("drive", "v3", credentials=creds)
        
        safe_query = query.replace("'", "\\'")
        res = service.files().list(
            q=f"name contains '{safe_query}'",
            pageSize=min(max_results, 100),
            fields="files(id,name,mimeType,modifiedTime,size)"
        ).execute()
        
        files = res.get("files", [])
        log_action(user_id, "search_drive_files", True, "mcp_tool", f"Query: {query}, Found: {len(files)}")
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
async def read_file_by_name(api_key: str, file_name: str) -> dict:
    """Read the contents of a file from Google Drive by searching for its name
    
    Args:
        api_key: User's API key for authentication
        file_name: The name of the file to search for and read
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build

        creds = _get_credentials(user_id)
        service = build("drive", "v3", credentials=creds)
        
        safe_name = file_name.replace("'", "\\'")
        res = service.files().list(
            q=f"name = '{safe_name}'",
            pageSize=5,
            fields="files(id,name)"
        ).execute()
        
        files = res.get("files", [])
        if not files:
            log_action(user_id, "read_file_by_name", False, "mcp_tool", f"File not found: {file_name}")
            return {"error": "File not found", "user_id": user_id, "searched_for": file_name}
        
        file_id = files[0]["id"]
        
        if len(files) > 1:
            match_info = {
                "note": f"Found {len(files)} matching files, reading the first one: '{files[0]['name']}'",
                "other_matches": [{"id": f["id"], "name": f["name"]} for f in files[1:]]
            }
        else:
            match_info = {}
            
        result = await _read_file_content_helper(user_id, file_id)
        result.update(match_info)
        log_action(user_id, "read_file_by_name", True, "mcp_tool", f"File: {file_name}")
        return result
        
    except Exception as e:
        log_action(user_id, "read_file_by_name", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}

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
    
    try:
        result = await _read_file_content_helper(user_id, file_id)
        log_action(user_id, "read_file_content", True, "mcp_tool", f"File: {file_id}")
        return result
    except Exception as e:
        log_action(user_id, "read_file_content", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "file_id": file_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def update_document_content(api_key: str, file_id: str, new_content: str) -> dict:
    """Update the contents of a Google Docs document
    
    Args:
        api_key: User's API key for authentication
        file_id: The Google Docs file ID to update
        new_content: The new text content to write (replaces all existing content)
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError

        creds = _get_credentials(user_id)
        drive_service = build("drive", "v3", credentials=creds)
        docs_service = build("docs", "v1", credentials=creds)
        
        file_metadata = drive_service.files().get(
            fileId=file_id,
            fields="name,mimeType"
        ).execute()
        
        if file_metadata.get("mimeType") != "application/vnd.google-apps.document":
            log_action(user_id, "update_document_content", False, "mcp_tool", "File is not a Google Doc")
            return {"error": "File is not a Google Doc", "user_id": user_id, "file_id": file_id, "mimeType": file_metadata.get("mimeType")}

        doc = docs_service.documents().get(documentId=file_id).execute()
        content_length = doc.get("body", {}).get("content", [])[-1].get("endIndex", 1) - 1

        requests_payload = []
        if content_length > 1:
            requests_payload.append({
                'deleteContentRange': {
                    'range': {
                        'startIndex': 1,
                        'endIndex': content_length
                    }
                }
            })
        
        requests_payload.append({
            'insertText': {
                'location': {
                    'index': 1
                },
                'text': new_content
            }
        })
        
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
async def update_document_by_name(api_key: str, file_name: str, new_content: str) -> dict:
    """Update the contents of a Google Docs document by searching for its name
    
    Args:
        api_key: User's API key for authentication
        file_name: The name of the document to search for and update
        new_content: The new text content to write to the document
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build

        creds = _get_credentials(user_id)
        service = build("drive", "v3", credentials=creds)
        
        safe_name = file_name.replace("'", "\\'")
        res = service.files().list(
            q=f"name = '{safe_name}' and mimeType = 'application/vnd.google-apps.document'",
            pageSize=5,
            fields="files(id,name)"
        ).execute()
        
        files = res.get("files", [])
        if not files:
            log_action(user_id, "update_document_by_name", False, "mcp_tool", f"Doc not found: {file_name}")
            return {"error": "Google Doc not found", "user_id": user_id, "searched_for": file_name}
        
        file_id = files[0]["id"]
        
        if len(files) > 1:
            match_info = {
                "note": f"Found {len(files)} matching docs, updating the first one: '{files[0]['name']}'"
            }
        else:
            match_info = {}
            
        result = await update_document_content(api_key, file_id, new_content)
        result.update(match_info)
        return result
        
    except Exception as e:
        log_action(user_id, "update_document_by_name", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}

# --- GMAIL TOOLS ---

async def _list_emails_helper(user_id: str, query: Optional[str] = None, max_results: int = 20) -> dict:
    """Helper for list_emails and search_emails"""
    try:
        from googleapiclient.discovery import build
        
        max_results = min(max_results, 100)
        creds = _get_credentials(user_id)
        service = build("gmail", "v1", credentials=creds)
        
        list_params = {
            "userId": "me",
            "maxResults": max_results
        }
        if query:
            list_params["q"] = query
            
        results = service.users().messages().list(**list_params).execute()
        messages = results.get("messages", [])
        
        if not messages:
            return {
                "success": True,
                "user_id": user_id,
                "count": 0,
                "query": query if query else "all emails",
                "emails": []
            }
            
        email_list = []
        
        for msg in messages:
            msg_data = service.users().messages().get(
                userId="me", 
                id=msg["id"], 
                format="metadata", 
                metadataHeaders=["From", "To", "Subject", "Date"]
            ).execute()
            
            headers = {h["name"]: h["value"] for h in msg_data["payload"]["headers"]}
            email_list.append({
                "id": msg_data["id"],
                "threadId": msg_data["threadId"],
                "snippet": msg_data.get("snippet", ""),
                "from": headers.get("From", "Unknown"),
                "to": headers.get("To", ""),
                "subject": headers.get("Subject", "(No subject)"),
                "date": headers.get("Date", ""),
                "labels": msg_data.get("labelIds", [])
            })
            
        return {
            "success": True,
            "user_id": user_id,
            "count": len(email_list),
            "query": query if query else "all emails",
            "emails": email_list
        }
    except Exception as e:
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def list_emails(api_key: str, max_results: int = 20) -> dict:
    """List recent emails from Gmail
    
    Args:
        api_key: User's API key for authentication
        max_results: Maximum number of emails to return (default: 20)
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}
    
    result = await _list_emails_helper(user_id, max_results=max_results)
    if "error" not in result:
        log_action(user_id, "list_emails", True, "mcp_tool", f"Found {result.get('count')} emails")
    else:
        log_action(user_id, "list_emails", False, "mcp_tool", result.get("error"))
    return result

@mcp.tool()
async def search_emails(api_key: str, query: str, max_results: int = 20) -> dict:
    """Search for emails in Gmail
    
    Args:
        api_key: User's API key for authentication
        query: Gmail search query (e.g., "from:boss subject:report")
        max_results: Maximum number of emails to return (default: 20)
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}
        
    result = await _list_emails_helper(user_id, query=query, max_results=max_results)
    if "error" not in result:
        log_action(user_id, "search_emails", True, "mcp_tool", f"Query: {query}, Found: {result.get('count')}")
    else:
        log_action(user_id, "search_emails", False, "mcp_tool", result.get("error"))
    return result

@mcp.tool()
async def read_email(api_key: str, email_id: str) -> dict:
    """Read the full body of a specific email
    
    Args:
        api_key: User's API key for authentication
        email_id: The Gmail message ID
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build

        creds = _get_credentials(user_id)
        service = build("gmail", "v1", credentials=creds)
        
        message = service.users().messages().get(
            userId="me", 
            id=email_id,
            format="full"
        ).execute()
        
        headers = {h["name"]: h["value"] for h in message["payload"]["headers"]}
        
        def get_body(payload):
            if payload.get("mimeType") == "text/plain":
                body = payload.get("body", {}).get("data")
                if body:
                    return base64.urlsafe_b64decode(body).decode("utf-8", errors="replace")
            
            if "parts" in payload:
                for part in payload["parts"]:
                    body = get_body(part)
                    if body:
                        return body
            return None

        body = get_body(message.get("payload", {}))
        
        log_action(user_id, "read_email", True, "mcp_tool", f"Email: {email_id}")
        return {
            "success": True,
            "user_id": user_id,
            "id": message["id"],
            "threadId": message["threadId"],
            "labels": message.get("labelIds", []),
            "from": headers.get("From", "Unknown"),
            "to": headers.get("To", ""),
            "cc": headers.get("Cc", ""),
            "subject": headers.get("Subject", "(No subject)"),
            "date": headers.get("Date", ""),
            "snippet": message.get("snippet", ""),
            "body": body if body else ""
        }
    except Exception as e:
        log_action(user_id, "read_email", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "email_id": email_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def send_email(
    api_key: str,
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None
) -> dict:
    """Send an email
    
    Args:
        api_key: User's API key for authentication
        to: Recipient email address
        subject: Email subject line
        body: Email body content
        cc: CC recipients (optional)
        bcc: BCC recipients (optional)
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        
        creds = _get_credentials(user_id)
        service = build("gmail", "v1", credentials=creds)
        
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        if cc:
            message["cc"] = cc
        if bcc:
            message["bcc"] = bcc
            
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
        
        send_message_request = {
            "raw": raw_message
        }
        
        sent_message = service.users().messages().send(
            userId="me",
            body=send_message_request
        ).execute()
        
        log_action(user_id, "send_email", True, "mcp_tool", f"To: {to}, Subject: {subject}")
        return {
            "success": True,
            "user_id": user_id,
            "message_id": sent_message["id"],
            "thread_id": sent_message["threadId"],
            "to": to,
            "subject": subject
        }
    except Exception as e:
        log_action(user_id, "send_email", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def mark_email_as_read(api_key: str, email_id: str) -> dict:
    """Mark an email as read (removes the UNREAD label)
    
    Args:
        api_key: User's API key for authentication
        email_id: The Gmail message ID to mark as read
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        
        creds = _get_credentials(user_id)
        service = build("gmail", "v1", credentials=creds)
        
        service.users().messages().modify(
            userId="me",
            id=email_id,
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        
        log_action(user_id, "mark_email_as_read", True, "mcp_tool", f"Email: {email_id}")
        return {
            "success": True,
            "user_id": user_id,
            "email_id": email_id,
            "message": "Email marked as read"
        }
    except Exception as e:
        log_action(user_id, "mark_email_as_read", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "email_id": email_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def mark_email_as_unread(api_key: str, email_id: str) -> dict:
    """Mark an email as unread (adds the UNREAD label)
    
    Args:
        api_key: User's API key for authentication
        email_id: The Gmail message ID to mark as unread
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        
        creds = _get_credentials(user_id)
        service = build("gmail", "v1", credentials=creds)
        
        service.users().messages().modify(
            userId="me",
            id=email_id,
            body={"addLabelIds": ["UNREAD"]}
        ).execute()
        
        log_action(user_id, "mark_email_as_unread", True, "mcp_tool", f"Email: {email_id}")
        return {
            "success": True,
            "user_id": user_id,
            "email_id": email_id,
            "message": "Email marked as unread"
        }
    except Exception as e:
        log_action(user_id, "mark_email_as_unread", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "email_id": email_id, "traceback": traceback.format_exc()}


# --- GOOGLE CALENDAR TOOLS ---

@mcp.tool()
async def list_calendar_events(
    api_key: str,
    max_results: int = 10,
    calendar_id: str = "primary"
) -> dict:
    """List upcoming events from Google Calendar
    
    Args:
        api_key: User's API key for authentication
        max_results: Maximum number of events to return (default: 10)
        calendar_id: Calendar identifier (default: "primary")
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        from datetime import datetime, timezone

        creds = _get_credentials(user_id)
        service = build("calendar", "v3", credentials=creds)
        
        now = datetime.now(timezone.utc).isoformat()
        
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime"
        ).execute()
        
        events = events_result.get("items", [])
        event_list = []
        
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            end = event["end"].get("dateTime", event["end"].get("date"))
            event_list.append({
                "id": event["id"],
                "summary": event.get("summary", "(No title)"),
                "description": event.get("description", ""),
                "location": event.get("location", ""),
                "start": start,
                "end": end,
                "status": event.get("status", ""),
                "htmlLink": event.get("htmlLink", ""),
                "attendees": [
                    {"email": a.get("email"), "responseStatus": a.get("responseStatus")}
                    for a in event.get("attendees", [])
                ]
            })
            
        log_action(user_id, "list_calendar_events", True, "mcp_tool", f"Found {len(event_list)} events")
        return {
            "success": True,
            "user_id": user_id,
            "count": len(event_list),
            "calendar_id": calendar_id,
            "events": event_list
        }
    except Exception as e:
        log_action(user_id, "list_calendar_events", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def create_calendar_event(
    api_key: str,
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    location: str = "",
    attendees: str = "",
    calendar_id: str = "primary"
) -> dict:
    """Create a new event in Google Calendar
    
    Args:
        api_key: User's API key for authentication
        summary: Event title (required)
        start_time: Event start time in ISO 8601 format
        end_time: Event end time in ISO 8601 format
        description: Event description (optional)
        location: Event location (optional)
        attendees: Comma-separated list of attendee emails (optional)
        calendar_id: Calendar identifier (default: "primary")
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        
        creds = _get_credentials(user_id)
        service = build("calendar", "v3", credentials=creds)
        
        event = {
            "summary": summary,
            "description": description,
            "location": location,
        }
        
        # Handle all-day vs. dateTime
        if "T" in start_time:
            event["start"] = {"dateTime": start_time, "timeZone": "America/New_York"}
        else:
            event["start"] = {"date": start_time}
            
        if "T" in end_time:
            event["end"] = {"dateTime": end_time, "timeZone": "America/New_York"}
        else:
            event["end"] = {"date": end_time}
            
        if attendees:
            event["attendees"] = [{"email": email.strip()} for email in attendees.split(",")]
            
        created_event = service.events().insert(
            calendarId=calendar_id,
            body=event,
            sendUpdates="all"
        ).execute()
        
        log_action(user_id, "create_calendar_event", True, "mcp_tool", f"Event: {summary}")
        return {
            "success": True,
            "user_id": user_id,
            "event_id": created_event["id"],
            "summary": created_event.get("summary"),
            "start": created_event["start"].get("dateTime", created_event["start"].get("date")),
            "end": created_event["end"].get("dateTime", created_event["end"].get("date")),
            "htmlLink": created_event.get("htmlLink")
        }
    except Exception as e:
        log_action(user_id, "create_calendar_event", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def update_calendar_event(
    api_key: str,
    event_id: str,
    summary: str = "",
    start_time: str = "",
    end_time: str = "",
    description: str = "",
    location: str = "",
    calendar_id: str = "primary"
) -> dict:
    """Update an existing event in Google Calendar
    
    Args:
        api_key: User's API key for authentication
        event_id: The event ID to update (required)
        summary: New event title/summary (optional - leave empty to keep unchanged)
        start_time: New start time in ISO 8601 format (optional)
        end_time: New end time in ISO 8601 format (optional)
        description: New event description (optional)
        location: New event location (optional)
        calendar_id: Calendar identifier (default: "primary")
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        
        creds = _get_credentials(user_id)
        service = build("calendar", "v3", credentials=creds)
        
        event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        
        if summary:
            event["summary"] = summary
        if description:
            event["description"] = description
        if location:
            event["location"] = location
            
        if start_time:
            if "T" in start_time:
                event["start"] = {"dateTime": start_time, "timeZone": "America/New_York"}
            else:
                event["start"] = {"date": start_time}
        
        if end_time:
            if "T" in end_time:
                event["end"] = {"dateTime": end_time, "timeZone": "America/New_York"}
            else:
                event["end"] = {"date": end_time}
        
        updated_event = service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=event
        ).execute()
        
        log_action(user_id, "update_calendar_event", True, "mcp_tool", f"Event: {event_id}")
        return {
            "success": True,
            "user_id": user_id,
            "event_id": updated_event["id"],
            "summary": updated_event.get("summary"),
            "start": updated_event["start"].get("dateTime", updated_event["start"].get("date")),
            "end": updated_event["end"].get("dateTime", updated_event["end"].get("date")),
            "htmlLink": updated_event.get("htmlLink")
        }
    except Exception as e:
        log_action(user_id, "update_calendar_event", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "event_id": event_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def delete_calendar_event(
    api_key: str,
    event_id: str,
    calendar_id: str = "primary"
) -> dict:
    """Delete an event from Google Calendar
    
    Args:
        api_key: User's API key for authentication
        event_id: The event ID to delete
        calendar_id: Calendar identifier (default: "primary")
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        
        creds = _get_credentials(user_id)
        service = build("calendar", "v3", credentials=creds)
        
        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id
        ).execute()
        
        log_action(user_id, "delete_calendar_event", True, "mcp_tool", f"Event: {event_id}")
        return {
            "success": True,
            "user_id": user_id,
            "event_id": event_id,
            "message": "Event deleted successfully"
        }
    except Exception as e:
        log_action(user_id, "delete_calendar_event", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "event_id": event_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def search_calendar_events(
    api_key: str,
    query: str,
    max_results: int = 10,
    calendar_id: str = "primary"
) -> dict:
    """Search for calendar events matching a query
    
    Args:
        api_key: User's API key for authentication
        query: Search query (e.g., "team meeting")
        max_results: Maximum number of events to return (default: 10)
        calendar_id: Calendar identifier (default: "primary")
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        from datetime import datetime, timezone

        creds = _get_credentials(user_id)
        service = build("calendar", "v3", credentials=creds)
        
        events_result = service.events().list(
            calendarId=calendar_id,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
            timeMin=datetime.now(timezone.utc).isoformat(),
            q=query
        ).execute()
        
        events = events_result.get("items", [])
        event_list = []
        
        for event in events:
            start = event["start"].get("dateTime", event["start"].get("date"))
            end = event["end"].get("dateTime", event["end"].get("date"))
            event_list.append({
                "id": event["id"],
                "summary": event.get("summary", "(No title)"),
                "description": event.get("description", ""),
                "location": event.get("location", ""),
                "start": start,
                "end": end,
                "status": event.get("status", ""),
                "htmlLink": event.get("htmlLink")
            })
            
        log_action(user_id, "search_calendar_events", True, "mcp_tool", f"Query: {query}, Found: {len(event_list)}")
        return {
            "success": True,
            "user_id": user_id,
            "count": len(event_list),
            "query": query,
            "calendar_id": calendar_id,
            "events": event_list
        }
    except Exception as e:
        log_action(user_id, "search_calendar_events", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}

# --- GOOGLE TASKS TOOLS ---

@mcp.tool()
async def list_task_lists(api_key: str) -> dict:
    """List all Google Tasks task lists
    
    Args:
        api_key: User's API key for authentication
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        
        creds = _get_credentials(user_id)
        service = build("tasks", "v1", credentials=creds)
        
        results = service.tasklists().list(maxResults=100).execute()
        items = results.get("items", [])
        
        task_lists = [
            {"id": tl["id"], "title": tl["title"], "updated": tl["updated"]}
            for tl in items
        ]
        
        log_action(user_id, "list_task_lists", True, "mcp_tool", f"Found {len(task_lists)} lists")
        return {
            "success": True,
            "user_id": user_id,
            "count": len(task_lists),
            "task_lists": task_lists
        }
    except Exception as e:
        log_action(user_id, "list_task_lists", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def list_tasks(
    api_key: str,
    task_list_id: str = "@default",
    max_results: int = 20
) -> dict:
    """List tasks from a specific Google Tasks list
    
    Args:
        api_key: User's API key for authentication
        task_list_id: The task list ID (default: "@default")
        max_results: Maximum number of tasks to return (default: 20)
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        
        creds = _get_credentials(user_id)
        service = build("tasks", "v1", credentials=creds)
        
        results = service.tasks().list(
            tasklist=task_list_id,
            maxResults=max_results
        ).execute()
        
        items = results.get("items", [])
        
        formatted_tasks = []
        for task in items:
            formatted_tasks.append({
                "id": task["id"],
                "title": task.get("title", ""),
                "notes": task.get("notes", ""),
                "status": task.get("status", ""),
                "due": task.get("due", ""),
                "updated": task.get("updated", ""),
                "completed": task.get("completed", "")
            })
            
        log_action(user_id, "list_tasks", True, "mcp_tool", f"Found {len(formatted_tasks)} tasks")
        return {
            "success": True,
            "user_id": user_id,
            "count": len(formatted_tasks),
            "task_list_id": task_list_id,
            "tasks": formatted_tasks
        }
    except Exception as e:
        log_action(user_id, "list_tasks", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def create_task(
    api_key: str,
    title: str,
    notes: str = "",
    due: str = "",
    task_list_id: str = "@default"
) -> dict:
    """Create a new task in Google Tasks
    
    Args:
        api_key: User's API key for authentication
        title: Task title (required)
        notes: Task notes (optional)
        due: Due date in RFC 3339 format (optional)
        task_list_id: The task list ID (default: "@default")
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        
        creds = _get_credentials(user_id)
        service = build("tasks", "v1", credentials=creds)
        
        task = {
            "title": title,
            "notes": notes
        }
        if due:
            task["due"] = due
            
        result = service.tasks().insert(
            tasklist=task_list_id,
            body=task
        ).execute()
        
        log_action(user_id, "create_task", True, "mcp_tool", f"Task: {title}")
        
        return {
            "success": True,
            "user_id": user_id,
            "task_id": result["id"],
            "title": result["title"],
            "notes": result.get("notes", ""),
            "due": result.get("due", ""),
            "status": result.get("status", ""),
            "message": "Task created successfully"
        }
        
    except Exception as e:
        log_action(user_id, "create_task", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def create_task_from_email(
    api_key: str,
    email_id: str,
    task_list_id: str = "@default",
    add_link: bool = True
) -> dict:
    """Create a Google Task from a Gmail email
    
    Args:
        api_key: User's API key for authentication
        email_id: The Gmail message ID (required)
        task_list_id: The task list ID (default: "@default")
        add_link: Whether to include a link to the email (default: True)
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        
        creds = _get_credentials(user_id)
        gmail_service = build("gmail", "v1", credentials=creds)
        tasks_service = build("tasks", "v1", credentials=creds)
        
        # 1. Get email details
        message = gmail_service.users().messages().get(
            userId="me", 
            id=email_id,
            format="metadata",
            metadataHeaders=["From", "Subject"]
        ).execute()
        
        headers = {h["name"]: h["value"] for h in message["payload"]["headers"]}
        snippet = message.get("snippet", "")
        subject = headers.get("Subject", "(No subject)")
        from_email = headers.get("From", "Unknown sender")
        
        # 2. Build task
        task_title = f"Email: {subject}"
        task_notes = f"From: {from_email}\n\n{snippet}"
        if add_link:
            email_link = f"https://mail.google.com/mail/u/0/#inbox/{email_id}"
            task_notes += f"\n\nEmail link: {email_link}"
            
        # 3. Create the task
        task = {
            "title": task_title,
            "notes": task_notes
        }
        result = tasks_service.tasks().insert(
            tasklist=task_list_id,
            body=task
        ).execute()
        
        log_action(user_id, "create_task_from_email", True, "mcp_tool", f"Email: {email_id}, Task: {result['id']}")
        return {
            "success": True,
            "user_id": user_id,
            "task_id": result["id"],
            "title": result["title"],
            "notes": result.get("notes", ""),
            "status": result.get("status", ""),
            "message": "Task created from email successfully"
        }
        
    except Exception as e:
        log_action(user_id, "create_task_from_email", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "email_id": email_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def update_task(
    api_key: str,
    task_id: str,
    title: str = "",
    notes: str = "",
    due: str = "",
    task_list_id: str = "@default"
) -> dict:
    """Update an existing task in Google Tasks
    
    Args:
        api_key: User's API key for authentication
        task_id: The task ID to update (required)
        title: New task title (optional)
        notes: New task notes (optional)
        due: New due date in RFC 3339 format (optional)
        task_list_id: The task list ID (default: "@default")
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        
        creds = _get_credentials(user_id)
        service = build("tasks", "v1", credentials=creds)
        
        task = service.tasks().get(tasklist=task_list_id, task=task_id).execute()
        
        if title:
            task["title"] = title
        if notes:
            task["notes"] = notes
        if due:
            task["due"] = due
        
        result = service.tasks().update(
            tasklist=task_list_id,
            task=task_id,
            body=task
        ).execute()
        
        log_action(user_id, "update_task", True, "mcp_tool", f"Task: {task_id}")
        return {
            "success": True,
            "user_id": user_id,
            "task_id": result["id"],
            "title": result["title"],
            "notes": result.get("notes", ""),
            "due": result.get("due", ""),
            "status": result.get("status", "")
        }
    except Exception as e:
        log_action(user_id, "update_task", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "task_id": task_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def complete_task(
    api_key: str,
    task_id: str,
    task_list_id: str = "@default"
) -> dict:
    """Mark a task as completed
    
    Args:
        api_key: User's API key for authentication
        task_id: The task ID to complete
        task_list_id: The task list ID (default: "@default")
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        
        creds = _get_credentials(user_id)
        service = build("tasks", "v1", credentials=creds)
        
        task = service.tasks().get(tasklist=task_list_id, task=task_id).execute()
        task["status"] = "completed"
        
        result = service.tasks().update(
            tasklist=task_list_id,
            task=task_id,
            body=task
        ).execute()
        
        log_action(user_id, "complete_task", True, "mcp_tool", f"Task: {task_id}")
        return {
            "success": True,
            "user_id": user_id,
            "task_id": result["id"],
            "status": result["status"],
            "message": "Task marked as completed"
        }
    except Exception as e:
        log_action(user_id, "complete_task", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "task_id": task_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def delete_task(
    api_key: str,
    task_id: str,
    task_list_id: str = "@default"
) -> dict:
    """Delete a task from Google Tasks
    
    Args:
        api_key: User's API key for authentication
        task_id: The task ID to delete
        task_list_id: The task list ID (default: "@default")
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"error": "Invalid API key"}

    try:
        from googleapiclient.discovery import build
        
        creds = _get_credentials(user_id)
        service = build("tasks", "v1", credentials=creds)
        
        service.tasks().delete(
            tasklist=task_list_id,
            task=task_id
        ).execute()
        
        log_action(user_id, "delete_task", True, "mcp_tool", f"Task: {task_id}")
        return {
            "success": True,
            "user_id": user_id,
            "task_id": task_id,
            "message": "Task deleted successfully"
        }
    except Exception as e:
        log_action(user_id, "delete_task", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "task_id": task_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def get_auth_status(api_key: str) -> dict:
    """Check the authentication status and return user info
    
    Args:
        api_key: User's API key for authentication
    """
    user_id = await verify_api_key(api_key)
    if not user_id:
        return {"authenticated": False, "error": "Invalid API key"}
        
    user_info = get_user_by_email(get_user_tokens(user_id).get("email", ""))
    if not user_info:
        # Fallback if email not in token (shouldn't happen)
        user_tokens = get_user_tokens(user_id)
    
    return {
        "authenticated": True,
        "user_id": user_id,
        "scopes": get_user_tokens(user_id).get("scopes", []),
        "token_expiry": get_user_tokens(user_id).get("token_expiry")
    }


# --- STARLETTE APP & OAUTH ENDPOINTS ---

mcp_asgi = mcp.build_asgi_app()

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
    
    # Store state in session (if using sessions) or pass it
    # For simplicity, we'll just redirect
    return StarletteJSONResponse({"auth_url": auth_url})

async def oauth_callback(request: StarletteRequest):
    """Handle the OAuth2 callback from Google"""
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build

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

        # Get user info to create user
        service = build("oauth2", "v2", credentials=creds)
        user_info = service.userinfo().get().execute()
        
        email = user_info.get("email")
        display_name = user_info.get("name")
        
        if not email:
            return StarletteJSONResponse({"error": "Could not retrieve email from Google"}, status_code=500)

        # Check if user exists, or create new one
        user = get_user_by_email(email)
        if user:
            user_id = user["user_id"]
            api_key = "REUSED" # We don't return the key on re-auth
        else:
            user_id, api_key = create_user(email, display_name)
        
        # Store the new tokens
        store_tokens(user_id, token_data, SCOPES)
        
        # Update last login
        update_last_login(user_id)
        
        # Create a session for the browser (optional, but good for web UIs)
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
        conn.close()
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
        "usage": {
            "step_1": "Visit /auth to get the authentication URL",
            "step_2": "Complete OAuth flow in browser",
            "step_3": "Save your API key from the callback response",
            "step_4": "Use your API key in all MCP tool calls"
        }
    })

# Create main app
app = Starlette(
    routes=[
        Route("/", root),
        Route("/auth", start_auth),
        Route("/oauth2callback", oauth_callback),
        Route("/health", health),
        Mount("/mcp", mcp),  # ← Fixed: no .build_asgi_app(), mounted on /mcp
    ],
    lifespan=mcp.lifespan,
)
