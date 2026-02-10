import traceback
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from mcpserver.auth import verify_email, _get_credentials
from mcpserver.database import log_action, sanitize_drive_query


# --- HELPER FUNCTIONS ---

async def _get_spreadsheet_id_by_name(user_id: str, file_name: str) -> dict:
    """
    Helper function to find a spreadsheet ID by name.
    Returns a dict with either file_id and name, or an error.
    """
    try:
        creds = _get_credentials(user_id)
        service = build("drive", "v3", credentials=creds)
        
        safe_name = sanitize_drive_query(file_name)
        res = service.files().list(
            q=f"name = '{safe_name}' and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false",
            pageSize=5,
            fields="files(id,name)"
        ).execute()
        
        files = res.get("files", [])
        if not files:
            return {"error": "Spreadsheet not found", "searched_for": file_name}
        
        result = {
            "file_id": files[0]["id"],
            "name": files[0]["name"]
        }
        
        if len(files) > 1:
            result["note"] = f"Found {len(files)} matching spreadsheets, using the first one: '{files[0]['name']}'"
            result["other_matches"] = [{"id": f["id"], "name": f["name"]} for f in files[1:]]
        
        return result
        
    except Exception as e:
        return {"error": str(e), "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def read_spreadsheet(email: str, file_name: str, sheet_name: str = "Sheet1") -> dict:
    """
    Description:
        Read the contents of a Google Sheets spreadsheet. If sheet_name is provided, reads that sheet; otherwise reads the first sheet.
    Args:
        email (str): The email of the user whose spreadsheet to read.
        file_name (str): The name of the spreadsheet to read.
        sheet_name (str): Optional name of the specific sheet to read. Defaults, to Sheet1. 
    Returns:
        A dictionary containing the spreadsheet data or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "read_spreadsheet", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        sheets_service = build("sheets", "v4", credentials=creds)
        
        # Get spreadsheet metadata to find sheet names
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=file_id).execute()
        sheets = spreadsheet.get("sheets", [])
        
        if not sheets:
            return {"error": "Spreadsheet has no sheets", "user_id": user_id, "file_id": file_id}
        
        # Determine which sheet to read
        if sheet_name:
            target_sheet = sheet_name
        else:
            target_sheet = sheets[0]["properties"]["title"]
        
        # Read the sheet data
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=file_id,
            range=target_sheet
        ).execute()
        
        values = result.get("values", [])
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "spreadsheet_name": file_info["name"],
            "sheet_name": target_sheet,
            "row_count": len(values),
            "column_count": len(values[0]) if values else 0,
            "data": values
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "read_spreadsheet", True, "mcp_tool", f"Read sheet: {target_sheet} from {file_name}")
        return response
        
    except Exception as e:
        log_action(user_id, "read_spreadsheet", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def read_range(email: str, file_name: str, range_notation: str) -> dict:
    """
    Description:
        Read a specific range from a Google Sheets spreadsheet (e.g., "Sheet1!A1:C10" or just "A1:C10").
    Args:
        email (str): The email of the user whose spreadsheet to read.
        file_name (str): The name of the spreadsheet to read.
        range_notation (str): The range to read in A1 notation (e.g., "Sheet1!A1:C10" or "A1:C10").
    Returns:
        A dictionary containing the range data or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "read_range", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        sheets_service = build("sheets", "v4", credentials=creds)
        
        # Read the range
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=file_id,
            range=range_notation
        ).execute()
        
        values = result.get("values", [])
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "spreadsheet_name": file_info["name"],
            "range": range_notation,
            "row_count": len(values),
            "column_count": len(values[0]) if values else 0,
            "data": values
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "read_range", True, "mcp_tool", f"Read range {range_notation} from {file_name}")
        return response
        
    except Exception as e:
        log_action(user_id, "read_range", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def get_sheet_names(email: str, file_name: str) -> dict:
    """
    Description:
        Get a list of all sheet names in a Google Sheets spreadsheet.
    Args:
        email (str): The email of the user whose spreadsheet to analyze.
        file_name (str): The name of the spreadsheet.
    Returns:
        A dictionary containing the list of sheet names or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "get_sheet_names", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        sheets_service = build("sheets", "v4", credentials=creds)
        
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=file_id).execute()
        sheets = spreadsheet.get("sheets", [])
        
        sheet_info = []
        for sheet in sheets:
            props = sheet["properties"]
            sheet_info.append({
                "sheet_id": props["sheetId"],
                "title": props["title"],
                "index": props["index"],
                "row_count": props["gridProperties"].get("rowCount", 0),
                "column_count": props["gridProperties"].get("columnCount", 0)
            })
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "spreadsheet_name": file_info["name"],
            "sheet_count": len(sheet_info),
            "sheets": sheet_info
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "get_sheet_names", True, "mcp_tool", f"Got {len(sheet_info)} sheet names from {file_name}")
        return response
        
    except Exception as e:
        log_action(user_id, "get_sheet_names", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def get_spreadsheet_metadata(email: str, file_name: str) -> dict:
    """
    Description:
        Get metadata about a Google Sheets spreadsheet including sheet count, creation date, and other properties.
    Args:
        email (str): The email of the user whose spreadsheet to analyze.
        file_name (str): The name of the spreadsheet.
    Returns:
        A dictionary containing spreadsheet metadata.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "get_spreadsheet_metadata", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        drive_service = build("drive", "v3", credentials=creds)
        sheets_service = build("sheets", "v4", credentials=creds)
        
        # Get Drive metadata
        file_metadata = drive_service.files().get(
            fileId=file_id,
            fields="name,createdTime,modifiedTime,webViewLink,owners"
        ).execute()
        
        # Get Sheets metadata
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=file_id).execute()
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "title": spreadsheet.get("properties", {}).get("title"),
            "sheet_count": len(spreadsheet.get("sheets", [])),
            "created_time": file_metadata.get("createdTime"),
            "modified_time": file_metadata.get("modifiedTime"),
            "webViewLink": file_metadata.get("webViewLink"),
            "owners": file_metadata.get("owners", [])
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "get_spreadsheet_metadata", True, "mcp_tool", f"Got metadata for: {file_name}")
        return response
        
    except Exception as e:
        log_action(user_id, "get_spreadsheet_metadata", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def update_cell(email: str, file_name: str, cell_notation: str, value: str) -> dict:
    """
    Description:
        Update a single cell in a Google Sheets spreadsheet.
    Args:
        email (str): The email of the user whose spreadsheet to update.
        file_name (str): The name of the spreadsheet.
        cell_notation (str): The cell to update in A1 notation (e.g., "Sheet1!A1" or "A1").
        value (str): The value to set in the cell.
    Returns:
        A dictionary indicating success or failure of the update operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "update_cell", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        sheets_service = build("sheets", "v4", credentials=creds)
        
        # Update the cell
        body = {
            "values": [[value]]
        }
        
        result = sheets_service.spreadsheets().values().update(
            spreadsheetId=file_id,
            range=cell_notation,
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "spreadsheet_name": file_info["name"],
            "cell": cell_notation,
            "value": value,
            "updated_cells": result.get("updatedCells", 0),
            "message": "Cell updated successfully"
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "update_cell", True, "mcp_tool", f"Updated {cell_notation} in {file_name}")
        return response
        
    except Exception as e:
        log_action(user_id, "update_cell", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def update_range(email: str, file_name: str, range_notation: str, values: list) -> dict:
    """
    Description:
        Update a range of cells in a Google Sheets spreadsheet.
    Args:
        email (str): The email of the user whose spreadsheet to update.
        file_name (str): The name of the spreadsheet.
        range_notation (str): The range to update in A1 notation (e.g., "Sheet1!A1:C3" or "A1:C3").
        values (list): A 2D list of values to write (e.g., [["A1", "B1"], ["A2", "B2"]]).
    Returns:
        A dictionary indicating success or failure of the update operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "update_range", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        sheets_service = build("sheets", "v4", credentials=creds)
        
        # Update the range
        body = {
            "values": values
        }
        
        result = sheets_service.spreadsheets().values().update(
            spreadsheetId=file_id,
            range=range_notation,
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "spreadsheet_name": file_info["name"],
            "range": range_notation,
            "updated_cells": result.get("updatedCells", 0),
            "updated_rows": result.get("updatedRows", 0),
            "updated_columns": result.get("updatedColumns", 0),
            "message": "Range updated successfully"
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "update_range", True, "mcp_tool", f"Updated {range_notation} in {file_name}")
        return response
        
    except Exception as e:
        log_action(user_id, "update_range", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def append_row(email: str, file_name: str, values: list, sheet_name: str = "Sheet1") -> dict:
    """
    Description:
        Append a new row of data to the end of a sheet in a Google Sheets spreadsheet.
    Args:
        email (str): The email of the user whose spreadsheet to update.
        file_name (str): The name of the spreadsheet.
        values (list): A list of values to append as a new row (e.g., ["value1", "value2", "value3"]).
        sheet_name (str): Optional name of the sheet to append to. If not provided, appends to the first sheet.
    Returns:
        A dictionary indicating success or failure of the append operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "append_row", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        sheets_service = build("sheets", "v4", credentials=creds)
        
        # Determine which sheet to append to
        if not sheet_name:
            spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=file_id).execute()
            sheets = spreadsheet.get("sheets", [])
            if not sheets:
                return {"error": "Spreadsheet has no sheets", "user_id": user_id, "file_id": file_id}
            sheet_name = sheets[0]["properties"]["title"]
        
        # Append the row
        body = {
            "values": [values]
        }
        
        result = sheets_service.spreadsheets().values().append(
            spreadsheetId=file_id,
            range=sheet_name,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body
        ).execute()
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "spreadsheet_name": file_info["name"],
            "sheet_name": sheet_name,
            "updated_range": result.get("updates", {}).get("updatedRange"),
            "updated_rows": result.get("updates", {}).get("updatedRows", 0),
            "message": "Row appended successfully"
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "append_row", True, "mcp_tool", f"Appended row to {sheet_name} in {file_name}")
        return response
        
    except Exception as e:
        log_action(user_id, "append_row", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def clear_range(email: str, file_name: str, range_notation: str) -> dict:
    """
    Description:
        Clear the contents of a specific range in a Google Sheets spreadsheet.
    Args:
        email (str): The email of the user whose spreadsheet to update.
        file_name (str): The name of the spreadsheet.
        range_notation (str): The range to clear in A1 notation (e.g., "Sheet1!A1:C10" or "A1:C10").
    Returns:
        A dictionary indicating success or failure of the clear operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "clear_range", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        sheets_service = build("sheets", "v4", credentials=creds)
        
        # Clear the range
        result = sheets_service.spreadsheets().values().clear(
            spreadsheetId=file_id,
            range=range_notation,
            body={}
        ).execute()
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "spreadsheet_name": file_info["name"],
            "cleared_range": result.get("clearedRange"),
            "message": "Range cleared successfully"
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "clear_range", True, "mcp_tool", f"Cleared {range_notation} in {file_name}")
        return response
        
    except Exception as e:
        log_action(user_id, "clear_range", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def create_sheet(email: str, file_name: str, sheet_name: str) -> dict:
    """
    Description:
        Add a new sheet to an existing Google Sheets spreadsheet.
    Args:
        email (str): The email of the user whose spreadsheet to modify.
        file_name (str): The name of the spreadsheet.
        sheet_name (str): The name for the new sheet.
    Returns:
        A dictionary indicating success or failure of the create operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "create_sheet", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        sheets_service = build("sheets", "v4", credentials=creds)
        
        # Create the new sheet
        request_body = {
            "requests": [{
                "addSheet": {
                    "properties": {
                        "title": sheet_name
                    }
                }
            }]
        }
        
        result = sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=file_id,
            body=request_body
        ).execute()
        
        new_sheet_id = result["replies"][0]["addSheet"]["properties"]["sheetId"]
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "spreadsheet_name": file_info["name"],
            "sheet_name": sheet_name,
            "sheet_id": new_sheet_id,
            "message": "Sheet created successfully"
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "create_sheet", True, "mcp_tool", f"Created sheet '{sheet_name}' in {file_name}")
        return response
        
    except Exception as e:
        log_action(user_id, "create_sheet", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def delete_sheet(email: str, file_name: str, sheet_name: str) -> dict:
    """
    Description:
        Delete a sheet from a Google Sheets spreadsheet.
    Args:
        email (str): The email of the user whose spreadsheet to modify.
        file_name (str): The name of the spreadsheet.
        sheet_name (str): The name of the sheet to delete.
    Returns:
        A dictionary indicating success or failure of the delete operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "delete_sheet", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        sheets_service = build("sheets", "v4", credentials=creds)
        
        # Get the sheet ID
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=file_id).execute()
        sheets = spreadsheet.get("sheets", [])
        
        sheet_id = None
        for sheet in sheets:
            if sheet["properties"]["title"] == sheet_name:
                sheet_id = sheet["properties"]["sheetId"]
                break
        
        if sheet_id is None:
            return {"error": f"Sheet '{sheet_name}' not found in spreadsheet", "user_id": user_id, "file_id": file_id}
        
        # Delete the sheet
        request_body = {
            "requests": [{
                "deleteSheet": {
                    "sheetId": sheet_id
                }
            }]
        }
        
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=file_id,
            body=request_body
        ).execute()
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "spreadsheet_name": file_info["name"],
            "sheet_name": sheet_name,
            "message": "Sheet deleted successfully"
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "delete_sheet", True, "mcp_tool", f"Deleted sheet '{sheet_name}' from {file_name}")
        return response
        
    except Exception as e:
        log_action(user_id, "delete_sheet", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def rename_sheet(email: str, file_name: str, old_sheet_name: str, new_sheet_name: str) -> dict:
    """
    Description:
        Rename a sheet in a Google Sheets spreadsheet.
    Args:
        email (str): The email of the user whose spreadsheet to modify.
        file_name (str): The name of the spreadsheet.
        old_sheet_name (str): The current name of the sheet.
        new_sheet_name (str): The new name for the sheet.
    Returns:
        A dictionary indicating success or failure of the rename operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "rename_sheet", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        sheets_service = build("sheets", "v4", credentials=creds)
        
        # Get the sheet ID
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=file_id).execute()
        sheets = spreadsheet.get("sheets", [])
        
        sheet_id = None
        for sheet in sheets:
            if sheet["properties"]["title"] == old_sheet_name:
                sheet_id = sheet["properties"]["sheetId"]
                break
        
        if sheet_id is None:
            return {"error": f"Sheet '{old_sheet_name}' not found in spreadsheet", "user_id": user_id, "file_id": file_id}
        
        # Rename the sheet
        request_body = {
            "requests": [{
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "title": new_sheet_name
                    },
                    "fields": "title"
                }
            }]
        }
        
        sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=file_id,
            body=request_body
        ).execute()
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "spreadsheet_name": file_info["name"],
            "old_sheet_name": old_sheet_name,
            "new_sheet_name": new_sheet_name,
            "message": "Sheet renamed successfully"
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "rename_sheet", True, "mcp_tool", f"Renamed sheet '{old_sheet_name}' to '{new_sheet_name}' in {file_name}")
        return response
        
    except Exception as e:
        log_action(user_id, "rename_sheet", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def duplicate_sheet(email: str, file_name: str, sheet_name: str, new_sheet_name: str = None) -> dict:
    """
    Description:
        Duplicate a sheet within a Google Sheets spreadsheet.
    Args:
        email (str): The email of the user whose spreadsheet to modify.
        file_name (str): The name of the spreadsheet.
        sheet_name (str): The name of the sheet to duplicate.
        new_sheet_name (str): Optional name for the duplicated sheet. If not provided, will be "Copy of [sheet_name]".
    Returns:
        A dictionary indicating success or failure of the duplicate operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "duplicate_sheet", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        sheets_service = build("sheets", "v4", credentials=creds)
        
        # Get the sheet ID
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=file_id).execute()
        sheets = spreadsheet.get("sheets", [])
        
        sheet_id = None
        for sheet in sheets:
            if sheet["properties"]["title"] == sheet_name:
                sheet_id = sheet["properties"]["sheetId"]
                break
        
        if sheet_id is None:
            return {"error": f"Sheet '{sheet_name}' not found in spreadsheet", "user_id": user_id, "file_id": file_id}
        
        # Duplicate the sheet
        request_body = {
            "requests": [{
                "duplicateSheet": {
                    "sourceSheetId": sheet_id,
                    "newSheetName": new_sheet_name if new_sheet_name else f"Copy of {sheet_name}"
                }
            }]
        }
        
        result = sheets_service.spreadsheets().batchUpdate(
            spreadsheetId=file_id,
            body=request_body
        ).execute()
        
        duplicated_sheet = result["replies"][0]["duplicateSheet"]["properties"]
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "spreadsheet_name": file_info["name"],
            "original_sheet_name": sheet_name,
            "new_sheet_name": duplicated_sheet["title"],
            "new_sheet_id": duplicated_sheet["sheetId"],
            "message": "Sheet duplicated successfully"
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "duplicate_sheet", True, "mcp_tool", f"Duplicated sheet '{sheet_name}' in {file_name}")
        return response
        
    except Exception as e:
        log_action(user_id, "duplicate_sheet", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def create_spreadsheet(email: str, title: str, sheet_names: list = None) -> dict:
    """
    Description:
        Create a new Google Sheets spreadsheet.
    Args:
        email (str): The email of the user who will own the spreadsheet.
        title (str): The title of the new spreadsheet.
        sheet_names (list): Optional list of sheet names to create. If not provided, creates one sheet named "Sheet1".
    Returns:
        A dictionary containing the new spreadsheet's details or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        creds = _get_credentials(user_id)
        sheets_service = build("sheets", "v4", credentials=creds)
        
        # Prepare sheets
        sheets_to_create = []
        if sheet_names:
            for name in sheet_names:
                sheets_to_create.append({"properties": {"title": name}})
        else:
            sheets_to_create.append({"properties": {"title": "Sheet1"}})
        
        # Create the spreadsheet
        spreadsheet_body = {
            "properties": {"title": title},
            "sheets": sheets_to_create
        }
        
        spreadsheet = sheets_service.spreadsheets().create(body=spreadsheet_body).execute()
        
        log_action(user_id, "create_spreadsheet", True, "mcp_tool", f"Created: {title}")
        return {
            "success": True,
            "user_id": user_id,
            "spreadsheet_id": spreadsheet.get("spreadsheetId"),
            "title": title,
            "spreadsheet_url": spreadsheet.get("spreadsheetUrl"),
            "sheet_count": len(sheets_to_create),
            "message": "Spreadsheet created successfully"
        }
        
    except Exception as e:
        log_action(user_id, "create_spreadsheet", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}


#@mcp.tool()
async def delete_spreadsheet(email: str, file_name: str) -> dict:
    """
    Description:
        Delete a Google Sheets spreadsheet (moves it to trash).
    Args:
        email (str): The email of the user whose spreadsheet to delete.
        file_name (str): The name of the spreadsheet to delete.
    Returns:
        A dictionary indicating success or failure of the delete operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "delete_spreadsheet", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        name = file_info["name"]
        
        creds = _get_credentials(user_id)
        drive_service = build("drive", "v3", credentials=creds)
        
        drive_service.files().update(
            fileId=file_id,
            body={"trashed": True}
        ).execute()
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "name": name,
            "message": "Spreadsheet moved to trash successfully"
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "delete_spreadsheet", True, "mcp_tool", f"Deleted: {name}")
        return response
        
    except Exception as e:
        log_action(user_id, "delete_spreadsheet", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def rename_spreadsheet(email: str, file_name: str, new_title: str) -> dict:
    """
    Description:
        Rename a Google Sheets spreadsheet.
    Args:
        email (str): The email of the user whose spreadsheet to rename.
        file_name (str): The current name of the spreadsheet.
        new_title (str): The new title for the spreadsheet.
    Returns:
        A dictionary indicating success or failure of the rename operation.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "rename_spreadsheet", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
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
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "old_title": old_title,
            "new_title": new_title,
            "message": "Spreadsheet renamed successfully"
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "rename_spreadsheet", True, "mcp_tool", f"Renamed: {old_title} -> {new_title}")
        return response
        
    except Exception as e:
        log_action(user_id, "rename_spreadsheet", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def duplicate_spreadsheet(email: str, file_name: str, new_title: str = None) -> dict:
    """
    Description:
        Create a copy of a Google Sheets spreadsheet.
    Args:
        email (str): The email of the user whose spreadsheet to duplicate.
        file_name (str): The name of the spreadsheet to duplicate.
        new_title (str): Optional title for the duplicated spreadsheet. If not provided, will be "Copy of [file_name]".
    Returns:
        A dictionary containing the new spreadsheet's details or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "duplicate_spreadsheet", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        original_name = file_info["name"]
        
        creds = _get_credentials(user_id)
        drive_service = build("drive", "v3", credentials=creds)
        
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
        
        response = {
            "success": True,
            "user_id": user_id,
            "original_file_id": file_id,
            "new_file_id": copied_file["id"],
            "original_title": original_name,
            "new_title": copy_title,
            "webViewLink": copied_metadata.get("webViewLink"),
            "message": "Spreadsheet duplicated successfully"
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "duplicate_spreadsheet", True, "mcp_tool", f"Duplicated: {original_name}")
        return response
        
    except Exception as e:
        log_action(user_id, "duplicate_spreadsheet", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}


#@mcp.tool()
async def find_in_sheet(email: str, file_name: str, search_value: str, sheet_name: str = None) -> dict:
    """
    Description:
        Search for a specific value in a Google Sheets spreadsheet and return all matching cells.
    Args:
        email (str): The email of the user whose spreadsheet to search.
        file_name (str): The name of the spreadsheet to search.
        search_value (str): The value to search for.
        sheet_name (str): Optional name of the sheet to search. If not provided, searches the first sheet.
    Returns:
        A dictionary containing the matching cells or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        # Find the spreadsheet by name
        file_info = await _get_spreadsheet_id_by_name(user_id, file_name)
        if "error" in file_info:
            log_action(user_id, "find_in_sheet", False, "mcp_tool", f"Spreadsheet not found: {file_name}")
            return {**file_info, "user_id": user_id}
        
        file_id = file_info["file_id"]
        
        creds = _get_credentials(user_id)
        sheets_service = build("sheets", "v4", credentials=creds)
        
        # Determine which sheet to search
        if not sheet_name:
            spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=file_id).execute()
            sheets = spreadsheet.get("sheets", [])
            if not sheets:
                return {"error": "Spreadsheet has no sheets", "user_id": user_id, "file_id": file_id}
            sheet_name = sheets[0]["properties"]["title"]
        
        # Read all data from the sheet
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=file_id,
            range=sheet_name
        ).execute()
        
        values = result.get("values", [])
        
        # Search for the value
        matches = []
        for row_idx, row in enumerate(values):
            for col_idx, cell_value in enumerate(row):
                if str(cell_value) == str(search_value):
                    # Convert to A1 notation
                    col_letter = chr(65 + col_idx) if col_idx < 26 else chr(64 + col_idx // 26) + chr(65 + col_idx % 26)
                    cell_notation = f"{col_letter}{row_idx + 1}"
                    matches.append({
                        "cell": cell_notation,
                        "row": row_idx + 1,
                        "column": col_idx + 1,
                        "value": cell_value
                    })
        
        response = {
            "success": True,
            "user_id": user_id,
            "file_id": file_id,
            "spreadsheet_name": file_info["name"],
            "sheet_name": sheet_name,
            "search_value": search_value,
            "matches_found": len(matches),
            "matches": matches
        }
        
        # Add note about multiple matches if applicable
        if "note" in file_info:
            response["note"] = file_info["note"]
        
        log_action(user_id, "find_in_sheet", True, "mcp_tool", f"Found {len(matches)} matches for '{search_value}' in {file_name}")
        return response
        
    except Exception as e:
        log_action(user_id, "find_in_sheet", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "searched_for": file_name, "traceback": traceback.format_exc()}