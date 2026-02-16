import os
from dotenv import load_dotenv
from cryptography.fernet import Fernet
import mysql.connector

load_dotenv()

# Environment variables with validation
CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = os.getenv("MYSQL_PORT", "3306")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/New_York")

# Validate all required environment variables individually at startup
missing_vars = []
if not CLIENT_ID: missing_vars.append("GOOGLE_CLIENT_ID")
if not CLIENT_SECRET: missing_vars.append("GOOGLE_CLIENT_SECRET")
if not REDIRECT_URI: missing_vars.append("GOOGLE_REDIRECT_URI")
if not MYSQL_HOST: missing_vars.append("MYSQL_HOST")
if not MYSQL_DATABASE: missing_vars.append("MYSQL_DATABASE")
if not MYSQL_USER: missing_vars.append("MYSQL_USER")
if not MYSQL_PASSWORD: missing_vars.append("MYSQL_PASSWORD")

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
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/spreadsheets",
    "hettps://www.googleapis.com/auth/presentations"
]

# Connection pool management
connection_pool = []
MAX_POOL_SIZE = 10

def get_db_connection():
    """Create a connection to MySQL Database using mysql-connector with connection pooling"""
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

    try:
        port = int(MYSQL_PORT) if MYSQL_PORT else 3306
    except ValueError:
        raise RuntimeError("Invalid MYSQL_PORT value, must be an integer")

    try:
        conn = mysql.connector.connect(
            host=MYSQL_HOST,
            port=port,
            database=MYSQL_DATABASE,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
        )
        return conn
    except mysql.connector.Error as e:
        print(f"Database connection failed: {e}")
        raise

def return_connection(conn):
    """Return connection to pool"""
    if len(connection_pool) < MAX_POOL_SIZE:
        connection_pool.append(conn)
    else:
        try: conn.close()
        except: pass