import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext
from repoagent.mcp import MCPManager
from repoagent.mcp_transport import (
    MCPConnectionError,
    StdioMCPClient,
    StdioServerConfig,
    load_mcp_servers,
)
from repoagent.sandbox import DockerSandboxAdapter, SandboxConfigurationError
from repoagent.tool_execution import ToolExecutionControl


@pytest.fixture
def docker_bridge(tmp_path, monkeypatch):
    """Fake Docker control plane, real SDK subprocess/JSON-RPC data plane."""
    pytest.importorskip("mcp")
    from mcp import StdioServerParameters
    from mcp.client import stdio

    calls, attached = [], []
    real_stdio = stdio.stdio_client
    fixture = Path(__file__).parent / "fixtures" / "mcp_server.py"

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "container-id\n", "")

    @asynccontextmanager
    async def bridge(params, errlog):
        attached.append(params)
        async with real_stdio(
            StdioServerParameters(
                command=sys.executable, args=[str(fixture)], cwd=str(tmp_path)
            ),
            errlog=errlog,
        ) as streams:
            yield streams

    monkeypatch.setattr(stdio, "stdio_client", bridge)
    adapter = DockerSandboxAdapter(tmp_path, lifecycle_runner=runner)
    return adapter, calls, attached


def test_owned_process_reuses_session_and_closes_container(docker_bridge, tmp_path):
    adapter, calls, attached = docker_bridge
    client = StdioMCPClient(
        StdioServerConfig("python", args=["server.py"], env={"SERVER_KEY": "explicit"}),
        cwd=tmp_path,
        sandbox_adapter=adapter,
    )
    try:
        assert len(client.list_tools()) == 5
        assert not adapter._process_names
        first = client.call_tool(
            "echo",
            {"value": "one"},
            control=ToolExecutionControl(timeout_seconds=5, max_output_chars=4000),
        )
        second = client.call_tool(
            "echo",
            {"value": "two"},
            control=ToolExecutionControl(timeout_seconds=5, max_output_chars=4000),
        )
        assert json.loads(first.content)["pid"] == json.loads(second.content)["pid"]
        assert json.loads(second.content)["calls"] == 2
        assert len(attached) == 2
        assert len(adapter._process_names) == 1
        create = calls[0][0]
        assert create[:2] == ["docker", "create"]
        assert create[create.index("--entrypoint") + 1] == "python"
        assert create[-2:] == [adapter.image, "server.py"]
        assert "--interactive" in create and "--init" in create
        assert create[create.index("--pull") + 1] == "never"
        assert create[create.index("--network") + 1] == "none"
        assert "--read-only" in create and "ALL" in create
        assert "SERVER_KEY=explicit" in create
        assert "SERVER_KEY" not in calls[0][1]["env"]
        assert attached[0].args[:3] == ["start", "--attach", "--interactive"]
    finally:
        client.close()
        adapter.close_processes()
    assert not adapter._process_names
    assert sum(argv[1] == "rm" for argv, _ in calls) == 2


def test_deadline_removes_owned_container(docker_bridge, tmp_path):
    adapter, calls, _ = docker_bridge
    client = StdioMCPClient(
        StdioServerConfig("python"), cwd=tmp_path, sandbox_adapter=adapter
    )
    client.list_tools()
    result = client.call_tool(
        "slow",
        {},
        control=ToolExecutionControl(timeout_seconds=0.15, max_output_chars=4000),
    )
    assert result.metadata["execution_status"] == "timeout"
    assert client._portal is None
    assert not adapter._process_names
    assert calls[-1][0][1] == "rm"


def test_creation_failure_never_attaches_and_still_removes(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 1 if argv[1] == "create" else 0, "", "private detail"
        )

    adapter = DockerSandboxAdapter(tmp_path, lifecycle_runner=runner)
    client = StdioMCPClient(
        StdioServerConfig("python", required=False),
        cwd=tmp_path,
        sandbox_adapter=adapter,
    )
    with pytest.raises(MCPConnectionError):
        client.list_tools()
    assert [argv[1] for argv in calls] == ["create", "rm"]
    assert not adapter._process_names
    assert client._portal is None


def test_cleanup_failure_is_retained_for_retry(tmp_path):
    calls = []

    def fail(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "daemon gone")

    adapter = DockerSandboxAdapter(tmp_path, lifecycle_runner=fail)
    adapter._process_names.add("repoagent-mcp-test")
    with pytest.raises(SandboxConfigurationError, match="cleanup"):
        adapter.close_processes()
    assert adapter._process_names == {"repoagent-mcp-test"}
    adapter._lifecycle_runner = lambda argv, **kwargs: subprocess.CompletedProcess(
        argv, 0, "", ""
    )
    adapter.close_processes()
    adapter.close_processes()
    assert not adapter._process_names


