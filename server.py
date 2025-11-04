from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from dotenv import load_dotenv
import os
import requests
import io
import traceback
import asyncio
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

load_dotenv()

# Environment variables
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "owner@example.com")
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify"
]

# In-memory token storage for single user
stored_token = None

# Create MCP instance
mcp = FastMCP("Google Drive & Gmail MCP")

# Add dependencies for proper async handling
import asyncio

# --- DRIVE TOOLS (existing) ---
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
        return {"error": str(e), "traceback": traceback.format_exc()}

async def _read_file_content_helper(file_id: str) -> dict:
    """Helper function to read file content - used by multiple tools"""
    if not stored_token:
        return {"error": "No Google account connected. Please authenticate first at /auth"}

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
            "file_id": file_id,
            "name": file_metadata["name"],
            "mimeType": mime_type,
            "size": file_metadata.get("size"),
            "content": None,
            "message": "Binary file - content not displayed. Use webViewLink to access.",
            "webViewLink": file_metadata.get("webViewLink")
        }
        
    except Exception as e:
        return {"error": str(e), "file_id": file_id, "traceback": traceback.format_exc()}

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
        return {"error": str(e), "traceback": traceback.format_exc()}

@mcp.tool()
async def read_file_by_name(file_name: str) -> dict:
    """Read the contents of a file from Google Drive by searching for its name
    
    Args:
        file_name: The name of the file to search for and read
    
    Returns:
        Dictionary containing file metadata and content. If multiple files match, reads the first one.
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
        result = await _read_file_content_helper(file_id)
        result.update(match_info)
        return result
        
    except Exception as e:
        return {"error": str(e), "searched_for": file_name, "traceback": traceback.format_exc()}

@mcp.tool()
async def read_file_content(file_id: str) -> dict:
    """Read the contents of a specific file from Google Drive using its file ID
    
    Args:
        file_id: The Google Drive file ID (not the file name) to read
    
    Returns:
        Dictionary containing file metadata and content (for text files) or download info (for binary files)
    """
    return await _read_file_content_helper(file_id)

@mcp.tool()
async def update_document_content(file_id: str, new_content: str) -> dict:
    """Update the contents of a Google Docs document
    
    Args:
        file_id: The Google Drive file ID of the document to update
        new_content: The new text content to write to the document (replaces all existing content)
    
    Returns:
        Dictionary with success status and updated file information
    """
    if not stored_token:
        return {"error": "No Google account connected. Please authenticate first at /auth"}

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError

        print(f"\n=== UPDATE DOCUMENT DEBUG ===")
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
        print(f"Scopes in creds: {creds.scopes}")

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
            print(f"Request payload: {requests_payload}")
            
            result = docs_service.documents().batchUpdate(
                documentId=file_id,
                body={'requests': requests_payload}
            ).execute()
            
            print(f"BatchUpdate result: {result}")
            print("=== UPDATE COMPLETE ===\n")
            
            return {
                "success": True,
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
                "file_id": file_id,
                "name": file_metadata["name"]
            }
        
    except HttpError as e:
        error_details = {
            "error_type": "HttpError",
            "status_code": e.resp.status,
            "reason": e.resp.reason,
            "error_details": e.error_details if hasattr(e, 'error_details') else str(e),
            "file_id": file_id,
            "traceback": traceback.format_exc()
        }
        print(f"HTTP Error occurred: {error_details}")
        return error_details
    except Exception as e:
        error_details = {
            "error_type": type(e).__name__,
            "error": str(e),
            "file_id": file_id,
            "traceback": traceback.format_exc()
        }
        print(f"Exception occurred: {error_details}")
        return error_details

@mcp.tool()
async def update_document_by_name(file_name: str, new_content: str) -> dict:
    """Update the contents of a Google Docs document by searching for its name
    
    Args:
        file_name: The name of the document to search for and update
        new_content: The new text content to write to the document (replaces all existing content)
    
    Returns:
        Dictionary with success status and updated file information
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
                "searched_for": file_name
            }
        
        # Use the first matching file
        file_id = files[0]["id"]
        
        print(f"Found file: {files[0]['name']} (ID: {file_id})")
        
        # Build the result with match info
        result = {
            "searched_for": file_name,
            "matched_file": files[0]["name"]
        }
        
        if len(files) > 1:
            result["note"] = f"Found {len(files)} matching documents, updating the first one: '{files[0]['name']}'"
            result["other_matches"] = [{"id": f["id"], "name": f["name"]} for f in files[1:]]
        
        # Update the document by directly implementing the logic here
        # (Can't call another @mcp.tool() from within a tool)
        from googleapiclient.errors import HttpError
        
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
            "searched_for": file_name,
            "traceback": traceback.format_exc()
        }

# --- GMAIL TOOLS (new) ---

