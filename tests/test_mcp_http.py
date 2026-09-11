import asyncio
from contextlib import contextmanager
import json
import socket
import threading
import time

import pytest

from repoagent import (
    CancellationToken,
    FakeModelClient,
    RepoAgent,
    SessionStore,
    WorkspaceContext,
)
from repoagent.cli import main
from repoagent.mcp import MCPManager
from repoagent.mcp_http import MCPHTTPPolicy, build_http_client
from repoagent.mcp_transport import (
    HTTPMCPClient,
    HTTPServerConfig,
    MCPConnectionError,
    load_mcp_servers,
)
from repoagent.sandbox import DirectSandboxAdapter
from repoagent.security import NetworkPolicy, NetworkPolicyError
from repoagent.tool_execution import ToolExecutionControl


def control(timeout=5, token=None):
    return ToolExecutionControl(
        timeout_seconds=timeout, max_output_chars=4000, cancellation_token=token
    )


@contextmanager
def server(transport, *, token="", redirect=None):
    pytest.importorskip("mcp")
    import uvicorn
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("HTTP fixture", json_response=True)

    @mcp.tool(structured_output=False)
    def echo(value: str) -> str:
        return value

    @mcp.tool()
    def fail() -> str:
        raise ValueError("tool failed")

    @mcp.tool()
    async def slow() -> str:
        await asyncio.sleep(1)
        return "late"

    app = mcp.sse_app() if transport == "sse" else mcp.streamable_http_app()
    requests = []

    async def wrapped(scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            requests.append(
                {"method": scope["method"], "path": scope["path"], "headers": headers}
            )
            status = (
                307
                if redirect
                else (
                    401
                    if token and headers.get(b"authorization") != token.encode()
                    else None
                )
            )
            if status:
                await send(
                    {
                        "type": "http.response.start",
                        "status": status,
                        "headers": [(b"location", redirect.encode())]
                        if redirect
                        else [],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return
        await app(scope, receive, send)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    address = f"http://127.0.0.1:{sock.getsockname()[1]}"
    instance = uvicorn.Server(
        uvicorn.Config(wrapped, log_level="critical", timeout_graceful_shutdown=1)
    )
    thread = threading.Thread(
        target=lambda: asyncio.run(instance.serve(sockets=[sock])), daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not instance.started:
            assert thread.is_alive() and time.monotonic() < deadline, (
                "fixture did not start"
            )
            time.sleep(0.01)
        yield address + ("/sse" if transport == "sse" else "/mcp"), requests
    finally:
        instance.should_exit = True
        thread.join(5)
        if thread.is_alive():
            instance.force_exit = True
            thread.join(5)
        sock.close()
        assert not thread.is_alive(), "fixture failed to stop"


@pytest.mark.parametrize("transport", ["sse", "streamableHttp"])
def test_real_http_discovery_auth_calls_and_shutdown(transport):
    with server(transport, token="Bearer fixture-secret") as (url, requests):
        client = HTTPMCPClient(
            HTTPServerConfig(
                url,
                type=transport,
                allow_private=True,
                headers={"Authorization": "Bearer fixture-secret"},
            )
        )
        try:
            assert len(client.list_tools()) == 3
            assert not client.connected
            result = client.call_tool("echo", {"value": "hello"}, control=control())
            assert result.content == "hello" and result.metadata["exit_code"] == 0
            assert client.connected
            error = client.call_tool("fail", {}, control=control())
            assert error.metadata["mcp_is_error"] and error.metadata["exit_code"] == 1
            secret = client.call_tool(
                "echo", {"value": "Bearer fixture-secret"}, control=control()
            )
            assert "fixture-secret" not in secret.content
        finally:
            client.close()
        assert not client.connected and client._portal is None
        assert all(
            r["headers"].get(b"authorization") == b"Bearer fixture-secret"
            for r in requests
        )
        if transport == "streamableHttp":
            assert sum(r["method"] == "DELETE" for r in requests) >= 2


@pytest.mark.parametrize("transport", ["sse", "streamableHttp"])
@pytest.mark.parametrize("cancel", [False, True])
def test_http_timeout_cancel_and_runtime_error(transport, cancel):
    with server(transport) as (url, _):
        client = HTTPMCPClient(
            HTTPServerConfig(url, type=transport, allow_private=True)
        )
        token = CancellationToken()
        timer = None
        try:
            client.list_tools()
            client.call_tool("echo", {"value": "warm"}, control=control())
            if cancel:
                timer = threading.Timer(0.1, token.cancel)
                timer.start()
            result = client.call_tool(
                "slow", {}, control=control(5 if cancel else 0.1, token)
            )
            assert result.metadata["execution_status"] == (
                "cancelled" if cancel else "timeout"
            )
            assert not client.connected
        finally:
            if timer:
                timer.cancel()
                timer.join()
            client.close()


@pytest.mark.parametrize("transport", ["sse", "streamableHttp"])
def test_redirect_does_not_contact_target_or_forward_credentials(transport):
    with server(transport) as (destination, received):
        with server(transport, redirect=destination) as (url, _):
            client = HTTPMCPClient(
                HTTPServerConfig(
                    url,
                    type=transport,
                    allow_private=True,
                    startup_timeout=0.5,
                    headers={"Authorization": "must-not-forward"},
                )
            )
            with pytest.raises(MCPConnectionError):
                client.list_tools()
            assert not received
            assert client._portal is None


def test_optional_failure_diagnostics_and_healthy_runtime(tmp_path, capsys):
    with server("streamableHttp", token="right-token") as (url, _):
        config = tmp_path / "mcp.json"
        config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "bad": {
                            "url": url,
                            "allow_private": True,
                            "headers": {"Authorization": "private-wrong-token"},
                            "required": False,
                            "startup_timeout": 0.5,
                        },
                        "good": {
                            "url": url,
                            "allow_private": True,
                            "headers": {"Authorization": "right-token"},
                        },
                    }
                }
            )
        )
        clients = load_mcp_servers(
            config, cwd=tmp_path, sandbox_adapter=DirectSandboxAdapter()
        )
        agent = RepoAgent(
            FakeModelClient(
                [
                    '<tool>{"name":"mcp_good_echo","args":{"value":"hi"}}</tool>',
                    "<final>done</final>",
                ]
            ),
            WorkspaceContext.build(tmp_path),
            SessionStore(tmp_path / "sessions"),
            mcp_servers=clients,
            approval_policy="auto",
        )
        assert [r["status"] for r in agent.mcp_manager.diagnostics] == [
            "unavailable",
            "discovered",
        ]
        assert "private-wrong-token" not in json.dumps(agent.mcp_manager.diagnostics)
        assert "mcp_bad_echo" not in agent.tools
        assert agent.ask("use MCP") == "done"
        assert all(not c.connected for c in clients.values())
        assert (
            main(["mcp", "check", "--config", str(config), "--cwd", str(tmp_path)]) == 2
        )
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "fail" and len(report["servers"]) == 2


