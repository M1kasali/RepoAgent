"""MCP scratch exchange fixture for shared-sandbox acceptance."""

from pathlib import Path

from mcp_server import server


@server.tool()
def exchange(value: str) -> str:
    path = Path("/tmp/shared-mcp-state")
    previous = path.read_text() if path.exists() else ""
    path.write_text(value)
    return previous


if __name__ == "__main__":
    server.run(transport="stdio")
