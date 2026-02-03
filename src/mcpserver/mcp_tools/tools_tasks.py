import traceback
from typing import Optional, Dict
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from mcpserver.auth import verify_email, _get_credentials
from mcpserver.database import log_action, get_user_tokens
from mcpserver.mcp_tools.tools_gmail import search_emails


#@mcp.tool()
async def list_task_lists(email: str) -> dict:
    """
    Description: 
        Tool that lists the current Tasks divied into lists in a user's Google Tasks account. 
    Args:
        email (str): The email address of the user whose tasks are to be listed.
    Returns:
        dict: A dictionary containing the list of task lists or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

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

#@mcp.tool()
async def list_tasks(
    email: str,
    task_list_id: str = "@default",
    max_results: int = 20
) -> dict:
    """
    Description::
        Tool that lists the tasks in a specified Google Tasks list for a user.
    Args:
        email (str): The email address of the user whose tasks are to be listed.
        task_list_id (str): The ID of the task list to retrieve tasks from. Defaults to "@default".
        max_results (int): The maximum number of tasks to retrieve. Defaults to 20.
    Returns:
        dict: A dictionary containing the list of tasks or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

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

#@mcp.tool()
async def create_task(
    email: str,
    title: str,
    notes: str = "",
    due: str = "",
    task_list_id: str = "@default"
) -> dict:
    """
    Description::
        Tool that creates a new task in a specified Google Tasks list for a user.
    Args:
        email (str): The email address of the user whose tasks are to be listed.
        title (str): The title of the task to create
        notes (str): The notes for the task. Defaults to an empty string.
        due (str): The due date for the task. Defaults to an empty string, in RFC3339 format. The time should always be midnight. 
        task_list_id (str): The ID of the task list to create the task in. Defaults to "@default".
    Returns:
        dict: A dictionary containing the created task or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

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

#@mcp.tool()
async def create_task_from_email(email: str, email_id: str, task_list_id: str = "@default", include_snippet: bool = True, include_sender: bool = True, mark_email_done: bool = False) -> dict:
    """
    Description::
        Tool that creates a new task from an emailin a specified Google Tasks list for a user.
    Args:
        email (str): The email address of the user whose tasks are to be listed.
        email_id (str): The ID of the email to create a task from.
        task_list_id (str): The ID of the task list to create the task in. Defaults to "@default".
        include_snippet (bool): Whether to include the email snippet in the task notes. Defaults to True.
        include_sender (bool): Whether to include sender information in the task notes. Defaults to True.
        mark_email_done (bool): Whether to mark the email as read after creating the task. Defaults to False.
    Returns:
        dict: A dictionary containing the created task or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        from googleapiclient.discovery import build

        creds = _get_credentials(user_id)
        gmail_service = build("gmail", "v1", credentials=creds)
        tasks_service = build("tasks", "v1", credentials=creds)
        
        # Get email details with full format to extract more info
        try:
            message = gmail_service.users().messages().get(
                userId="me",
                id=email_id,
                format="full"
            ).execute()
        except HttpError as e:
            if e.resp.status == 404:
                log_action(user_id, "create_task_from_email", False, "mcp_tool", f"Email not found: {email_id}")
                return {
                    "success": False,
                    "error": f"Email not found with ID: {email_id}",
                    "hint": "Please verify the email ID is correct. Use list_emails or search_emails to get valid email IDs.",
                    "user_id": user_id,
                    "email_id": email_id
                }
            else:
                raise
        
        # Extract comprehensive headers
        headers = {h["name"]: h["value"] for h in message.get("payload", {}).get("headers", [])}
        
        subject = headers.get("Subject", "(No subject)")
        from_email = headers.get("From", "Unknown sender")
        date = headers.get("Date", "")
        to_email = headers.get("To", "")
        snippet = message.get("snippet", "")
        
        # Build task title (match Gmail's format: subject line)
        task_title = subject
        
        # Build comprehensive task notes
        task_notes_parts = []
        
        if include_sender:
            task_notes_parts.append(f"From: {from_email}")
            if to_email:
                task_notes_parts.append(f"To: {to_email}")
            if date:
                task_notes_parts.append(f"Date: {date}")
        
        if include_snippet and snippet:
            task_notes_parts.append("")  # Empty line for spacing
            task_notes_parts.append(snippet)
        
        # Always add email link (this is key to the Gmail integration)
        task_notes_parts.append("")
        email_link = f"https://mail.google.com/mail/u/0/#inbox/{email_id}"
        task_notes_parts.append(f"View email: {email_link}")
        
        task_notes = "\n".join(task_notes_parts)
        
        # Create the task
        task = {
            "title": task_title,
            "notes": task_notes
        }
        
        result = tasks_service.tasks().insert(
            tasklist=task_list_id,
            body=task
        ).execute()
        
        # Optionally mark email as read
        response_data = {
            "success": True,
            "user_id": user_id,
            "task_id": result["id"],
            "title": result["title"],
            "notes": result.get("notes", ""),
            "email_id": email_id,
            "email_subject": subject,
            "message": "Task created from email successfully"
        }
        
        if mark_email_done:
            try:
                gmail_service.users().messages().modify(
                    userId="me",
                    id=email_id,
                    body={"removeLabelIds": ["UNREAD"]}
                ).execute()
                response_data["email_marked_read"] = True
            except Exception as e:
                response_data["email_mark_warning"] = f"Task created but couldn't mark email as read: {str(e)}"
        
        log_action(user_id, "create_task_from_email", True, "mcp_tool", f"Email: {email_id} -> Task: {result['id']}")
        return response_data
        
    except HttpError as e:
        log_action(user_id, "create_task_from_email", False, "mcp_tool", f"HttpError: {str(e)}")
        return {
            "success": False,
            "error": f"Gmail API error: {e.resp.status} - {e.resp.reason}",
            "details": str(e),
            "user_id": user_id,
            "email_id": email_id,
            "traceback": traceback.format_exc()
        }
    except Exception as e:
        log_action(user_id, "create_task_from_email", False, "mcp_tool", str(e))
        return {
            "success": False,
            "error": str(e), 
            "user_id": user_id,
            "email_id": email_id, 
            "traceback": traceback.format_exc()
        }
    