def test_required_failure_blocks_startup_and_external_policy_cannot_be_weakened():
    with server("sse", token="required") as (url, _):
        client = HTTPMCPClient(
            HTTPServerConfig(url, type="sse", allow_private=True, startup_timeout=0.5)
        )
        manager = MCPManager({"docs": client})
        with pytest.raises(MCPConnectionError):
            manager.discover()
        assert manager.diagnostics[0]["status"] == "unavailable"
        assert manager.diagnostics[0]["error_code"] == "authentication_failed"
        assert not client.connected
        manager = MCPManager(
            {"docs": client}, network_policy=NetworkPolicy(enabled=False)
        )
        with pytest.raises(NetworkPolicyError, match="disabled"):
            manager.discover()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/mcp",
        "http://localhost/mcp",
        "file:///tmp/mcp",
        "https://user:password@example.com/mcp",
        "https://example.com/mcp#fragment",
    ],
)
def test_default_policy_rejects_unsafe_config(url):
    with pytest.raises(ValueError):
        HTTPServerConfig(url)


def test_dns_resolution_is_checked_and_socket_target_is_pinned(monkeypatch):
    httpx = pytest.importorskip("httpx")
    seen = []

    async def inner(_, request):
        seen.append(request)
        return httpx.Response(200, content=b"ok")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", inner)
    config = HTTPServerConfig("https://docs.example/mcp")
    policy = MCPHTTPPolicy(config.url)

    async def run():
        def resolve(*args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]

        monkeypatch.setattr(socket, "getaddrinfo", resolve)
        async with build_http_client(config, policy) as client:
            await client.get(config.url)
            assert seen[0].url.host == "8.8.8.8"
            assert seen[0].headers["host"] == "docs.example"
            assert seen[0].extensions["sni_hostname"] == "docs.example"

            def rebound(*args, **kwargs):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

            monkeypatch.setattr(socket, "getaddrinfo", rebound)
            with pytest.raises(NetworkPolicyError):
                await client.get(config.url)
            with pytest.raises(NetworkPolicyError, match="origin"):
                await client.get("https://other.example/mcp")
            assert len(seen) == 1

    asyncio.run(run())


