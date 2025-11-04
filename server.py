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
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events"
]

# In-memory token storage for single user
stored_token = None

# Create MCP instance
mcp = FastMCP("Google Drive, Gmail & Calendar MCP")

# --- HELPER FUNCTIONS ---

def _get_credentials():
    """Helper to create Google credentials from stored token"""
    from google.oauth2.credentials import Credentials
    return Credentials(
        token=stored_token.get("access_token"),
        refresh_token=stored_token.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES,
    )

# --- DRIVE TOOLS ---

@mcp.tool()
async def list_drive_files(max_results: int = 20) -> dict:
    """List files from Google Drive
    
    Args:
        max_results: Maximum number of files to return (default: 20, max: 100)
    """
    if not stored_token:
        return {"error": "No Google account connected. Please authenticate first at /auth"}

    try:
        from googleapiclient.discovery import build

        max_results = min(max_results, 100)
        creds = _get_credentials()
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
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseDownload

        creds = _get_credentials()
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
        from googleapiclient.discovery import build

        creds = _get_credentials()
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
        from googleapiclient.discovery import build

        creds = _get_credentials()
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
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError

        creds = _get_credentials()
        drive_service = build("drive", "v3", credentials=creds)
        
        file_metadata = drive_service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,capabilities"
        ).execute()
        
        mime_type = file_metadata.get("mimeType", "")
        capabilities = file_metadata.get("capabilities", {})
        can_edit = capabilities.get("canEdit", False)
        
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
        return {
            "error_type": "HttpError",
            "status_code": e.resp.status,
            "reason": e.resp.reason,
            "file_id": file_id,
            "traceback": traceback.format_exc()
        }
    except Exception as e:
        return {
            "error_type": type(e).__name__,
            "error": str(e),
            "file_id": file_id,
            "traceback": traceback.format_exc()
        }

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
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError

        creds = _get_credentials()
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
        
        result = {
            "searched_for": file_name,
            "matched_file": files[0]["name"]
        }
        
        if len(files) > 1:
            result["note"] = f"Found {len(files)} matching documents, updating the first one: '{files[0]['name']}'"
            result["other_matches"] = [{"id": f["id"], "name": f["name"]} for f in files[1:]]
        
        # Get file metadata to check type and permissions
        file_metadata = service.files().get(
            fileId=file_id,
            fields="id,name,mimeType,capabilities"
        ).execute()
        
        mime_type = file_metadata.get("mimeType", "")
        capabilities = file_metadata.get("capabilities", {})
        can_edit = capabilities.get("canEdit", False)
        
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
        
        api_result = docs_service.documents().batchUpdate(
            documentId=file_id,
            body={'requests': requests_payload}
        ).execute()
        
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

# --- GMAIL TOOLS ---

async def _list_emails_helper(max_results: int = 20, query: str = "") -> dict:
    """Helper function to list emails - used by list_emails and search_emails"""
    if not stored_token:
        return {"error": "No Google account connected. Please authenticate first at /auth"}

    try:
        from googleapiclient.discovery import build

        max_results = min(max_results, 100)
        creds = _get_credentials()
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
async def list_emails(max_results: int = 20, query: str = "") -> dict:
    """List emails from Gmail inbox
    
    Args:
        max_results: Maximum number of emails to return (default: 20, max: 100)
        query: Gmail search query (e.g., "is:unread", "from:someone@example.com", "subject:important")
               Leave empty to get all emails. See Gmail search operators for more options.
    
    Returns:
        Dictionary containing list of emails with basic info (id, threadId, snippet, date, from, subject)
    """
    return await _list_emails_helper(max_results=max_results, query=query)

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
        from googleapiclient.discovery import build

        creds = _get_credentials()
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
        from googleapiclient.discovery import build

        creds = _get_credentials()
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
    return await _list_emails_helper(max_results=max_results, query=query)

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
        from googleapiclient.discovery import build

        creds = _get_credentials()
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
        from googleapiclient.discovery import build

        creds = _get_credentials()
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

# --- CALENDAR TOOLS ---