#@mcp.tool()
async def add_emails_to_tasks(
    email: str,
    email_ids: str,
    task_list_id: str = "@default",
    mark_emails_done: bool = False
) -> dict:
    """
    Description:
        Tool that creates tasks from multiple emails in a specified Google Tasks list for a user.
    Args:
        email (str): The email address of the user whose tasks are to be listed.
        email_ids (str): Comma-separated string of email IDs to create tasks from.
        task_list_id (str): The ID of the task list to create the tasks in. Defaults to "@default".
        mark_emails_done (bool): Whether to mark the emails as read after creating the tasks. Defaults to False.
    Returns:
        dict: A dictionary containing the results of the task creation process.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    # Split email IDs
    ids_list = [id.strip() for id in email_ids.split(",") if id.strip()]
    
    if not ids_list:
        log_action(user_id, "add_emails_to_tasks", False, "mcp_tool", "No valid email IDs provided")
        return {
            "success": False,
            "error": "No valid email IDs provided",
            "user_id": user_id
        }
    
    results = []
    success_count = 0
    error_count = 0
    
    # Process each email
    for email_id in ids_list:
        result = await create_task_from_email(
            email=email,
            email_id=email_id,
            task_list_id=task_list_id,
            mark_email_done=mark_emails_done
        )
        
        if result.get("success"):
            success_count += 1
        else:
            error_count += 1
        
        results.append({
            "email_id": email_id,
            "result": result
        })
    
    log_action(user_id, "add_emails_to_tasks", True, "mcp_tool", 
               f"Processed {len(ids_list)} emails: {success_count} success, {error_count} errors")
    
    return {
        "success": success_count > 0,
        "user_id": user_id,
        "total_processed": len(ids_list),
        "success_count": success_count,
        "error_count": error_count,
        "results": results
    }


#@mcp.tool()
async def create_task_from_email_search(
    email: str,
    search_query: str,
    max_emails: int = 5,
    task_list_id: str = "@default",
    mark_emails_done: bool = False
) -> dict:
    """
    Description:
        Tool that searches for emails and creates tasks from all matching results.
    Args:
        email (str): The email address of the user whose tasks are to be listed.
        search_query (str): The Gmail search query to find matching emails.
        max_emails (int): Maximum number of emails to process. Defaults to 5.
        task_list_id (str): The ID of the task list to create the tasks in. Defaults to "@default".
        mark_emails_done (bool): Whether to mark the emails as read after creating the tasks. Defaults to False.
    Returns:
        dict: A dictionary containing the results of the task creation process.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    # First, search for emails
    max_emails = min(max_emails, 20)
    search_result = await search_emails(email=email, query=search_query, max_results=max_emails)
    
    if not search_result.get("success"):
        log_action(user_id, "create_task_from_email_search", False, "mcp_tool", 
                   f"Search failed: {search_query}")
        return {
            "success": False,
            "error": "Failed to search emails",
            "user_id": user_id,
            "details": search_result
        }
    
    emails = search_result.get("emails", [])
    
    if not emails:
        log_action(user_id, "create_task_from_email_search", False, "mcp_tool", 
                   f"No emails found: {search_query}")
        return {
            "success": False,
            "error": f"No emails found matching query: {search_query}",
            "user_id": user_id,
            "query": search_query
        }
    
    # Extract email IDs
    email_ids = [email["id"] for email in emails]
    
    # Use bulk add function
    result = await add_emails_to_tasks(
        email=email,
        email_ids=",".join(email_ids),
        task_list_id=task_list_id,
        mark_emails_done=mark_emails_done
    )
    
    result["search_query"] = search_query
    result["emails_found"] = len(emails)
    
    log_action(user_id, "create_task_from_email_search", True, "mcp_tool", 
               f"Query: {search_query}, Found: {len(emails)}, Created: {result.get('success_count')}")
    
    return result

