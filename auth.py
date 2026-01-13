from datetime import datetime
from typing import Optional
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import database
import config

async def verify_api_key(api_key: Optional[str]) -> Optional[str]:
    """Verify API key and return user_id"""
    if not api_key:
        return None
    return database.get_user_by_api_key(api_key)

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