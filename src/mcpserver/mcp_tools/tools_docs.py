import traceback
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from mcpserver.auth import verify_email, _get_credentials
from mcpserver.database import log_action, sanitize_drive_query


#@mcp.tool()
async def create_document(email: str, title: str, content: str = "", folder_id: str = None) -> dict:
    """
    Description:
        Create a new Google Docs document.
    Args:
        email (str): The email of the user who will own the document.
        title (str): The title of the new document.
        content (str): Optional initial content for the document.
        folder_id (str): Optional folder ID where the document should be created.
    Returns:
        A dictionary containing the new document's details or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        creds = _get_credentials(user_id)
        docs_service = build("docs", "v1", credentials=creds)
        drive_service = build("drive", "v3", credentials=creds)
        
        # Create the document
        doc = docs_service.documents().create(body={"title": title}).execute()
        doc_id = doc.get("documentId")
        
        # Move to folder if specified
        if folder_id:
            drive_service.files().update(
                fileId=doc_id,
                addParents=folder_id,
                fields="id, parents"
            ).execute()
        
        # Add content if provided
        if content:
            requests_payload = [{
                'insertText': {
                    'location': {'index': 1},
                    'text': content
                }
            }]
            docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={'requests': requests_payload}
            ).execute()
        
        # Get the web view link
        file_metadata = drive_service.files().get(
            fileId=doc_id,
            fields="webViewLink"
        ).execute()
        
        log_action(user_id, "create_document", True, "mcp_tool", f"Created: {title}")
        return {
            "success": True,
            "user_id": user_id,
            "document_id": doc_id,
            "title": title,
            "webViewLink": file_metadata.get("webViewLink"),
            "message": "Document created successfully"
        }
        
    except Exception as e:
        log_action(user_id, "create_document", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}


#@mcp.tool()
async def delete_document(email: str, file_id: str) -> dict:
    """
    Description:
        Delete a Google Docs document (moves it to trash).
    Args:
        email (str): The email of the user whose document to delete.
        file_id (str): The ID of the document to delete.
    Returns:
        A dictionary indicating success or failure of the delete operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        creds = _get_credentials(user_id)
        drive_service = build("drive", "v3", credentials=creds)
        
        # Get file metadata first to confirm it's a doc and get the name
        file_metadata = drive_service.files().get(
            fileId=file_id,
            fields="name,mimeType"
        ).execute()
        
        if file_metadata.get("mimeType") != "application/vnd.google-apps.document":
            log_action(user_id, "delete_document", False, "mcp_tool", "File is not a Google Doc")
            return {"error": "File is not a Google Doc", "user_id": user_id, "file_id": file_id}
        
        # Move to trash
        drive_service.files().update(
            fileId=file_id,
            body={"trashed": True}
        ).execute()
        
        log_action(user_id, "delete_document", True, "mcp_tool", f"Deleted: {file_metadata['name']}")
        return {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "name": file_metadata["name"],
            "message": "Document moved to trash successfully"
        }
        
    except Exception as e:
        log_action(user_id, "delete_document", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "file_id": file_id, "traceback": traceback.format_exc()}


#@mcp.tool()
async def delete_document_by_name(email: str, file_name: str) -> dict:
    """
    Description:
        Delete a Google Docs document by searching for its name (moves it to trash).
    Args:
        email (str): The email of the user whose document to delete.
        file_name (str): The name of the document to delete.
    Returns:
        A dictionary indicating success or failure of the delete operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        creds = _get_credentials(user_id)
        service = build("drive", "v3", credentials=creds)
        
        safe_name = sanitize_drive_query(file_name)
        res = service.files().list(
            q=f"name = '{safe_name}' and mimeType = 'application/vnd.google-apps.document' and trashed = false",
            pageSize=5,
            fields="files(id,name)"
        ).execute()
        
        files = res.get("files", [])
        if not files:
            log_action(user_id, "delete_document_by_name", False, "mcp_tool", f"Doc not found: {file_name}")
            return {"error": "Google Doc not found", "user_id": user_id, "searched_for": file_name}
        
        file_id = files[0]["id"]
        
        if len(files) > 1:
            match_info = {
                "note": f"Found {len(files)} matching docs, deleting the first one: '{files[0]['name']}'"
            }
        else:
            match_info = {}
            
        result = await delete_document(email=email, file_id=file_id)
        result.update(match_info)
        return result
        
    except Exception as e:
        log_action(user_id, "delete_document_by_name", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def append_to_document(email: str, file_id: str, content: str) -> dict:
    """
    Description:
        Append content to the end of a Google Docs document.
    Args:
        email (str): The email of the user whose Google Doc to update.
        file_id (str): The ID of the Google Doc to append to.
        content (str): The content to append to the document.
    Returns:
        A dictionary indicating success or failure of the append operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        creds = _get_credentials(user_id)
        drive_service = build("drive", "v3", credentials=creds)
        docs_service = build("docs", "v1", credentials=creds)
        
        file_metadata = drive_service.files().get(
            fileId=file_id,
            fields="name,mimeType"
        ).execute()
        
        if file_metadata.get("mimeType") != "application/vnd.google-apps.document":
            log_action(user_id, "append_to_document", False, "mcp_tool", "File is not a Google Doc")
            return {"error": "File is not a Google Doc", "user_id": user_id, "file_id": file_id}

        doc = docs_service.documents().get(documentId=file_id).execute()
        end_index = doc.get("body", {}).get("content", [])[-1].get("endIndex", 1)
        
        requests_payload = [{
            'insertText': {
                'location': {'index': end_index - 1},
                'text': content
            }
        }]
        
        docs_service.documents().batchUpdate(
            documentId=file_id,
            body={'requests': requests_payload}
        ).execute()
        
        log_action(user_id, "append_to_document", True, "mcp_tool", f"File: {file_id}")
        return {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "name": file_metadata["name"],
            "message": "Content appended successfully",
            "appended_length": len(content)
        }
        
    except Exception as e:
        log_action(user_id, "append_to_document", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "file_id": file_id, "traceback": traceback.format_exc()}


