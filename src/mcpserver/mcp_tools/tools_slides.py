import traceback
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from mcpserver.auth import verify_email, _get_credentials
from mcpserver.database import log_action, sanitize_drive_query


# --- HELPER FUNCTIONS ---

async def _get_presentation_id_by_name(user_id: str, file_name: str) -> dict:
    """
    Helper function to find a presentation ID by name.
    Returns a dict with either file_id and name, or an error.
    """
    try:
        creds = _get_credentials(user_id)
        service = build("drive", "v3", credentials=creds)
        
        safe_name = sanitize_drive_query(file_name)
        res = service.files().list(
            q=f"name = '{safe_name}' and mimeType = 'application/vnd.google-apps.presentation' and trashed = false",
            pageSize=5,
            fields="files(id,name)"
        ).execute()
        
        files = res.get("files", [])
        if not files:
            return {"error": "Presentation not found", "searched_for": file_name}
        
        result = {
            "file_id": files[0]["id"],
            "name": files[0]["name"]
        }
        
        if len(files) > 1:
            result["note"] = f"Found {len(files)} matching presentations, using the first one: '{files[0]['name']}'"
            result["other_matches"] = [{"id": f["id"], "name": f["name"]} for f in files[1:]]
        
        return result
        
    except Exception as e:
        return {"error": str(e), "searched_for": file_name, "traceback": traceback.format_exc()}


async def _read_presentation_helper(user_id: str, file_id: str) -> dict:
    """
    Helper function to read presentation content by file ID.
    """
    try:
        creds = _get_credentials(user_id)
        drive_service = build("drive", "v3", credentials=creds)
        slides_service = build("slides", "v1", credentials=creds)
        
        # Verify it's a presentation
        file_metadata = drive_service.files().get(
            fileId=file_id,
            fields="name,mimeType,webViewLink"
        ).execute()
        
        if file_metadata.get("mimeType") != "application/vnd.google-apps.presentation":
            return {"error": "File is not a Google Slides presentation", "file_id": file_id}
        
        # Get presentation content
        presentation = slides_service.presentations().get(presentationId=file_id).execute()
        
        slides_data = []
        for slide in presentation.get("slides", []):
            slide_info = {
                "slide_id": slide.get("objectId"),
                "slide_index": len(slides_data),
                "text_content": []
            }
            
            # Extract text from all page elements
            for element in slide.get("pageElements", []):
                if "shape" in element:
                    shape = element["shape"]
                    if "text" in shape:
                        text_content = shape["text"]
                        for text_element in text_content.get("textElements", []):
                            if "textRun" in text_element:
                                content = text_element["textRun"].get("content", "").strip()
                                if content:
                                    slide_info["text_content"].append(content)
                
                # Also extract from tables
                elif "table" in element:
                    table = element["table"]
                    for row in table.get("tableRows", []):
                        for cell in row.get("tableCells", []):
                            if "text" in cell:
                                for text_element in cell["text"].get("textElements", []):
                                    if "textRun" in text_element:
                                        content = text_element["textRun"].get("content", "").strip()
                                        if content:
                                            slide_info["text_content"].append(content)
            
            slides_data.append(slide_info)
        
        return {
            "success": True,
            "file_id": file_id,
            "title": presentation.get("title"),
            "webViewLink": file_metadata.get("webViewLink"),
            "total_slides": len(slides_data),
            "slides": slides_data
        }
        
    except Exception as e:
        return {"error": str(e), "file_id": file_id, "traceback": traceback.format_exc()}