#@mcp.tool()
async def update_task(
    email: str,
    task_id: str,
    title: str = "",
    notes: str = "",
    due: str = "",
    task_list_id: str = "@default"
) -> dict:
    """
    Description:
        Tool that updates an existing task in a specified Google Tasks list for a user.
    Args:  
        email (str): The email address of the user whose tasks are to be listed.
        task_id (str): The ID of the task to update.
        title (str): The new title for the task. Defaults to an empty string.
        notes (str): The new notes for the task. Defaults to an empty string.
        due (str): The new due date for the task. Defaults to an empty string.
        task_list_id (str): The ID of the task list containing the task. Defaults to "@default".
    Returns:
        dict: A dictionary containing the updated task or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

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

#@mcp.tool()
async def complete_task(
    email: str,
    task_id: str,
    task_list_id: str = "@default"
) -> dict:
    """
    Description:
        Tool that marks a task as completed in a specified Google Tasks list for a user.
    Args:
        email (str): The email address of the user whose tasks are to be listed.
        task_id (str): The ID of the task to mark as completed.
        task_list_id (str): The ID of the task list containing the task. Defaults to "@default".
    Returns:
        dict: A dictionary containing the completion status or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

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

#@mcp.tool()
async def delete_task(
    email: str,
    task_id: str,
    task_list_id: str = "@default"
) -> dict:
    """
    Description:
        Tool that deletes a task from a specified Google Tasks list for a user.
    Args:
        email (str): The email address of the user whose tasks are to be listed.
        task_id (str): The ID of the task to delete.
        task_list_id (str): The ID of the task list containing the task. Defaults to "@default".
    Returns:
        dict: A dictionary containing the deletion status or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

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

#@mcp.tool()
async def get_auth_status(email: str) -> dict:
    """
    Description:
        Tool that checks the authentication status and returns user information.
    Args:
        email (str): The email address of the user whose authentication status is to be checked.
    Returns:
        dict: A dictionary containing the authentication status or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"authenticated": False, "error": "Invalid email or user not authenticated with Google."}
    
    try:
        token_data = get_user_tokens(user_id)
        if not token_data:
            return {
                "authenticated": False,
                "error": "No tokens found for user"
            }
        
        # Convert datetime to string for JSON serialization
        token_expiry = token_data.get("token_expiry")
        expiry_str = token_expiry.isoformat() if token_expiry else None
        
        return {
            "authenticated": True,
            "user_id": user_id,
            "scopes": token_data.get("scopes", []),
            "token_expiry": expiry_str
        }
    except Exception as e:
        log_action(user_id, "get_auth_status", False, "mcp_tool", str(e))
        return {
            "authenticated": False,
            "error": f"Error retrieving auth status: {str(e)}"
        }