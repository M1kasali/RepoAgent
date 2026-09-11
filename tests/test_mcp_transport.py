import asyncio
import json
import os
from pathlib import Path
import sys
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
from repoagent.cli import build_arg_parser
from repoagent.mcp import MCPManager
from repoagent.mcp_transport import (
    MCPConnectionError,
    StdioMCPClient,
    StdioServerConfig,
    load_stdio_servers,
)
from repoagent.runtime_assembly import RuntimeAssembly
from repoagent.sandbox import DirectSandboxAdapter, IsolatedSandboxAdapter
from repoagent.tool_execution import ToolExecutionControl


SERVER = Path(__file__).parent / "fixtures" / "mcp_server.py"


def client(root, **kwargs):
    pytest.importorskip("mcp")
    return StdioMCPClient(
        StdioServerConfig(sys.executable, (str(SERVER),), **kwargs), cwd=root
    )


def control(timeout=5, token=None):
    return ToolExecutionControl(
        timeout_seconds=timeout, max_output_chars=4000, cancellation_token=token
    )


def test_real_sdk_discovery_persistent_calls_error_and_shutdown(tmp_path, monkeypatch):
    monkeypatch.setenv("UNRELATED_API_KEY", "must-not-inherit")
    transport = client(
        tmp_path,
        env={"EXPLICIT_VALUE": "allowed", "FIXTURE_API_KEY": "secret-value-123"},
    )
    try:
        definitions = transport.list_tools()
        assert not transport.connected
        echo = next(item for item in definitions if item["name"] == "echo")
        assert echo["effect"] == "external" and not echo["concurrency_safe"]
        first = json.loads(
            transport.call_tool("echo", {"value": "one"}, control=control()).content
        )
        second = json.loads(
            transport.call_tool("echo", {"value": "two"}, control=control()).content
        )
        assert first["pid"] == second["pid"]
        assert (first["calls"], second["calls"]) == (1, 2)
        assert first["cwd"] == str(tmp_path)
        assert first["explicit"] == "allowed" and first["inherited_secret"] is None
        failure = transport.call_tool("fail", {}, control=control())
        assert failure.metadata["mcp_is_error"] and failure.metadata["exit_code"] == 1
        assert (
            "secret-value-123"
            not in transport.call_tool("secret", {}, control=control()).content
        )
    finally:
        transport.close()
    assert not transport.connected
    if os.name == "posix":
        with pytest.raises(ProcessLookupError):
            os.kill(first["pid"], 0)
    transport.close()


@pytest.mark.parametrize("cancel", [False, True])
def test_real_call_timeout_and_cancellation_stop_late_effects(tmp_path, cancel):
    transport = client(tmp_path)
    token = CancellationToken()
    timer = None
    try:
        transport.list_tools()
        transport.call_tool("echo", {"value": "warm"}, control=control())
        if cancel:
            timer = threading.Timer(0.1, token.cancel)
            timer.start()
        result = transport.call_tool(
            "slow", {}, control=control(5 if cancel else 0.1, token)
        )
        assert result.metadata["execution_status"] == (
            "cancelled" if cancel else "timeout"
        )
        assert not transport.connected
        time.sleep(0.9)
        assert not (tmp_path / "late-effect.txt").exists()
    finally:
        if timer:
            timer.cancel()
            timer.join()
        transport.close()


def test_server_crash_and_catalog_drift_fail_closed(tmp_path):
    transport = client(tmp_path)
    try:
        transport.list_tools()
        result = transport.call_tool("crash", {}, control=control())
        assert result.metadata["exit_code"] == 1
        assert not transport.connected
        (tmp_path / "catalog-drift").touch()
        result = transport.call_tool("echo", {"value": "not-called"}, control=control())
        assert result.metadata["exit_code"] == 1
        assert not transport.connected
    finally:
        transport.close()


def test_handshake_timeout_cleans_owner_thread(tmp_path):
    pytest.importorskip("mcp")
    transport = StdioMCPClient(
        StdioServerConfig(
            sys.executable, ("-c", "import time; time.sleep(60)"), startup_timeout=0.2
        ),
        cwd=tmp_path,
    )
    with pytest.raises(MCPConnectionError, match="startup"):
        transport.list_tools()
    assert not transport.connected
    assert transport._portal is None


def test_config_rejects_sandbox_host_fallback_and_unknown_transport(tmp_path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"docs": {"command": sys.executable}}}))
    isolated = IsolatedSandboxAdapter(
        type(
            "Backend",
            (),
            {
                "is_isolated": True,
                "execute": lambda *args, **kwargs: None,
            },
        )()
    )
    with pytest.raises(MCPConnectionError, match="host fallback"):
        load_stdio_servers(path, cwd=tmp_path, sandbox_adapter=isolated)
    direct = DirectSandboxAdapter()
    servers = load_stdio_servers(path, cwd=tmp_path, sandbox_adapter=direct)
    with pytest.raises(MCPConnectionError, match="host execution"):
        MCPManager(servers, sandbox_adapter=isolated)
    with pytest.raises(MCPConnectionError):
        MCPManager(servers, require_isolation=True)
    path.write_text(
        json.dumps({"mcpServers": {"docs": {"type": "unsupported", "url": "http://localhost"}}})
    )
    with pytest.raises(ValueError, match="unsupported MCP transport"):
        load_stdio_servers(path, cwd=tmp_path, sandbox_adapter=direct)