def test_explicit_private_access_never_allows_metadata_or_multicast():
    policy = MCPHTTPPolicy("http://127.0.0.1/mcp", allow_private=True)
    for address in [
        "169.254.169.254",
        "224.0.0.1",
        "0.0.0.0",
        "::",
        "::ffff:169.254.169.254",
    ]:
        with pytest.raises(NetworkPolicyError):
            policy.validate_address(address)


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil"},
        {"Authorization": "a\nb"},
        {"Mcp-Session-Id": "forged"},
        {"X-A": "1", "x-a": "2"},
        {"Bad Header": "value"},
        {"X-Token": "\u00e9"},
    ],
)
def test_invalid_headers_are_rejected(headers):
    with pytest.raises(ValueError):
        HTTPServerConfig("https://example.com/mcp", headers=headers)


def test_optional_server_cannot_hide_dns_policy_denial(monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )
    client = HTTPMCPClient(HTTPServerConfig("https://docs.example/mcp", required=False))
    manager = MCPManager({"docs": client})
    with pytest.raises(MCPConnectionError) as error:
        manager.discover()
    assert error.value.code == "network_denied"
    assert manager.diagnostics[0]["error_code"] == "network_denied"
    assert client._portal is None


def test_blocked_dns_does_not_hold_cancelled_session_open(monkeypatch):
    pytest.importorskip("mcp")
    release = threading.Event()
    resolved = threading.Event()

    def blocked(*args, **kwargs):
        try:
            release.wait(5)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
        finally:
            resolved.set()

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    client = HTTPMCPClient(
        HTTPServerConfig("https://docs.example/mcp", startup_timeout=5)
    )
    try:
        started = time.monotonic()
        result = client.call_tool("echo", {}, control=control(0.2))
        assert result.metadata["execution_status"] == "timeout"
        assert time.monotonic() - started < 2
        assert client._portal is None
    finally:
        release.set()
        assert resolved.wait(2)
        client.close()


@pytest.mark.parametrize("transport", ["sse", "streamableHttp"])
def test_same_origin_redirect_is_also_rejected(transport):
    with server(transport, redirect="/other") as (url, requests):
        client = HTTPMCPClient(
            HTTPServerConfig(
                url, type=transport, allow_private=True, startup_timeout=0.5
            )
        )
        with pytest.raises(MCPConnectionError) as error:
            client.list_tools()
        assert error.value.code == "network_denied"
        assert all(r["path"] != "/other" for r in requests)


def test_proxy_environment_cannot_redirect_transport(monkeypatch):
    pytest.importorskip("mcp")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with server("streamableHttp") as (url, _):
        client = HTTPMCPClient(HTTPServerConfig(url, allow_private=True))
        assert len(client.list_tools()) == 3
        assert not client.connected


def test_http_configuration_does_not_require_host_process_fallback(tmp_path):
    class IsolatedAdapter:
        is_isolated = True

    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"mcpServers": {"docs": {"url": "https://example.com/mcp"}}})
    )
    clients = load_mcp_servers(
        config, cwd=tmp_path, sandbox_adapter=IsolatedAdapter(), require_isolation=True
    )
    assert isinstance(clients["docs"], HTTPMCPClient)
    assert not clients["docs"].connected


def test_cli_reports_invalid_config_without_startup(tmp_path, capsys):
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "docs": {
                        "url": "https://example.com/mcp",
                        "required": False,
                        "unknown": "private-token",
                    }
                }
            }
        )
    )
    assert main(["mcp", "check", "--config", str(config)]) == 2
    output = capsys.readouterr()
    assert "invalid MCP configuration fields" in output.err
    assert "private-token" not in output.out + output.err
