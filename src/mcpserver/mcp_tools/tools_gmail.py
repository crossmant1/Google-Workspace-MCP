import base64
import html
import re
import traceback
from typing import Optional, Dict
from email.mime.text import MIMEText
from googleapiclient.discovery import build

from mcpserver.auth import verify_email, _get_credentials
from mcpserver.database import log_action, get_user_tokens



def extract_email_body(payload):
    """
    Description:
        Extracts the email body from the payload, handling both plain text and HTML content.
    Args: 
        payload (dict): The payload of the email message.
    Returns:
        The extracted email body.
    """
    # Try to get plain text first
    if payload.get("mimeType") == "text/plain":
        body_data = payload.get("body", {}).get("data")
        if body_data:
            return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
    
    # Try HTML if plain text not available
    if payload.get("mimeType") == "text/html":
        body_data = payload.get("body", {}).get("data")
        if body_data:
            html_content = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
            # Strip HTML tags for better readability
            text = re.sub('<[^<]+?>', '', html_content)
            # Decode HTML entities
            text = html.unescape(text)
            return text
    
    # Recursively check parts for multipart messages
    if "parts" in payload:
        for part in payload["parts"]:
            body = extract_email_body(part)
            if body:
                return body
    
    return None

async def _list_emails_helper(user_id: str, query: Optional[str] = None, max_results: int = 20) -> dict:
    """
    Description:
        Helper function to list or search emails in Gmail.
    Args:
        user_id (str): The Google user ID.
        query (Optional[str]): The search query. If None, lists recent emails.
        max_results (int): Maximum number of emails to retrieve. Defaults to 20.
    Returns:
        A dictionary containing the result of the email listing or search.
    """
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

#@mcp.tool()
async def list_emails(email: str, max_results: int = 20) -> dict:
    """
    Description:
        List recent emails in Gmail.
    Args:
        email (str): The user's email address.
        max_results (int): Maximum number of emails to retrieve. Defaults to 20.
    Returns:
        A dictionary containing the result of the email listing.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}
    
    result = await _list_emails_helper(user_id, max_results=max_results)
    if "error" not in result:
        log_action(user_id, "list_emails", True, "mcp_tool", f"Found {result.get('count')} emails")
    else:
        log_action(user_id, "list_emails", False, "mcp_tool", result.get("error"))
    return result

#@mcp.tool()
async def search_emails(email: str, query: str, max_results: int = 20) -> dict:
    """
    Description:
        Search for emails in Gmail.
    Args:
        email (str): The user's email address.
        query (str): The search query.
        max_results (int): Maximum number of emails to retrieve. Defaults to 20.
    Returns:
        A dictionary containing the result of the email search.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}
        
    result = await _list_emails_helper(user_id, query=query, max_results=max_results)
    if "error" not in result:
        log_action(user_id, "search_emails", True, "mcp_tool", f"Query: {query}, Found: {result.get('count')}")
    else:
        log_action(user_id, "search_emails", False, "mcp_tool", result.get("error"))
    return result

#@mcp.tool()
async def read_email(email: str, email_id: str) -> dict:
    """
    Description:
        Read a specific email by its ID in Gmail.
    Args:
        email (str): The user's email address.
        email_id (str): The ID of the email to read.
    Returns:
        A dictionary containing the email details.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

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
        
        # Use improved body extraction with HTML fallback
        body = extract_email_body(message.get("payload", {}))
        
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

#@mcp.tool()
async def send_email(
    email: str,
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None
) -> dict:
    """
    Description:
        Send an email via Gmail.
    Args:
        email (str): The user's email address.
        to (str): Recipient email address.
        subject (str): Subject of the email.
        body (str): Body content of the email.
        cc (Optional[str]): CC recipient email address. Defaults to None.
        bcc (Optional[str]): BCC recipient email address. Defaults to None.
    Returns:
        A dictionary containing the result of the email sending operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

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

#@mcp.tool()
async def mark_email_as_read(email: str, email_id: str) -> dict:
    """
    Description:
        Mark an email as read (removes the UNREAD label).
    Args:
        email (str): The user's email address.
        email_id (str): The ID of the email to mark as read.
    Returns:
        A dictionary containing the result of the operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

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

#@mcp.tool()
async def mark_email_as_unread(email: str, email_id: str) -> dict:
    """
    Description:
        Mark an email as unread (adds the UNREAD label).
    Args:
        email (str): The user's email address.
        email_id (str): The ID of the email to mark as unread.
    Returns:
        A dictionary containing the result of the operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

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