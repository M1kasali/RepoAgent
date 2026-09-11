"""Owned SDK sessions behind the synchronous Tool Gateway client API."""

from __future__ import annotations

from concurrent.futures import CancelledError, TimeoutError as FutureTimeout
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from importlib.util import find_spec
import json
import math
import os
from pathlib import Path
import re
import threading
import time

from .tool_execution import ToolRunnerOutput
from .security import NetworkPolicyError, redact_text
from .mcp_http import MCPHTTPPolicy, build_http_client
from .sandbox import SandboxConfigurationError


class MCPConnectionError(RuntimeError):
    def __init__(self, message, *, code="connection_failed"):
        super().__init__(message)
        self.code = code


def _connection_error_code(error):
    pending, seen = [error], set()
    code = "connection_failed"
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, NetworkPolicyError):
            return "network_denied"
        if isinstance(current, SandboxConfigurationError):
            return "sandbox_failed"
        if isinstance(current, MCPConnectionError):
            code = current.code
        if getattr(getattr(current, "response", None), "status_code", None) in {
            401,
            403,
        }:
            code = "authentication_failed"
        pending.extend(
            item
            for item in (
                current.__cause__,
                current.__context__,
                *getattr(current, "exceptions", ()),
            )
            if isinstance(item, BaseException)
        )
    return code


def _validate_timeouts(*values):
    for value in values:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError("MCP timeouts must be finite positive numbers")


@dataclass(frozen=True)
class StdioServerConfig:
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict, repr=False)
    startup_timeout: float = 10.0
    tool_timeout: float = 30.0
    required: bool = True

    def __post_init__(self):
        if not isinstance(self.command, str) or not self.command.strip():
            raise ValueError("MCP command must be a non-empty executable")
        if not isinstance(self.args, (list, tuple)) or not all(
            isinstance(value, str) for value in self.args
        ):
            raise ValueError("MCP args must be an array of strings")
        if not isinstance(self.env, dict) or not all(
            isinstance(key, str)
            and key
            and "=" not in key
            and "\0" not in key
            and isinstance(value, str)
            and "\0" not in value
            for key, value in self.env.items()
        ):
            raise ValueError("MCP env must be a string mapping")
        if any("\0" in value for value in (self.command, *self.args)):
            raise ValueError("MCP executable and args cannot contain NUL")
        _validate_timeouts(self.startup_timeout, self.tool_timeout)
        object.__setattr__(self, "args", tuple(self.args))
        object.__setattr__(self, "env", dict(self.env))
        if not isinstance(self.required, bool):
            raise ValueError("MCP required must be boolean")


@dataclass(frozen=True)
class HTTPServerConfig:
    url: str
    type: str = "streamableHttp"
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    allow_private: bool = False
    startup_timeout: float = 10.0
    tool_timeout: float = 30.0
    required: bool = True

    def __post_init__(self):
        if not isinstance(self.url, str) or self.url != self.url.strip():
            raise ValueError("MCP URL must be a trimmed string")
        if self.type not in {"sse", "streamableHttp"}:
            raise ValueError("unsupported MCP HTTP transport")
        if not isinstance(self.allow_private, bool) or not isinstance(
            self.required, bool
        ):
            raise ValueError("MCP allow_private and required must be boolean")
        reserved = {
            "host",
            "content-length",
            "transfer-encoding",
            "connection",
            "mcp-session-id",
            "mcp-protocol-version",
        }
        if not isinstance(self.headers, dict) or not all(
            isinstance(key, str)
            and re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
            and key.lower() not in reserved
            and isinstance(value, str)
            and value.isascii()
            and not any(c in key + value for c in "\r\n\0")
            for key, value in self.headers.items()
        ):
            raise ValueError("invalid or reserved MCP headers")
        if len({key.lower() for key in self.headers}) != len(self.headers):
            raise ValueError("duplicate MCP headers")
        _validate_timeouts(self.startup_timeout, self.tool_timeout)
        MCPHTTPPolicy(self.url, allow_private=self.allow_private)
        object.__setattr__(self, "headers", dict(self.headers))


def load_mcp_servers(path, *, cwd, sandbox_adapter, require_isolation=False):
    """Only explicit host-selected files can authorize subprocess startup."""
    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or set(raw) != {"mcpServers"}
        or not isinstance(raw["mcpServers"], dict)
    ):
        raise ValueError("MCP config must contain an mcpServers object")
    servers = {}
    for name, spec in raw["mcpServers"].items():
        if not isinstance(spec, dict):
            raise ValueError("MCP server configuration must be an object")
        spec = dict(spec)
        transport = spec.pop("type", None)
        if transport is None:
            transport = (
                "stdio"
                if "command" in spec
                else (
                    "sse"
                    if str(spec.get("url", "")).rstrip("/").endswith("/sse")
                    else "streamableHttp"
                )
            )
        if transport == "stdio":
            if (require_isolation or sandbox_adapter.is_isolated) and not (
                sandbox_adapter.is_isolated
                and sandbox_adapter.supports_process_spawning
            ):
                raise MCPConnectionError(
                    "stdio MCP requires sandbox process spawning; refusing host fallback"
                )
            config_type = StdioServerConfig
        elif transport in {"sse", "streamableHttp"}:
            config_type = HTTPServerConfig
            spec["type"] = transport
        else:
            raise ValueError("unsupported MCP transport")
        try:
            config = config_type(**spec)
        except TypeError as exc:
            raise ValueError("invalid MCP configuration fields") from exc
        servers[name] = (
            StdioMCPClient(
                config,
                cwd=cwd,
                sandbox_adapter=sandbox_adapter
                if sandbox_adapter.is_isolated
                else None,
            )
            if transport == "stdio"
            else HTTPMCPClient(config)
        )
    return servers