async def _list_emails_helper(max_results: int = 20, query: str = "") -> dict:
    """Helper function to list emails - used by multiple tools"""
    """List emails from Gmail inbox
    
    Args:
        max_results: Maximum number of emails to return (default: 20, max: 100)
        query: Gmail search query (e.g., "is:unread", "from:someone@example.com", "subject:important")
               Leave empty to get all emails. See Gmail search operators for more options.
    
    Returns:
        Dictionary containing list of emails with basic info (id, threadId, snippet, date, from, subject)
    """
    if not stored_token:
        return {"error": "No Google account connected. Please authenticate first at /auth"}

    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from datetime import datetime

        max_results = min(max_results, 100)
        
        creds = Credentials(
            token=stored_token.get("access_token"),
            refresh_token=stored_token.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            scopes=SCOPES,
        )

        service = build("gmail", "v1", credentials=creds)
        
        # List messages
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
                "count": 0,
                "emails": [],
                "query": query if query else "all emails"
            }
        
        # Get details for each message
        email_list = []
        for msg in messages:
            msg_data = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "To", "Subject", "Date"]
            ).execute()
            
            headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
            
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
            "count": len(email_list),
            "query": query if query else "all emails",
            "emails": email_list
        }
        
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}

@mcp.tool()
async def read_email(email_id: str) -> dict:
    """Read the full content of a specific email
    
    Args:
        email_id: The Gmail message ID to read
    
    Returns:
        Dictionary containing full email details including body content
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

        service = build("gmail", "v1", credentials=creds)
        
        # Get full message
        message = service.users().messages().get(
            userId="me",
            id=email_id,
            format="full"
        ).execute()
        
        # Extract headers
        headers = {h["name"]: h["value"] for h in message.get("payload", {}).get("headers", [])}
        
        # Extract body
        def get_body(payload):
            """Recursively extract email body from payload"""
            if "body" in payload and "data" in payload["body"]:
                return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
            
            if "parts" in payload:
                for part in payload["parts"]:
                    if part.get("mimeType") == "text/plain":
                        if "data" in part.get("body", {}):
                            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                    
                    # Try recursively for nested parts
                    body = get_body(part)
                    if body:
                        return body
            
            return None
        
        body = get_body(message.get("payload", {}))
        
        return {
            "success": True,
            "id": message["id"],
            "threadId": message["threadId"],
            "labels": message.get("labelIds", []),
            "from": headers.get("From", "Unknown"),
            "to": headers.get("To", ""),
            "cc": headers.get("Cc", ""),
            "subject": headers.get("Subject", "(No subject)"),
            "date": headers.get("Date", ""),
            "snippet": message.get("snippet", ""),
            "body": body if body else "(Could not extract body - may be HTML only or have attachments)",
            "raw_payload_available": "payload" in message
        }
        
    except Exception as e:
        return {"error": str(e), "email_id": email_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def send_email(to: str, subject: str, body: str, cc: str = "", bcc: str = "") -> dict:
    """Send an email via Gmail
    
    Args:
        to: Recipient email address (or comma-separated list for multiple recipients)
        subject: Email subject line
        body: Email body content (plain text)
        cc: CC recipients (optional, comma-separated)
        bcc: BCC recipients (optional, comma-separated)
    
    Returns:
        Dictionary with success status and sent message details
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

        service = build("gmail", "v1", credentials=creds)
        
        # Create message
        message = MIMEText(body)
        message["to"] = to
        message["subject"] = subject
        
        if cc:
            message["cc"] = cc
        if bcc:
            message["bcc"] = bcc
        
        # Encode message
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        
        # Send message
        sent_message = service.users().messages().send(
            userId="me",
            body={"raw": raw_message}
        ).execute()
        
        return {
            "success": True,
            "message_id": sent_message["id"],
            "thread_id": sent_message["threadId"],
            "to": to,
            "subject": subject,
            "message": "Email sent successfully"
        }
        
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}

@mcp.tool()
async def search_emails(query: str, max_results: int = 20) -> dict:
    """Search emails using Gmail search operators
    
    Args:
        query: Gmail search query (e.g., "from:someone@example.com", "subject:meeting", 
               "is:unread after:2024/01/01", "has:attachment")
        max_results: Maximum number of results to return (default: 20, max: 100)
    
    Returns:
        Dictionary containing matching emails
    
    Common search operators:
    - from:email@example.com - emails from a specific sender
    - to:email@example.com - emails to a specific recipient
    - subject:keyword - emails with keyword in subject
    - is:unread - unread emails
    - is:starred - starred emails
    - has:attachment - emails with attachments
    - after:2024/01/01 - emails after a date
    - before:2024/12/31 - emails before a date
    """
    # This uses the same logic as list_emails but with explicit search query
    return await list_emails(max_results=max_results, query=query)