#@mcp.tool()
async def append_to_document_by_name(email: str, file_name: str, content: str) -> dict:
    """
    Description:
        Append content to the end of a Google Docs document by searching for its name.
    Args:
        email (str): The email of the user whose Google Doc to update.
        file_name (str): The name of the Google Doc to append to.
        content (str): The content to append to the document.
    Returns:
        A dictionary indicating success or failure of the append operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
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
            log_action(user_id, "append_to_document_by_name", False, "mcp_tool", f"Doc not found: {file_name}")
            return {"error": "Google Doc not found", "user_id": user_id, "searched_for": file_name}
        
        file_id = files[0]["id"]
        
        if len(files) > 1:
            match_info = {
                "note": f"Found {len(files)} matching docs, appending to the first one: '{files[0]['name']}'"
            }
        else:
            match_info = {}
            
        result = await append_to_document(email=email, file_id=file_id, content=content)
        result.update(match_info)
        return result
        
    except Exception as e:
        log_action(user_id, "append_to_document_by_name", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def insert_text_at_position(email: str, file_id: str, content: str, index: int) -> dict:
    """
    Description:
        Insert text at a specific position in a Google Docs document.
    Args:
        email (str): The email of the user whose Google Doc to update.
        file_id (str): The ID of the Google Doc.
        content (str): The content to insert.
        index (int): The position index where to insert the content (1 = beginning).
    Returns:
        A dictionary indicating success or failure of the insert operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        creds = _get_credentials(user_id)
        drive_service = build("drive", "v3", credentials=creds)
        docs_service = build("docs", "v1", credentials=creds)
        
        file_metadata = drive_service.files().get(
            fileId=file_id,
            fields="name,mimeType"
        ).execute()
        
        if file_metadata.get("mimeType") != "application/vnd.google-apps.document":
            log_action(user_id, "insert_text_at_position", False, "mcp_tool", "File is not a Google Doc")
            return {"error": "File is not a Google Doc", "user_id": user_id, "file_id": file_id}

        requests_payload = [{
            'insertText': {
                'location': {'index': index},
                'text': content
            }
        }]
        
        docs_service.documents().batchUpdate(
            documentId=file_id,
            body={'requests': requests_payload}
        ).execute()
        
        log_action(user_id, "insert_text_at_position", True, "mcp_tool", f"File: {file_id}")
        return {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "name": file_metadata["name"],
            "message": "Text inserted successfully",
            "inserted_at_index": index,
            "inserted_length": len(content)
        }
        
    except Exception as e:
        log_action(user_id, "insert_text_at_position", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "file_id": file_id, "traceback": traceback.format_exc()}


#@mcp.tool()
async def rename_document(email: str, file_id: str, new_title: str) -> dict:
    """
    Description:
        Rename a Google Docs document.
    Args:
        email (str): The email of the user whose document to rename.
        file_id (str): The ID of the document to rename.
        new_title (str): The new title for the document.
    Returns:
        A dictionary indicating success or failure of the rename operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        creds = _get_credentials(user_id)
        drive_service = build("drive", "v3", credentials=creds)
        
        # Get current metadata
        file_metadata = drive_service.files().get(
            fileId=file_id,
            fields="name,mimeType"
        ).execute()
        
        if file_metadata.get("mimeType") != "application/vnd.google-apps.document":
            log_action(user_id, "rename_document", False, "mcp_tool", "File is not a Google Doc")
            return {"error": "File is not a Google Doc", "user_id": user_id, "file_id": file_id}
        
        old_title = file_metadata["name"]
        
        # Update the title
        updated_file = drive_service.files().update(
            fileId=file_id,
            body={"name": new_title},
            fields="name"
        ).execute()
        
        log_action(user_id, "rename_document", True, "mcp_tool", f"Renamed: {old_title} -> {new_title}")
        return {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "old_title": old_title,
            "new_title": new_title,
            "message": "Document renamed successfully"
        }
        
    except Exception as e:
        log_action(user_id, "rename_document", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "file_id": file_id, "traceback": traceback.format_exc()}


#@mcp.tool()
async def update_document_content(email: str, file_id: str, new_content: str) -> dict:
    """
    Description:
        Update the contents of a Google Docs document.
    Args:
        email (str): The email of the user whose Google Doc to update.
        file_id (str): The ID of the Google Doc to update.
        new_content (str): The new content to insert into the document.
    Returns:
        A dictionary indicating success or failure of the update operation.
    """
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
    """
    Description:
        Update the contents of a Google Docs document by searching for its name.
    Args:
        email (str): The email of the user whose Google Doc to update.
        file_name (str): The name of the Google Doc to update.
        new_content (str): The new content to insert into the document.
    Returns:
        A dictionary indicating success or failure of the update operation.
    """
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
            
        result = await update_document_content(email=email, file_id=file_id, new_content=new_content)
        result.update(match_info)
        return result
        
    except Exception as e:
        log_action(user_id, "update_document_by_name", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "email": email, "searched_for": file_name, "traceback": traceback.format_exc()}