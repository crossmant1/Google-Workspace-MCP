import secrets
import hashlib
import json
from datetime import datetime, timedelta
from typing import Optional
from config import get_db_connection, return_connection, cipher_suite

# Security helper functions
def encrypt_token(token_data: dict) -> str:
    """Encrypt token data for storage"""
    json_data = json.dumps(token_data)
    encrypted = cipher_suite.encrypt(json_data.encode())
    return encrypted.decode()

def decrypt_token(encrypted_data: str) -> dict:
    """Decrypt token data from storage"""
    decrypted = cipher_suite.decrypt(encrypted_data.encode())
    return json.loads(decrypted.decode())

def sanitize_drive_query(query: str) -> str:
    """Safely escape Drive API query - NO SQL involved, just Drive API query syntax"""
    # For Drive API, we need to escape single quotes by doubling them
    # This is NOT SQL injection - it's Drive API's query language
    return query.replace("'", "''")

def create_user(email: str, display_name: str) -> str:
    """Create a new user and return user_id (NO API KEY)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        user_id = secrets.token_urlsafe(16)
        
        cursor.execute("""
            INSERT INTO users (user_id, email, display_name, created_at, last_login, is_active)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, email, display_name, datetime.utcnow(), datetime.utcnow(), 1))
        conn.commit()
        return user_id
    finally:
        cursor.close()
        return_connection(conn)

def get_user_by_email(email: str) -> Optional[dict]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT user_id, email, display_name, is_active FROM users WHERE email = ?", (email,))
        row = cursor.fetchone()
        if row:
            return {
                "user_id": row[0],
                "email": row[1],
                "display_name": row[2],
                "is_active": bool(row[3])
            }
        return None
    finally:
        cursor.close()
        return_connection(conn)

def store_tokens(user_id: str, token_data: dict, scopes: list):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        encrypted_access = encrypt_token({"token": token_data.get("access_token")})
        encrypted_refresh = encrypt_token({"token": token_data.get("refresh_token")})
        
        expires_in = token_data.get("expires_in", 3600)
        token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
        scopes_str = " ".join(scopes)
        
        cursor.execute("SELECT user_id FROM tokens WHERE user_id = ?", (user_id,))
        exists = cursor.fetchone()
        
        if exists:
            cursor.execute("""
                UPDATE tokens
                SET access_token = ?, refresh_token = ?, token_expiry = ?, scopes = ?, updated_at = ?
                WHERE user_id = ?
            """, (encrypted_access, encrypted_refresh, token_expiry, scopes_str, datetime.utcnow(), user_id))
        else:
            cursor.execute("""
                INSERT INTO tokens (user_id, access_token, refresh_token, token_expiry, scopes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, encrypted_access, encrypted_refresh, token_expiry, scopes_str, datetime.utcnow()))
        conn.commit()
    finally:
        cursor.close()
        return_connection(conn)

def get_user_tokens(user_id: str) -> Optional[dict]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT access_token, refresh_token, token_expiry, scopes
            FROM tokens
            WHERE user_id = ?
        """, (user_id,))
        row = cursor.fetchone()
        if row:
            access_token_data = decrypt_token(row[0])
            refresh_token_data = decrypt_token(row[1])
            return {
                "access_token": access_token_data.get("token"),
                "refresh_token": refresh_token_data.get("token"),
                "token_expiry": row[2],
                "scopes": row[3].split()
            }
        return None
    finally:
        cursor.close()
        return_connection(conn)

def create_session(user_id: str, ip_address: str, user_agent: str) -> str:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(days=30)
        cursor.execute("""
            INSERT INTO sessions (session_token, user_id, created_at, expires_at, ip_address, user_agent)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_token, user_id, datetime.utcnow(), expires_at, ip_address, user_agent))
        conn.commit()
        return session_token
    finally:
        cursor.close()
        return_connection(conn)

def get_user_from_session(session_token: str) -> Optional[str]:
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT user_id FROM sessions
            WHERE session_token = ? AND expires_at > ?
        """, (session_token, datetime.utcnow()))
        row = cursor.fetchone()
        return row[0] if row else None
    finally:
        cursor.close()
        return_connection(conn)

def log_action(user_id: str, action: str, success: bool, source: str, details: str, ip_address: str = "N/A"):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            if len(details) > 1024: details = details[:1021] + "..."
            success_int = 1 if success else 0
            timestamp = datetime.utcnow()
            cursor.execute("""
                INSERT INTO audit_logs (user_id, action, timestamp, success, ip_address, source, details)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, action, timestamp, success_int, ip_address, source, details))
            conn.commit()
        finally:
            cursor.close()
            return_connection(conn)
    except Exception as e:
        print(f"Failed to log action: {e}")

def update_last_login(user_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE users SET last_login = ? WHERE user_id = ?", (datetime.utcnow(), user_id))
        conn.commit()
    finally:
        cursor.close()
        return_connection(conn)