import os, uvicorn
from mcpserver.server import app

def main():
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    main()