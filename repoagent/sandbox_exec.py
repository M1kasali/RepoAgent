"""Official MCP stdio transport over independently owned Docker exec groups."""

from contextlib import asynccontextmanager
from functools import lru_cache
import os
from pathlib import Path


@lru_cache(maxsize=1)
def guest_source():
    return Path(__file__).with_name("sandbox_exec_guest.py").read_text(encoding="utf-8")


def guest_argv(mode, token, command=(), *, executable="python"):
    return [executable, "-I", "-B", "-c", guest_source(), mode, token, *command]


@asynccontextmanager
async def shared_docker_process(adapter, command, args, *, cwd, env, startup_timeout):
    import anyio
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    handle = None
    try:
        with anyio.CancelScope(shield=True):
            handle, argv = await anyio.to_thread.run_sync(
                lambda: adapter.prepare_process(
                    command, args, cwd=cwd, env=env, timeout=startup_timeout
                )
            )
        await anyio.lowlevel.checkpoint()
        params = StdioServerParameters(
            command=adapter.executable, args=argv,
            cwd=str(adapter.workspace), env=adapter._docker_environment(),
        )
        with open(os.devnull, "w") as errlog:
            async with stdio_client(params, errlog=errlog) as streams:
                try:
                    yield streams
                finally:
                    with anyio.CancelScope(shield=True):
                        await anyio.to_thread.run_sync(adapter.finish_process, handle)
                    handle = None
    finally:
        if handle is not None:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(adapter.finish_process, handle)
