import logging
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Create the MCP Server
mcp = FastMCP("maigie-mcp-server")


@mcp.tool()
def get_maigie_status() -> str:
    """Get the current status of the Maigie application"""
    return "Maigie backend is online and ready!"


@mcp.tool()
def say_hello(name: str) -> str:
    """Say hello to the user through the Maigie integration"""
    return f"Hello, {name}! Welcome to Maigie via ChatGPT."


# We can add more tools here later that interact with the database, etc.