@mcp.tool()
async def mark_email_as_read(email_id: str) -> dict:
    """Mark an email as read
    
    Args:
        email_id: The Gmail message ID to mark as read
    
    Returns:
        Dictionary with success status
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

        service = build("gmail", "v1", credentials=creds)
        
        # Remove UNREAD label
        service.users().messages().modify(
            userId="me",
            id=email_id,
            body={"removeLabelIds": ["UNREAD"]}
        ).execute()
        
        return {
            "success": True,
            "email_id": email_id,
            "message": "Email marked as read"
        }
        
    except Exception as e:
        return {"error": str(e), "email_id": email_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def mark_email_as_unread(email_id: str) -> dict:
    """Mark an email as unread
    
    Args:
        email_id: The Gmail message ID to mark as unread
    
    Returns:
        Dictionary with success status
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

        service = build("gmail", "v1", credentials=creds)
        
        # Add UNREAD label
        service.users().messages().modify(
            userId="me",
            id=email_id,
            body={"addLabelIds": ["UNREAD"]}
        ).execute()
        
        return {
            "success": True,
            "email_id": email_id,
            "message": "Email marked as unread"
        }
        
    except Exception as e:
        return {"error": str(e), "email_id": email_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def get_auth_status() -> dict:
    """Check if the server is authenticated with Google Drive and Gmail, and get authenticated user info"""
    status = {
        "authenticated": stored_token is not None,
        "owner": OWNER_EMAIL if stored_token else None,
        "scopes": SCOPES,
        "message": "Connected to Google Drive and Gmail" if stored_token else "Not authenticated. Please visit /auth to connect."
    }
    
    if stored_token:
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
            
            # Get info about the authenticated user
            drive_service = build("drive", "v3", credentials=creds)
            about = drive_service.about().get(fields="user").execute()
            
            status["authenticated_user"] = {
                "email": about.get("user", {}).get("emailAddress"),
                "display_name": about.get("user", {}).get("displayName")
            }
        except Exception as e:
            status["error_getting_user_info"] = str(e)
        
        status["token_preview"] = {
            "has_access_token": "access_token" in stored_token,
            "has_refresh_token": "refresh_token" in stored_token,
            "access_token_preview": stored_token.get("access_token", "")[:20] + "..." if stored_token.get("access_token") else None
        }
    
    return status

# Create the MCP ASGI app - this creates a Starlette app with the MCP endpoint at /mcp/
mcp_asgi = mcp.http_app(path='/mcp')

# Create a Starlette app to combine everything
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse as StarletteJSONResponse

# Define OAuth routes
async def start_auth(request):
    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        return StarletteJSONResponse({"error": "OAuth environment variables missing"}, status_code=500)

    from urllib.parse import urlencode
    params = urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    })
    return StarletteJSONResponse({"auth_url": f"https://accounts.google.com/o/oauth2/v2/auth?{params}"})

async def oauth_callback(request):
    global stored_token
    code = request.query_params.get("code")
    if not code:
        return StarletteJSONResponse({"error": "Missing code"}, status_code=400)

    token_resp = requests.post("https://oauth2.googleapis.com/token", data={
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    })

    if token_resp.status_code != 200:
        return StarletteJSONResponse({"error": f"Token exchange failed: {token_resp.text}"}, status_code=500)

    stored_token = token_resp.json()
    print(f"\n=== NEW TOKEN STORED ===")
    print(f"Token keys: {stored_token.keys()}")
    print(f"Scope in token: {stored_token.get('scope')}")
    print("========================\n")
    
    return StarletteJSONResponse({
        "status": "connected", 
        "owner": OWNER_EMAIL,
        "scopes_granted": stored_token.get('scope', '').split()
    })

async def health(request):
    return StarletteJSONResponse({
        "status": "ok", 
        "authenticated": stored_token is not None,
        "owner": OWNER_EMAIL,
        "scopes_configured": SCOPES
    })

async def root(request):
    return StarletteJSONResponse({
        "service": "Google Drive & Gmail MCP Server",
        "endpoints": {
            "auth": "/auth - Start OAuth flow",
            "callback": "/oauth2callback - OAuth callback",
            "health": "/health - Health check",
            "mcp": "/mcp/ - MCP protocol endpoint (POST only)"
        },
        "authenticated": stored_token is not None,
        "scopes": SCOPES,
        "available_tools": [
            "Drive: list_drive_files, search_drive_files, read_file_by_name, read_file_content, update_document_content, update_document_by_name",
            "Gmail: list_emails, read_email, send_email, search_emails, mark_email_as_read, mark_email_as_unread",
            "Auth: get_auth_status"
        ]
    })

# Create the main app using Starlette and mount everything
app = Starlette(
    routes=[
        Route("/", root),
        Route("/auth", start_auth),
        Route("/oauth2callback", oauth_callback),
        Route("/health", health),
        Mount("/", mcp_asgi),  # Mount MCP at root - it will handle /mcp/ path
    ],
    lifespan=mcp_asgi.lifespan,  # CRITICAL: Pass MCP's lifespan
)

# Export for uvicorn
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