def test_real_gateway_and_runtime_close(tmp_path):
    transport = client(tmp_path)
    agent = RepoAgent(
        FakeModelClient(
            [
                '<tool>{"name":"mcp_docs_echo","args":{"value":"hello"}}</tool>',
                "<final>done</final>",
            ]
        ),
        WorkspaceContext.build(tmp_path),
        SessionStore(tmp_path / ".repoagent/sessions"),
        mcp_servers={"docs": transport},
        approval_policy="auto",
    )
    assert agent.ask("call the docs tool") == "done"
    assert not transport.connected
    try:
        result = agent.execute_tool("mcp_docs_fail", {})
        assert result.status == "error" and result.metadata["mcp_is_error"]
    finally:
        asyncio.run(agent.aclose())


def test_cli_assembly_loads_only_explicit_config(tmp_path):
    pytest.importorskip("mcp")
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps(
            {"mcpServers": {"docs": {"command": sys.executable, "args": [str(SERVER)]}}}
        )
    )
    args = build_arg_parser().parse_args(
        [
            "--cwd",
            str(tmp_path),
            "--mcp-config",
            str(path),
            "--checkpoint-policy",
            "never",
        ]
    )

    def factory(_):
        model = FakeModelClient([])
        model.profile = type(
            "Profile",
            (),
            {
                "max_output_tokens": 64,
                "context_window_tokens": 4096,
                "context_window_source": "test",
            },
        )()
        return model

    agent = RuntimeAssembly.from_arguments(
        args, model_client_factory=factory, secret_names_factory=lambda _: ()
    ).build()
    try:
        assert "mcp_docs_echo" in agent.tools
        assert all(not c.connected for c in agent.mcp_manager._servers.values())
    finally:
        asyncio.run(agent.aclose())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"startup_timeout": float("nan")},
        {"tool_timeout": 0},
        {"args": "shell args"},
        {"env": {"TOKEN": 1}},
    ],
)
def test_config_rejects_invalid_fields(kwargs):
    with pytest.raises(ValueError):
        StdioServerConfig("python", **kwargs)


def test_missing_optional_sdk_has_actionable_error(tmp_path, monkeypatch):
    transport = StdioMCPClient(StdioServerConfig("python"), cwd=tmp_path)
    monkeypatch.setattr("repoagent.mcp_transport.find_spec", lambda _: None)
    with pytest.raises(MCPConnectionError, match=r"repoagent\[mcp\]"):
        transport.list_tools()


def test_cancellation_at_handshake_boundary_does_not_submit_call(tmp_path, monkeypatch):
    transport = StdioMCPClient(StdioServerConfig("python"), cwd=tmp_path)
    token = CancellationToken()
    monkeypatch.setattr(transport, "_connect", lambda _: token.cancel())
    result = transport.call_tool("echo", {}, control=control(5, token))
    assert result.metadata["execution_status"] == "cancelled"
    assert result.content == "MCP call did not start"


def test_cancellation_during_startup_reaps_child(tmp_path):
    pytest.importorskip("mcp")
    pid_file = tmp_path / "server.pid"
    source = "import os,pathlib,time; pathlib.Path('server.pid').write_text(str(os.getpid())); time.sleep(60)"
    transport = StdioMCPClient(
        StdioServerConfig(sys.executable, ("-c", source)), cwd=tmp_path
    )
    token = CancellationToken()
    timer = threading.Timer(0.2, token.cancel)
    try:
        timer.start()
        result = transport.call_tool("echo", {}, control=control(5, token))
        assert result.metadata["execution_status"] == "cancelled"
        assert transport._portal is None
        if os.name == "posix":
            assert pid_file.is_file()
            with pytest.raises(ProcessLookupError):
                os.kill(int(pid_file.read_text()), 0)
    finally:
        timer.cancel()
        timer.join()
        transport.close()


def test_runtime_cleanup_survives_memory_stop_error(tmp_path):
    transport = client(tmp_path)
    agent = RepoAgent(
        FakeModelClient([]),
        WorkspaceContext.build(tmp_path),
        SessionStore(tmp_path / ".repoagent/sessions"),
        mcp_servers={"docs": transport},
        approval_policy="auto",
    )

    async def bad_stop():
        raise RuntimeError("memory stop failed")

    try:
        agent.execute_tool("mcp_docs_echo", {"value": "started"})
        assert transport.connected
        agent._memory_backend_started = True
        agent.memory_backend.stop = bad_stop
        with pytest.raises(RuntimeError, match="memory stop failed"):
            asyncio.run(agent.aclose())
        assert not transport.connected
    finally:
        transport.close()
