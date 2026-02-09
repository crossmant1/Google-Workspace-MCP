from datetime import datetime, timedelta
import traceback
from typing import Optional, Dict
from googleapiclient.discovery import build
from mcp.server.fastmcp import Context

import mcpserver.auth as auth
from mcpserver.auth import verify_email
import mcpserver.database as database
import mcpserver.config as config

#@mcp.tool()
async def list_calendar_events(
    context: Context,
    max_results: int = 10,
    calendar_id: str = "primary",
    timezone: str = "America/New_York"
) -> dict:
    """
    Description: 
        List upcoming events from Google Calendar.
    Args:
        email (str): User's email address.
        max_results (int): Maximum number of events to retrieve.
        calendar_id (str): The ID of the calendar to fetch events from.
        timezone (str, optional): Timezone for event times. Defaults to America/New_York.
    Returns:
        dict: A dictionary containing the list of events or an error message.
    """
    email = context.request_context.request.headers.get("email")
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        from googleapiclient.discovery import build
        from datetime import datetime, timezone as tz

        creds = auth._get_credentials(user_id)
        service = build("calendar", "v3", credentials=creds)
        
        now = datetime.now(tz.utc).isoformat()
        
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
            
        database.log_action(user_id, "list_calendar_events", True, "mcp_tool", f"Found {len(event_list)} events")
        return {
            "success": True,
            "user_id": user_id,
            "count": len(event_list),
            "calendar_id": calendar_id,
            "events": event_list
        }
    except Exception as e:
        database.log_action(user_id, "list_calendar_events", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}

#@mcp.tool()
async def create_calendar_event(
    email: str,
    summary: str,
    start_time: str,
    end_time: str,
    description: str = "",
    location: str = "",
    attendees: str = "",
    calendar_id: str = "primary",
    timezone: str = "America/New_York"
) -> dict:
    """
    Description:
        Create a new event in Google Calendar.
    Args:
        email (str): User's email address.
        summary (str): Event title.
        start_time (str): Event start time in RFC3339 format.
        end_time (str): Event end time in RFC3339 format.
        description (str): Event description.
        location (str): Event location.
        attendees (str): Comma-separated list of attendee email addresses.
        calendar_id (str): The ID of the calendar to add the event to.
        timezone (str, optional): Timezone for event times. Defaults to America/New_York.
    Returns:
        dict: A dictionary containing the created event details or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        from googleapiclient.discovery import build
        
        # Use provided timezone or default
        tz = timezone or config.DEFAULT_TIMEZONE
        
        creds = auth._get_credentials(user_id)
        service = build("calendar", "v3", credentials=creds)
        
        event = {
            "summary": summary,
            "description": description,
            "location": location,
        }
        
        # Handle all-day vs. dateTime
        if "T" in start_time:
            event["start"] = {"dateTime": start_time, "timeZone": tz}
        else:
            event["start"] = {"date": start_time}
            
        if "T" in end_time:
            event["end"] = {"dateTime": end_time, "timeZone": tz}
        else:
            event["end"] = {"date": end_time}
            
        if attendees:
            event["attendees"] = [{"email": attendee_email.strip()} for attendee_email in attendees.split(",")]
            
        created_event = service.events().insert(
            calendarId=calendar_id,
            body=event,
            sendUpdates="all"
        ).execute()
        
        database.log_action(user_id, "create_calendar_event", True, "mcp_tool", f"Event: {summary}")
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
        database.log_action(user_id, "create_calendar_event", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}

#@mcp.tool()
async def update_calendar_event(
    email: str,
    event_id: str,
    summary: str = "",
    start_time: str = "",
    end_time: str = "",
    description: str = "",
    location: str = "",
    calendar_id: str = "primary",
    timezone: str = "America/New_York"
) -> dict:
    """
    Description:
        Update an existing event in Google Calendar.
    Args:
        email (str): User's email address.
        event_id (str): The ID of the event to update.
        summary (str): Updated event title.
        start_time (str): Updated event start time in RFC3339 format.
        end_time (str): Updated event end time in RFC3339 format.
        description (str): Updated event description.
        location (str): Updated event location.
        calendar_id (str): The ID of the calendar containing the event.
        timezone (str, optional): Timezone for event times. Defaults to America/New_York.
    Returns:
        dict: A dictionary containing the updated event details or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        from googleapiclient.discovery import build
        
        # Use provided timezone or default
        tz = timezone or config.DEFAULT_TIMEZONE
        
        creds = auth._get_credentials(user_id)
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
                event["start"] = {"dateTime": start_time, "timeZone": tz}
            else:
                event["start"] = {"date": start_time}
        
        if end_time:
            if "T" in end_time:
                event["end"] = {"dateTime": end_time, "timeZone": tz}
            else:
                event["end"] = {"date": end_time}
        
        updated_event = service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=event
        ).execute()
        
        database.log_action(user_id, "update_calendar_event", True, "mcp_tool", f"Event: {event_id}")
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
        database.log_action(user_id, "update_calendar_event", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "event_id": event_id, "traceback": traceback.format_exc()}

#@mcp.tool()
async def delete_calendar_event(
    email: str,
    event_id: str,
    calendar_id: str = "primary"
) -> dict:
    """
    Description:
        Delete an event from Google Calendar.
    Args:
        email (str): User's email address.
        event_id (str): The ID of the event to delete.
        calendar_id (str): The ID of the calendar containing the event.
    Returns:
        dict: A dictionary indicating success or containing an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        from googleapiclient.discovery import build
        
        creds = auth._get_credentials(user_id)
        service = build("calendar", "v3", credentials=creds)
        
        service.events().delete(
            calendarId=calendar_id,
            eventId=event_id
        ).execute()
        
        database.log_action(user_id, "delete_calendar_event", True, "mcp_tool", f"Event: {event_id}")
        return {
            "success": True,
            "user_id": user_id,
            "event_id": event_id,
            "message": "Event deleted successfully"
        }
    except Exception as e:
        database.log_action(user_id, "delete_calendar_event", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "event_id": event_id, "traceback": traceback.format_exc()}

#@mcp.tool()
async def search_calendar_events(
    email: str,
    query: str,
    max_results: int = 10,
    calendar_id: str = "primary"
) -> dict:
    """
    Description:
        Search for events in Google Calendar matching a query.
    Args:
        email (str): User's email address.
        query (str): Search query string.
        max_results (int): Maximum number of events to retrieve.
        calendar_id (str): The ID of the calendar to search events in.
    Returns:
        dict: A dictionary containing the list of matching events or an error message.
    """
    user_id = await verify_email(email)
    if not user_id:
        return {"error": "Invalid email or user not authenticated with Google."}

    try:
        from googleapiclient.discovery import build
        from datetime import datetime, timezone

        creds = auth._get_credentials(user_id)
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

        database.log_action(user_id, "search_calendar_events", True, "mcp_tool", f"Query: {query}, Found: {len(event_list)}")
        return {
            "success": True,
            "user_id": user_id,
            "count": len(event_list),
            "query": query,
            "calendar_id": calendar_id,
            "events": event_list
        }
    except Exception as e:
        database.log_action(user_id, "search_calendar_events", False, "mcp_tool", str(e))
        return {"error": str(e), "user_id": user_id, "traceback": traceback.format_exc()}