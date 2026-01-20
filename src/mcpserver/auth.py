import traceback
import urllib.parse
from datetime import datetime
from typing import Optional
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import database
import config

async def verify_email(email: Optional[str]) -> Optional[str]:
    """Verify email and return user_id if user exists and has valid tokens"""
    if not email:
        return None
    
    # Sanitize email
    email = email.lower().strip()
    
    # Get user by email
    user = database.get_user_by_email(email)
    if not user:
        return None
    
    # Check if user is active
    if not user.get("is_active"):
        return None
    
    return user["user_id"]

def get_user_id_by_email(email: str) -> Optional[str]:
    """Get user_id from email - lightweight lookup"""
    if not email:
        return None
    
    email = email.lower().strip()
    user = database.get_user_by_email(email)
    return user["user_id"] if user else None

async def check_google_auth(email: str) -> dict:
    """
    Check if a user has authenticated with Google.
    Returns authentication status and auth_url if needed.
    The AI Agent should call this first before using other tools.
    """
    if not email:
        return {"error": "Email is required"}
    
    # Sanitize email
    email = email.lower().strip()
    
    try:
        # Check if user exists
        user = database.get_user_by_email(email)
        
        if not user:
            # User doesn't exist - need to complete OAuth
            auth_url = f"{config.REDIRECT_URI.rsplit('/', 1)[0]}/auth?email={urllib.parse.quote(email)}"
            
            database.log_action("N/A", "check_google_auth", False, "mcp_tool", f"New user: {email}")
            return {
                "authenticated": False,
                "email": email,
                "message": "User not found. Please complete Google OAuth authentication.",
                "auth_url": auth_url,
                "next_step": "User must visit auth_url to grant Google permissions"
            }
        
        # User exists - check for valid tokens
        user_id = user["user_id"]
        token_data = database.get_user_tokens(user_id)
        
        if not token_data:
            # User exists but no tokens
            auth_url = f"{config.REDIRECT_URI.rsplit('/', 1)[0]}/auth?email={urllib.parse.quote(email)}"
            
            database.log_action(user_id, "check_google_auth", False, "mcp_tool", f"No tokens: {email}")
            return {
                "authenticated": False,
                "email": email,
                "user_id": user_id,
                "message": "User found but not authenticated with Google. Please complete OAuth.",
                "auth_url": auth_url,
                "next_step": "User must visit auth_url to grant Google permissions"
            }
        
        # User is fully authenticated
        token_expiry = token_data.get("token_expiry")
        expiry_str = token_expiry.isoformat() if token_expiry else None
        
        database.log_action(user_id, "check_google_auth", True, "mcp_tool", f"Authenticated: {email}")
        return {
            "authenticated": True,
            "email": email,
            "user_id": user_id,
            "display_name": user.get("display_name"),
            "scopes": token_data.get("scopes", []),
            "token_expiry": expiry_str,
            "message": "User is authenticated. You can now use Google Drive, Gmail, Calendar, and Tasks tools."
        }
        
    except Exception as e:
        database.log_action("N/A", "check_google_auth", False, "mcp_tool", str(e))
        return {
            "authenticated": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }

def _get_credentials(user_id: str):
    """Helper to create Google credentials from user's stored token with auto-refresh"""
    token_data = database.get_user_tokens(user_id)
    if not token_data:
        return None
    
    creds = Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.CLIENT_ID,
        client_secret=config.CLIENT_SECRET,
        scopes=token_data.get("scopes", config.SCOPES),
    )
    
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            new_token_data = {
                "access_token": creds.token,
                "refresh_token": creds.refresh_token,
                "expires_in": (creds.expiry - datetime.utcnow()).total_seconds() if creds.expiry else 3600
            }
            database.store_tokens(user_id, new_token_data, creds.scopes)
        except Exception as e:
            print(f"Token refresh failed for user {user_id}: {e}")
    
    return creds