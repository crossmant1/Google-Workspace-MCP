import os
import pyodbc
from dotenv import load_dotenv
from cryptography.fernet import Fernet

load_dotenv()

# Environment variables with validation
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
AZURE_SQL_SERVER = os.getenv("AZURE_SQL_SERVER")
AZURE_SQL_DATABASE = os.getenv("AZURE_SQL_DATABASE")
AZURE_SQL_USERNAME = os.getenv("AZURE_SQL_USERNAME")
AZURE_SQL_PASSWORD = os.getenv("AZURE_SQL_PASSWORD")
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/New_York")

# Validate all required environment variables individually at startup
missing_vars = []
if not CLIENT_ID: missing_vars.append("GOOGLE_CLIENT_ID")
if not CLIENT_SECRET: missing_vars.append("GOOGLE_CLIENT_SECRET")
if not REDIRECT_URI: missing_vars.append("GOOGLE_REDIRECT_URI")
if not AZURE_SQL_SERVER: missing_vars.append("AZURE_SQL_SERVER")
if not AZURE_SQL_DATABASE: missing_vars.append("AZURE_SQL_DATABASE")
if not AZURE_SQL_USERNAME: missing_vars.append("AZURE_SQL_USERNAME")
if not AZURE_SQL_PASSWORD: missing_vars.append("AZURE_SQL_PASSWORD")

if missing_vars:
    raise RuntimeError(f"Missing required environment variables: {', '.join(missing_vars)}")

# Encryption key for tokens
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    print("WARNING: No ENCRYPTION_KEY found, generating temporary key (DO NOT USE IN PRODUCTION)")
    ENCRYPTION_KEY = Fernet.generate_key()
else:
    ENCRYPTION_KEY = ENCRYPTION_KEY.encode()

cipher_suite = Fernet(ENCRYPTION_KEY)

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks"
]

# Connection pool management
connection_pool = []
MAX_POOL_SIZE = 10

def get_db_connection():
    """Create a connection to Azure SQL Database using pyodbc with connection pooling"""
    # Try to reuse existing connection from pool
    while connection_pool:
        conn = connection_pool.pop()
        try:
            # Test if connection is still alive
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            return conn
        except:
            try: conn.close()
            except: pass
    
    # No valid connections in pool, create new one
    server = AZURE_SQL_SERVER
    if not server.endswith('.database.windows.net'):
        if server.startswith('tcp:'):
            server = server.replace('tcp:', '')
        if not server.endswith('.database.windows.net'):
            server = f"{server}.database.windows.net"
    else:
        server = server.replace('tcp:', '')
    
    conn_str = (
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={server};"
        f"DATABASE={AZURE_SQL_DATABASE};"
        f"UID={AZURE_SQL_USERNAME};"
        f"PWD={AZURE_SQL_PASSWORD};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )
    
    try:
        conn = pyodbc.connect(conn_str)
        return conn
    except pyodbc.Error as e:
        print(f"Database connection failed: {e}")
        raise

def return_connection(conn):
    """Return connection to pool"""
    if len(connection_pool) < MAX_POOL_SIZE:
        connection_pool.append(conn)
    else:
        try: conn.close()
        except: pass