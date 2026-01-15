import io
import traceback
from typing import Optional, Dict
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from auth import verify_email, _get_credentials
from database import log_action, get_user_tokens
from database import sanitize_drive_query


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
    
    
#@mcp.tool()
async def list_drive_files(email: str, max_results: int = 20) -> dict:
    """List files from Google Drive"""
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

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

#@mcp.tool()
async def search_drive_files(email: str, query: str, max_results: int = 10) -> dict:
    """Search for files in Google Drive by name"""
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        from googleapiclient.discovery import build

        creds = _get_credentials(user_id)
        service = build("drive", "v3", credentials=creds)
        
        # Use proper Drive API query escaping
        safe_query = sanitize_drive_query(query)
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

#@mcp.tool()
async def read_file_by_name(email: str, file_name: str) -> dict:
    """Read the contents of a file from Google Drive by searching for its name"""
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        from googleapiclient.discovery import build

        creds = _get_credentials(user_id)
        service = build("drive", "v3", credentials=creds)
        
        # Use proper Drive API query escaping
        safe_name = sanitize_drive_query(file_name)
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

#@mcp.tool()
async def read_file_content(email: str, file_id: str) -> dict:
    """Read the contents of a specific file from Google Drive"""
    # Implementation unchanged
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}
    
    try:
        result = await _read_file_content_helper(user_id, file_id)
        log_action(user_id, "read_file_content", True, "mcp_tool", f"File: {file_id}")
        return result
    except Exception as e:
        log_action(user_id, "read_file_content", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "file_id": file_id, "traceback": traceback.format_exc()}

#@mcp.tool()
async def update_document_content(email: str, file_id: str, new_content: str) -> dict:
    """Update the contents of a Google Docs document"""
    # Implementation unchanged
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

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

#@mcp.tool()
async def update_document_by_name(email: str, file_name: str, new_content: str) -> dict:
    """Update the contents of a Google Docs document by searching for its name"""
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google"}

    try:
        from googleapiclient.discovery import build

        creds = _get_credentials(user_id)
        service = build("drive", "v3", credentials=creds)
        
        safe_name = sanitize_drive_query(file_name)
        res = service.files().list(
            q=f"name = '{safe_name}' and mimeType = 'application/vnd.google-apps.document'",
            pageSize=5,
            fields="files(id,name)"
        ).execute()
        
        files = res.get("files", [])
        if not files:
            log_action(user_id, "update_document_by_name", False, "mcp_tool", f"Doc not found: {file_name}")
            return {"error": "Google Doc not found", "user_id": user_id, "email": email, "searched_for": file_name}
        
        file_id = files[0]["id"]
        
        if len(files) > 1:
            match_info = {
                "note": f"Found {len(files)} matching docs, updating the first one: '{files[0]['name']}'"
            }
        else:
            match_info = {}
            
        result = await update_document_content(email=email, file_id=file_id, new_content=new_content)  # CHANGE: Add parameter names
        result.update(match_info)
        return result
        
    except Exception as e:
        log_action(user_id, "update_document_by_name", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "email": email, "searched_for": file_name, "traceback": traceback.format_exc()}