#@mcp.tool()
async def read_presentation(email: str, file_name: str) -> dict:
    """
    Description:
        Read the contents of a Google Slides presentation by name, including all slides and their text content.
    Args:
        email (str): The email of the user whose presentation to read.
        file_name (str): The name of the presentation to read.
    Returns:
        A dictionary containing the presentation structure and content or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the presentation by name
        file_info = await _get_presentation_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "read_presentation", False, "mcp_tool", f"Presentation not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        # Read the presentation content
        result = await _read_presentation_helper(user_id, file_id)
        result["user_id"] = user_id
        
        
        if "note" in file_info:
            result["note"] = file_info["note"]
        if "other_matches" in file_info:
            result["other_matches"] = file_info["other_matches"]
        
        log_action(user_id, "read_presentation", True, "mcp_tool", f"Read presentation: {file_name}")
        return result
        
    except Exception as e:
        log_action(user_id, "read_presentation", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def list_slides(email: str, file_name: str) -> dict:
    """
    Description:
        List all slides in a presentation with basic information (titles and slide IDs).
    Args:
        email (str): The email of the user whose presentation to analyze.
        file_name (str): The name of the presentation.
    Returns:
        A dictionary containing a list of slides with their basic information.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the presentation by name
        file_info = await _get_presentation_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "list_slides", False, "mcp_tool", f"Presentation not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        slides_service = build("slides", "v1", credentials=creds)
        
        presentation = slides_service.presentations().get(presentationId=file_id).execute()
        
        slides_summary = []
        for idx, slide in enumerate(presentation.get("slides", [])):
            # Try to get the first text element as a "title"
            title = None
            for element in slide.get("pageElements", []):
                if "shape" in element and "text" in element["shape"]:
                    for text_element in element["shape"]["text"].get("textElements", []):
                        if "textRun" in text_element:
                            title = text_element["textRun"].get("content", "").strip()
                            if title:
                                break
                    if title:
                        break
            
            slides_summary.append({
                "slide_number": idx + 1,
                "slide_id": slide.get("objectId"),
                "title": title or "(No title)"
            })
        
        result = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "presentation_title": presentation.get("title"),
            "total_slides": len(slides_summary),
            "slides": slides_summary
        }
        
        
        if "note" in file_info:
            result["note"] = file_info["note"]
        
        log_action(user_id, "list_slides", True, "mcp_tool", f"Listed {len(slides_summary)} slides")
        return result
        
    except Exception as e:
        log_action(user_id, "list_slides", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def delete_presentation(email: str, file_name: str) -> dict:
    """
    Description:
        Delete a Google Slides presentation by name (moves it to trash).
    Args:
        email (str): The email of the user whose presentation to delete.
        file_name (str): The name of the presentation to delete.
    Returns:
        A dictionary indicating success or failure of the delete operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the presentation by name
        file_info = await _get_presentation_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "delete_presentation", False, "mcp_tool", f"Presentation not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        name = file_info["name"]
        
        creds = _get_credentials(user_id)
        drive_service = build("drive", "v3", credentials=creds)
        
        drive_service.files().update(
            fileId=file_id,
            body={"trashed": True}
        ).execute()
        
        result = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "name": name,
            "message": "Presentation moved to trash successfully"
        }
        
        
        if "note" in file_info:
            result["note"] = file_info["note"]
        
        log_action(user_id, "delete_presentation", True, "mcp_tool", f"Deleted: {name}")
        return result
        
    except Exception as e:
        log_action(user_id, "delete_presentation", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def rename_presentation(email: str, file_name: str, new_title: str) -> dict:
    """
    Description:
        Rename a Google Slides presentation.
    Args:
        email (str): The email of the user whose presentation to rename.
        file_name (str): The current name of the presentation to rename.
        new_title (str): The new title for the presentation.
    Returns:
        A dictionary indicating success or failure of the rename operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the presentation by name
        file_info = await _get_presentation_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "rename_presentation", False, "mcp_tool", f"Presentation not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        old_title = file_info["name"]
        
        creds = _get_credentials(user_id)
        drive_service = build("drive", "v3", credentials=creds)
        
        drive_service.files().update(
            fileId=file_id,
            body={"name": new_title},
            fields="name"
        ).execute()
        
        result = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "old_title": old_title,
            "new_title": new_title,
            "message": "Presentation renamed successfully"
        }
        
        
        if "note" in file_info:
            result["note"] = file_info["note"]
        
        log_action(user_id, "rename_presentation", True, "mcp_tool", f"Renamed: {old_title} -> {new_title}")
        return result
        
    except Exception as e:
        log_action(user_id, "rename_presentation", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def duplicate_presentation(email: str, file_name: str, new_title: str = None) -> dict:
    """
    Description:
        Create a copy of a Google Slides presentation.
    Args:
        email (str): The email of the user whose presentation to duplicate.
        file_name (str): The name of the presentation to duplicate.
        new_title (str): Optional title for the duplicated presentation. If not provided, will be "Copy of [file_name]".
    Returns:
        A dictionary containing the new presentation's details or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the presentation by name
        file_info = await _get_presentation_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "duplicate_presentation", False, "mcp_tool", f"Presentation not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        original_name = file_info["name"]
        
        creds = _get_credentials(user_id)
        drive_service = build("drive", "v3", credentials=creds)
        
        # Use new_title if provided, otherwise use "Copy of [file_name]" where file_name is the parameter
        copy_title = new_title if new_title else f"Copy of {file_name}"
        
        copied_file = drive_service.files().copy(
            fileId=file_id,
            body={"name": copy_title}
        ).execute()
        
        # Get web view link
        copied_metadata = drive_service.files().get(
            fileId=copied_file["id"],
            fields="webViewLink"
        ).execute()
        
        result = {
            "success": True,
            "user_id": user_id,
            "original_file_id": file_id,
            "new_file_id": copied_file["id"],
            "original_title": original_name,
            "new_title": copy_title,
            "webViewLink": copied_metadata.get("webViewLink"),
            "message": "Presentation duplicated successfully"
        }
        
        
        if "note" in file_info:
            result["note"] = file_info["note"]
        
        log_action(user_id, "duplicate_presentation", True, "mcp_tool", f"Duplicated: {original_name}")
        return result
        
    except Exception as e:
        log_action(user_id, "duplicate_presentation", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def get_presentation_metadata(email: str, file_name: str) -> dict:
    """
    Description:
        Get metadata about a Google Slides presentation including slide count, dimensions, and other properties.
    Args:
        email (str): The email of the user whose presentation to analyze.
        file_name (str): The name of the presentation.
    Returns:
        A dictionary containing presentation metadata.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the presentation by name
        file_info = await _get_presentation_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "get_presentation_metadata", False, "mcp_tool", f"Presentation not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        drive_service = build("drive", "v3", credentials=creds)
        slides_service = build("slides", "v1", credentials=creds)
        
        file_metadata = drive_service.files().get(
            fileId=file_id,
            fields="name,mimeType,createdTime,modifiedTime,webViewLink,owners"
        ).execute()
        
        presentation = slides_service.presentations().get(presentationId=file_id).execute()
        
        result = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "title": presentation.get("title"),
            "slide_count": len(presentation.get("slides", [])),
            "page_size": presentation.get("pageSize"),
            "created_time": file_metadata.get("createdTime"),
            "modified_time": file_metadata.get("modifiedTime"),
            "webViewLink": file_metadata.get("webViewLink"),
            "owners": file_metadata.get("owners", [])
        }
        
        
        if "note" in file_info:
            result["note"] = file_info["note"]
        
        log_action(user_id, "get_presentation_metadata", True, "mcp_tool", f"Got metadata for: {file_name}")
        return result
        
    except Exception as e:
        log_action(user_id, "get_presentation_metadata", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def export_presentation_as_pdf(email: str, file_name: str) -> dict:
    """
    Description:
        Export a Google Slides presentation as a PDF and get the download link.
    Args:
        email (str): The email of the user whose presentation to export.
        file_name (str): The name of the presentation to export.
    Returns:
        A dictionary containing export information and download instructions.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the presentation by name
        file_info = await _get_presentation_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "export_presentation_as_pdf", False, "mcp_tool", f"Presentation not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        name = file_info["name"]
        
        # Generate export link
        export_link = f"https://docs.google.com/presentation/d/{file_id}/export/pdf"
        
        result = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "presentation_name": name,
            "export_link": export_link,
            "message": "PDF export link generated. You can download the PDF from the provided link."
        }
        
        
        if "note" in file_info:
            result["note"] = file_info["note"]
        
        log_action(user_id, "export_presentation_as_pdf", True, "mcp_tool", f"Generated PDF export link for: {name}")
        return result
        
    except Exception as e:
        log_action(user_id, "export_presentation_as_pdf", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}