@mcp.tool()
async def list_calendar_events(max_results: int = 10, time_min: str = "", time_max: str = "", calendar_id: str = "primary") -> dict:
    """List upcoming events from Google Calendar
    
    Args:
        max_results: Maximum number of events to return (default: 10, max: 100)
        time_min: Lower bound (inclusive) for event start time (ISO 8601 format, e.g., "2024-11-04T00:00:00Z")
                  Leave empty to start from now
        time_max: Upper bound (exclusive) for event start time (ISO 8601 format)
                  Leave empty for no upper bound
        calendar_id: Calendar identifier (default: "primary" for user's primary calendar)
    
    Returns:
        Dictionary containing list of events with details (id, summary, start, end, description, location)
    """
    if not stored_token:
        return {"error": "No Google account connected. Please authenticate first at /auth"}

    try:
        from googleapiclient.discovery import build
        from datetime import datetime, timezone

        max_results = min(max_results, 100)
        creds = _get_credentials()
        service = build("calendar", "v3", credentials=creds)
        
        # Build query parameters
        query_params = {
            "calendarId": calendar_id,
            "maxResults": max_results,
            "singleEvents": True,
            "orderBy": "startTime"
        }
        
        # Use current time if time_min not specified
        if time_min:
            query_params["timeMin"] = time_min
        else:
            query_params["timeMin"] = datetime.now(timezone.utc).isoformat()
        
        if time_max:
            query_params["timeMax"] = time_max
        
        # Get events
        events_result = service.events().list(**query_params).execute()
        events = events_result.get("items", [])
        
        if not events:
            return {
                "success": True,
                "count": 0,
                "events": [],
                "calendar_id": calendar_id
            }
        
        # Format events
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
        
        return {
            "success": True,
            "count": len(event_list),
            "calendar_id": calendar_id,
            "events": event_list
        }
        
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}