class SDKMCPClient:
    """One portal owns SDK async scopes; callers never move them between Tasks.

    Discovery closes its temporary session. Calls lazily open a persistent
    session, revalidate the catalog, and keep it until Runtime shutdown.
    """

    def __init__(self, config):
        self.config = config
        self._lock = threading.RLock()
        self._portal_cm = None
        self._portal = None
        self._owner = None
        self._session = None
        self._stop = None
        self._catalog = None
        self._ready = None

    @property
    def connected(self):
        return (
            self._session is not None
            and self._owner is not None
            and not self._owner.done()
        )

    async def _serve(self):
        import anyio
        from mcp import ClientSession

        try:
            async with AsyncExitStack() as stack:
                read, write = await self._open_streams(stack)
                session = await stack.enter_async_context(ClientSession(read, write))
                with anyio.fail_after(self.config.startup_timeout):
                    await session.initialize()
                    catalog = []
                    cursor = None
                    seen = set()
                    for _ in range(100):
                        page = await session.list_tools(cursor=cursor)
                        for tool in page.tools:
                            # Server hints cannot grant local read-only trust.
                            catalog.append(
                                {
                                    "name": tool.name,
                                    "description": tool.description or tool.name,
                                    "input_schema": tool.inputSchema,
                                    "effect": "external",
                                    "concurrency_safe": False,
                                    "timeout_seconds": self.config.tool_timeout,
                                }
                            )
                        cursor = page.nextCursor
                        if cursor is None:
                            break
                        if cursor in seen:
                            raise MCPConnectionError(
                                "MCP catalog pagination repeated a cursor"
                            )
                        seen.add(cursor)
                    else:
                        raise MCPConnectionError("MCP catalog exceeds page limit")
                    catalog.sort(key=lambda item: item["name"])
                    if self._catalog is not None and catalog != self._catalog:
                        raise MCPConnectionError(
                            "MCP catalog changed; rebuild the Agent before calling tools",
                            code="catalog_changed",
                        )
                self._session = session
                self._stop = anyio.Event()
                self._catalog = catalog
                self._ready.set()
                await self._stop.wait()
        finally:
            self._session = None

    def _connect(self, control=None):
        if self.connected:
            return
        self.close()
        if find_spec("mcp") is None:
            raise MCPConnectionError(
                "Install repoagent[mcp] to use configured MCP servers",
                code="dependency_missing",
            )
        try:
            from anyio.from_thread import start_blocking_portal
        except ImportError as exc:
            raise MCPConnectionError(
                "Install repoagent[mcp] to use configured MCP servers",
                code="dependency_missing",
            ) from exc
        self._portal_cm = start_blocking_portal(name="repoagent-mcp")
        self._portal = self._portal_cm.__enter__()
        try:
            self._ready = threading.Event()
            self._owner = self._portal.start_task_soon(self._serve)
            deadline = time.monotonic() + self.config.startup_timeout
            while not self._ready.wait(0.02):
                if self._owner.done():
                    self._owner.result()
                    raise MCPConnectionError(
                        "MCP session stopped before initialization"
                    )
                if time.monotonic() >= deadline or (
                    control is not None and control.status() != "running"
                ):
                    raise MCPConnectionError(
                        "MCP startup timed out or was cancelled", code="startup_timeout"
                    )
        except BaseException as exc:
            self.close()
            if not isinstance(exc, Exception):
                raise
            raise MCPConnectionError(
                "MCP startup or catalog validation failed",
                code=_connection_error_code(exc),
            ) from exc

    def list_tools(self):
        with self._lock:
            try:
                self._connect()
                return json.loads(json.dumps(self._catalog))
            finally:
                self.close()

    async def _call(self, name, arguments, finished):
        try:
            return await self._session.call_tool(name, arguments=arguments)
        finally:
            finished.set()

    def call_tool(self, name, arguments, *, control):
        with self._lock:
            status = control.status()
            if status != "running":
                return ToolRunnerOutput(
                    "MCP call did not start", {"execution_status": status}
                )
            try:
                self._connect(control)
                if control.status() != "running":
                    status = control.status()
                    self.close()
                    return ToolRunnerOutput(
                        "MCP call did not start", {"execution_status": status}
                    )
                finished = threading.Event()
                future = self._portal.start_task_soon(
                    self._call, name, arguments, finished
                )
                while True:
                    status = control.status()
                    if status != "running":
                        future.cancel()
                        # Closing the session also prevents reuse after an uncertain call.
                        finished.wait(2.0)
                        self.close()
                        return ToolRunnerOutput(
                            "MCP call stopped", {"execution_status": status}
                        )
                    try:
                        result = future.result(
                            timeout=min(0.05, control.remaining_seconds)
                        )
                        break
                    except FutureTimeout:
                        if future.done():
                            raise
                parts = [
                    block.text if block.type == "text" else block.model_dump_json()
                    for block in result.content
                ]
                if not parts and result.structuredContent is not None:
                    parts.append(
                        json.dumps(result.structuredContent, ensure_ascii=True)
                    )
                content = self._redact_output("\n".join(parts) or "(no output)")
                return ToolRunnerOutput(
                    content,
                    {
                        "execution_status": "completed",
                        "mcp_is_error": bool(result.isError),
                        "exit_code_is_error": True,
                        "exit_code": int(bool(result.isError)),
                    },
                )
            except Exception as exc:
                self.close()
                return ToolRunnerOutput(
                    "MCP connection or call failed",
                    {
                        "execution_status": control.status(),
                        "exit_code_is_error": True,
                        "exit_code": 1,
                        "mcp_error_code": _connection_error_code(exc),
                    },
                )

    def close(self):
        with self._lock:
            try:
                if (
                    self._portal is not None
                    and self._owner is not None
                    and not self._owner.done()
                ):
                    if self._stop is not None:
                        self._portal.call(self._stop.set)
                    else:
                        self._owner.cancel()
                    try:
                        self._owner.result(timeout=10.0)
                    except CancelledError:
                        pass
            finally:
                cm, self._portal_cm = self._portal_cm, None
                self._portal = None
                self._owner = None
                self._session = None
                self._stop = None
                if cm is not None:
                    cm.__exit__(None, None, None)

    async def _open_streams(self, stack):
        raise NotImplementedError

    def _redact_output(self, content):
        return content