def test_absent_container_only_accepted_when_daemon_confirms(tmp_path):
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1 if argv[1] == "rm" else 0, "", "")

    adapter = DockerSandboxAdapter(tmp_path, lifecycle_runner=runner)
    adapter._process_names.add("repoagent-mcp-test")
    adapter.close_processes()
    assert not adapter._process_names


def test_config_binds_process_to_runtime_sandbox(docker_bridge, tmp_path):
    adapter, _, _ = docker_bridge
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"docs": {"command": "python"}}}))
    clients = load_mcp_servers(
        config, cwd=tmp_path, sandbox_adapter=adapter, require_isolation=True
    )
    assert clients["docs"].sandbox_adapter is adapter
    with pytest.raises(MCPConnectionError, match="active sandbox"):
        MCPManager(clients, sandbox_adapter=DockerSandboxAdapter(tmp_path))
    agent = RepoAgent(
        FakeModelClient(()),
        WorkspaceContext.build(tmp_path),
        SessionStore(tmp_path / "sessions"),
        sandbox_adapter=adapter,
        require_isolation=True,
        mcp_servers=clients,
        approval_policy="auto",
    )
    assert agent.execute_tool("mcp_docs_echo", {"value": "ok"}).status == "ok"
    asyncio.run(agent.aclose())
    assert not adapter._process_names


@pytest.mark.parametrize(
    "command,args,env",
    [("", [], {}), ("python", [1], {}), ("python", [], {"BAD=KEY": "x"})],
)
def test_invalid_process_config_never_reaches_docker(tmp_path, command, args, env):
    pytest.importorskip("mcp")
    calls = []
    adapter = DockerSandboxAdapter(
        tmp_path, lifecycle_runner=lambda *args, **kwargs: calls.append(args)
    )

    async def run():
        async with adapter.start_process(command, args, cwd=tmp_path, env=env):
            pytest.fail("must not start")

    with pytest.raises(SandboxConfigurationError):
        asyncio.run(run())
    assert not calls


def test_cancel_during_create_removes_without_attach(tmp_path, monkeypatch):
    anyio = pytest.importorskip("anyio")
    pytest.importorskip("mcp")
    from mcp.client import stdio

    calls = []

    def runner(argv, **kwargs):
        calls.append(argv[1])
        if argv[1] == "create":
            time.sleep(0.1)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(
        stdio,
        "stdio_client",
        lambda *a, **k: pytest.fail("cancelled create must not attach"),
    )
    adapter = DockerSandboxAdapter(tmp_path, lifecycle_runner=runner)

    async def run():
        with anyio.move_on_after(0.02):
            async with adapter.start_process("python", [], cwd=tmp_path, env={}):
                pytest.fail("cancelled create must not yield")

    anyio.run(run)
    assert calls == ["create", "rm"]
    assert not adapter._process_names


def test_optional_server_cannot_hide_sandbox_failure(tmp_path):
    pytest.importorskip("mcp")

    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, 1 if argv[1] == "create" else 0, "", ""
        )

    adapter = DockerSandboxAdapter(tmp_path, lifecycle_runner=runner)
    client = StdioMCPClient(
        StdioServerConfig("python", required=False),
        cwd=tmp_path,
        sandbox_adapter=adapter,
    )
    manager = MCPManager({"docs": client}, sandbox_adapter=adapter)
    with pytest.raises(MCPConnectionError) as exc:
        manager.discover()
    assert exc.value.code == "sandbox_failed"
    assert manager.diagnostics[0]["error_code"] == "sandbox_failed"


def test_runtime_retries_adapter_cleanup_despite_manager_failure(
    docker_bridge, tmp_path, monkeypatch
):
    adapter, _, _ = docker_bridge
    agent = RepoAgent(
        FakeModelClient(()),
        WorkspaceContext.build(tmp_path),
        SessionStore(tmp_path / "sessions"),
        sandbox_adapter=adapter,
    )
    closed = []

    def fail():
        raise MCPConnectionError("cleanup failure")

    monkeypatch.setattr(agent.mcp_manager, "close", fail)
    monkeypatch.setattr(adapter, "close_processes", lambda: closed.append(True))
    with pytest.raises(MCPConnectionError):
        asyncio.run(agent.aclose())
    assert closed == [True]


def test_mcp_check_uses_selected_sandbox(docker_bridge, tmp_path, monkeypatch, capsys):
    from repoagent.cli import main
    from repoagent import sandbox

    adapter, _, attached = docker_bridge
    monkeypatch.setattr(sandbox, "build_sandbox_adapter", lambda *a, **k: adapter)
    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"docs": {"command": "python"}}}))
    assert (
        main(
            [
                "mcp",
                "check",
                "--config",
                str(config),
                "--cwd",
                str(tmp_path),
                "--backend",
                "docker",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["sandbox_identity"] == adapter.identity
    assert len(attached) == 1
    assert not adapter._process_names