@mcp.tool()
async def create_calendar_event(
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
        summary: Event title/summary (required)
        start_time: Event start time in ISO 8601 format (e.g., "2024-11-04T10:00:00-05:00" or "2024-11-04" for all-day)
        end_time: Event end time in ISO 8601 format (e.g., "2024-11-04T11:00:00-05:00" or "2024-11-05" for all-day)
        description: Event description (optional)
        location: Event location (optional)
        attendees: Comma-separated list of attendee email addresses (optional, e.g., "person1@example.com,person2@example.com")
        calendar_id: Calendar identifier (default: "primary" for user's primary calendar)
    
    Returns:
        Dictionary with success status and created event details
    """
    if not stored_token:
        return {"error": "No Google account connected. Please authenticate first at /auth"}

    try:
        from googleapiclient.discovery import build

        creds = _get_credentials()
        service = build("calendar", "v3", credentials=creds)
        
        # Build event object
        event = {
            "summary": summary,
            "description": description,
            "location": location,
        }
        
        # Handle start time (check if all-day event)
        if "T" in start_time:
            event["start"] = {"dateTime": start_time, "timeZone": "America/New_York"}
        else:
            event["start"] = {"date": start_time}
        
        # Handle end time
        if "T" in end_time:
            event["end"] = {"dateTime": end_time, "timeZone": "America/New_York"}
        else:
            event["end"] = {"date": end_time}
        
        # Add attendees if provided
        if attendees:
            attendee_list = [{"email": email.strip()} for email in attendees.split(",")]
            event["attendees"] = attendee_list
        
        # Create the event
        created_event = service.events().insert(
            calendarId=calendar_id,
            body=event,
            sendUpdates="all" if attendees else "none"
        ).execute()
        
        return {
            "success": True,
            "event_id": created_event["id"],
            "summary": created_event.get("summary"),
            "start": created_event["start"].get("dateTime", created_event["start"].get("date")),
            "end": created_event["end"].get("dateTime", created_event["end"].get("date")),
            "htmlLink": created_event.get("htmlLink"),
            "message": "Event created successfully"
        }
        
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}

@mcp.tool()
async def update_calendar_event(
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
        event_id: The event ID to update (required)
        summary: New event title/summary (optional - leave empty to keep unchanged)
        start_time: New start time in ISO 8601 format (optional)
        end_time: New end time in ISO 8601 format (optional)
        description: New event description (optional)
        location: New event location (optional)
        calendar_id: Calendar identifier (default: "primary")
    
    Returns:
        Dictionary with success status and updated event details
    """
    if not stored_token:
        return {"error": "No Google account connected. Please authenticate first at /auth"}

    try:
        from googleapiclient.discovery import build

        creds = _get_credentials()
        service = build("calendar", "v3", credentials=creds)
        
        # Get the existing event
        event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        
        # Update fields if provided
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
        
        # Update the event
        updated_event = service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=event
        ).execute()
        
        return {
            "success": True,
            "event_id": updated_event["id"],
            "summary": updated_event.get("summary"),
            "start": updated_event["start"].get("dateTime", updated_event["start"].get("date")),
            "end": updated_event["end"].get("dateTime", updated_event["end"].get("date")),
            "htmlLink": updated_event.get("htmlLink"),
            "message": "Event updated successfully"
        }
        
    except Exception as e:
        return {"error": str(e), "event_id": event_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def delete_calendar_event(event_id: str, calendar_id: str = "primary") -> dict:
    """Delete an event from Google Calendar
    
    Args:
        event_id: The event ID to delete (required)
        calendar_id: Calendar identifier (default: "primary" for user's primary calendar)
    
    Returns:
        Dictionary with success status
    """
    if not stored_token:
        return {"error": "No Google account connected. Please authenticate first at /auth"}

    try:
        from googleapiclient.discovery import build

        creds = _get_credentials()
        service = build("calendar", "v3", credentials=creds)
        
        # Delete the event
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        
        return {
            "success": True,
            "event_id": event_id,
            "calendar_id": calendar_id,
            "message": "Event deleted successfully"
        }
        
    except Exception as e:
        return {"error": str(e), "event_id": event_id, "traceback": traceback.format_exc()}

@mcp.tool()
async def search_calendar_events(query: str, max_results: int = 10, calendar_id: str = "primary") -> dict:
    """Search for events in Google Calendar by keyword
    
    Args:
        query: Search query to match against event summaries, descriptions, locations, and attendee names/emails
        max_results: Maximum number of events to return (default: 10, max: 100)
        calendar_id: Calendar identifier (default: "primary" for user's primary calendar)
    
    Returns:
        Dictionary containing matching events
    """
    if not stored_token:
        return {"error": "No Google account connected. Please authenticate first at /auth"}

    try:
        from googleapiclient.discovery import build
        from datetime import datetime, timezone

        max_results = min(max_results, 100)
        creds = _get_credentials()
        service = build("calendar", "v3", credentials=creds)
        
        # Search events
        events_result = service.events().list(
            calendarId=calendar_id,
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
            timeMin=datetime.now(timezone.utc).isoformat(),
            q=query
        ).execute()
        
        events = events_result.get("items", [])
        
        if not events:
            return {
                "success": True,
                "count": 0,
                "query": query,
                "events": [],
                "calendar_id": calendar_id
            }
        
        # Format events
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
                "htmlLink": event.get("htmlLink", "")
            })
        
        return {
            "success": True,
            "count": len(event_list),
            "query": query,
            "calendar_id": calendar_id,
            "events": event_list
        }
        
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}

@mcp.tool()
async def get_auth_status() -> dict:
    """Check if the server is authenticated with Google Drive, Gmail, and Calendar, and get authenticated user info"""
    status = {
        "authenticated": stored_token is not None,
        "owner": OWNER_EMAIL if stored_token else None,
        "scopes": SCOPES,
        "message": "Connected to Google Drive, Gmail, and Calendar" if stored_token else "Not authenticated. Please visit /auth to connect."
    }
    
    if stored_token:
        try:
            from googleapiclient.discovery import build
            
            creds = _get_credentials()
            
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

# Create the MCP ASGI app
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
        "service": "Google Drive, Gmail & Calendar MCP Server",
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
            "Calendar: list_calendar_events, create_calendar_event, update_calendar_event, delete_calendar_event, search_calendar_events",
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
