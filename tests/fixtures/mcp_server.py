"""Credential-free real MCP server used only by transport integration tests."""

import asyncio
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP


server = FastMCP("RepoAgent transport fixture")
calls = 0


@server.tool(annotations={"readOnlyHint": True})
def echo(value: str) -> str:
    global calls
    calls += 1
    return json.dumps(
        {
            "value": value,
            "pid": os.getpid(),
            "calls": calls,
            "cwd": os.getcwd(),
            "explicit": os.environ.get("EXPLICIT_VALUE"),
            "inherited_secret": os.environ.get("UNRELATED_API_KEY"),
        }
    )


@server.tool()
def fail() -> str:
    raise ValueError("fixture tool failure")


@server.tool()
def secret() -> str:
    return os.environ.get("FIXTURE_API_KEY", "unset")


@server.tool()
async def slow() -> str:
    await asyncio.sleep(0.8)
    Path("late-effect.txt").write_text("should not run", encoding="utf-8")
    return "late"


@server.tool()
def crash() -> str:
    os._exit(17)


if Path("catalog-drift").exists():

    @server.tool()
    def added_later() -> str:
        return "changed"


if __name__ == "__main__":
    server.run(transport="stdio")