class StdioMCPClient(SDKMCPClient):
    transport = "stdio"

    def __init__(self, config, *, cwd, sandbox_adapter=None):
        if not isinstance(config, StdioServerConfig):
            raise TypeError("stdio MCP requires StdioServerConfig")
        super().__init__(config)
        self.cwd = str(Path(cwd).resolve())
        if sandbox_adapter is not None and not (
            sandbox_adapter.is_isolated and sandbox_adapter.supports_process_spawning
        ):
            raise MCPConnectionError(
                "stdio MCP sandbox does not support process spawning"
            )
        self.sandbox_adapter = sandbox_adapter

    async def _open_streams(self, stack):
        if self.sandbox_adapter is not None:
            return await stack.enter_async_context(
                self.sandbox_adapter.start_process(
                    self.config.command,
                    self.config.args,
                    cwd=self.cwd,
                    env=self.config.env,
                    startup_timeout=self.config.startup_timeout,
                )
            )
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        errlog = stack.enter_context(open(os.devnull, "w"))
        params = StdioServerParameters(
            command=self.config.command,
            args=list(self.config.args),
            env=self.config.env,
            cwd=self.cwd,
        )
        return await stack.enter_async_context(stdio_client(params, errlog=errlog))

    def _redact_output(self, content):
        return redact_text(content, env=self.config.env)


class HTTPMCPClient(SDKMCPClient):
    def __init__(self, config):
        if not isinstance(config, HTTPServerConfig):
            raise TypeError("HTTP MCP requires HTTPServerConfig")
        super().__init__(config)
        self.transport = config.type
        self.endpoint_url = config.url
        self.policy = MCPHTTPPolicy(config.url, allow_private=config.allow_private)

    async def _open_streams(self, stack):
        if self.transport == "sse":
            from mcp.client.sse import sse_client

            def factory(headers=None, timeout=None, auth=None):
                return build_http_client(
                    self.config,
                    self.policy,
                    headers=headers,
                    timeout=timeout,
                    auth=auth,
                )

            return await stack.enter_async_context(
                sse_client(
                    self.endpoint_url,
                    timeout=self.config.startup_timeout,
                    sse_read_timeout=self.config.tool_timeout,
                    httpx_client_factory=factory,
                )
            )
        from mcp.client.streamable_http import streamable_http_client

        http = await stack.enter_async_context(
            build_http_client(self.config, self.policy)
        )
        read, write, _ = await stack.enter_async_context(
            streamable_http_client(self.endpoint_url, http_client=http)
        )
        return read, write

    def _redact_output(self, content):
        return redact_text(
            content, env=self.config.headers, secret_env_names=self.config.headers
        )


def load_stdio_servers(path, **kwargs):
    """Compatibility entry point; new callers should use load_mcp_servers."""
    return load_mcp_servers(path, **kwargs)


__all__ = [
    "MCPConnectionError",
    "StdioServerConfig",
    "StdioMCPClient",
    "load_stdio_servers",
    "HTTPServerConfig",
    "HTTPMCPClient",
    "load_mcp_servers",
]
