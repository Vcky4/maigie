from mcp.server.fastmcp import FastMCP

mcp = FastMCP("maigie")
app = mcp.streamable_http_app
try:
    routes = [r.path for r in app.routes]
    print("Routes:", routes)
except Exception as e:
    print(e)
