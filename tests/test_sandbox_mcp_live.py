"""Opt-in local Docker acceptance. No model, image pull or dependency install."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
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
from repoagent.evaluation.container import wsl_windows_path
from repoagent.mcp_transport import (
    MCPConnectionError,
    StdioMCPClient,
    StdioServerConfig,
)
from repoagent.sandbox import DockerSandboxAdapter
from repoagent.tool_execution import ToolExecutionControl


pytestmark = pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"),
    reason="explicit local Docker acceptance required",
)


@pytest.fixture
def live_process():
    pytest.importorskip("mcp")
    root = Path(__file__).resolve().parents[1]
    artifacts = root / "artifacts" / "sandbox-mcp-live"
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="case-", dir=artifacts) as directory:
        cwd = Path(directory)
        shutil.copyfile(root / "tests/fixtures/mcp_server.py", cwd / "server.py")
        adapter = DockerSandboxAdapter(
            root,
            executable=os.environ["REPOAGENT_TEST_DOCKER"],
            image=os.environ.get("REPOAGENT_TEST_DOCKER_IMAGE", "python:3.12-slim"),
            workspace_path_converter=wsl_windows_path
            if os.environ.get("REPOAGENT_TEST_DOCKER_WSL") == "1"
            else None,
        )
        # Reuse the installed SDK read from the workspace mount. The selected
        # image must match this venv's Linux Python ABI; never pip install here.
        guest_root = adapter._guest_paths(cwd)[0]
        version = f"python{sys.version_info.major}.{sys.version_info.minor}"
        config = StdioServerConfig(
            "python",
            args=["-B", "server.py"],
            env={
                "PYTHONPATH": f"{guest_root}/.venv/lib/{version}/site-packages",
                "EXPLICIT_VALUE": "sandbox",
                "FIXTURE_API_KEY": "local-fixture-secret",
            },
            startup_timeout=20,
        )
        client = StdioMCPClient(config, cwd=cwd, sandbox_adapter=adapter)
        try:
            yield adapter, client, cwd
        finally:
            try:
                client.close()
            finally:
                try:
                    adapter.close_processes()
                finally:
                    if adapter._process_names:
                        (artifacts / f"{cwd.name}-cleanup-pending.json").write_text(
                            json.dumps(
                                {
                                    "container_names": sorted(adapter._process_names),
                                    "docker_executable": adapter.executable,
                                    "status": "cleanup_unconfirmed",
                                },
                                indent=2,
                            )
                            + "\n"
                        )


def call(client, name, arguments=None, timeout=20, token=None):
    result = client.call_tool(
        name,
        arguments or {},
        control=ToolExecutionControl(
            timeout_seconds=timeout, max_output_chars=4000, cancellation_token=token
        ),
    )
    assert result.metadata.get("exit_code", 0) == 0, result
    return result


def assert_removed(adapter, names):
    assert names
    for name in names:
        result = subprocess.run(
            [
                adapter.executable,
                "container",
                "ls",
                "--all",
                "--filter",
                f"name=^/{name}$",
                "--format",
                "{{.Names}}",
            ],
            env=adapter._docker_environment(),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert not result.stdout.strip(), result.stdout


def test_real_container_discovery_reuse_and_shutdown(live_process, monkeypatch):
    adapter, client, cwd = live_process
    monkeypatch.setenv("UNRELATED_API_KEY", "must-not-cross")
    assert len(client.list_tools()) == 5
    assert not adapter._process_names
    first = json.loads(call(client, "echo", {"value": "first"}).content)
    second = json.loads(call(client, "echo", {"value": "second"}).content)
    assert first["pid"] == second["pid"]
    assert second["calls"] == 2
    assert first["cwd"] == adapter._guest_paths(cwd)[1]
    assert first["explicit"] == "sandbox" and first["inherited_secret"] is None
    assert "local-fixture-secret" not in call(client, "secret").content
    names = set(adapter._process_names)
    inspect = subprocess.run(
        [adapter.executable, "inspect", *names],
        env=adapter._docker_environment(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert inspect.returncode == 0
    container = json.loads(inspect.stdout)[0]
    assert container["HostConfig"]["NetworkMode"] == "none"
    assert container["HostConfig"]["ReadonlyRootfs"] is True
    assert container["Config"]["Tty"] is False
    client.close()
    assert_removed(adapter, names)


@pytest.mark.parametrize("mode", ["timeout", "cancelled"])
def test_real_container_stop_kills_remote_side_effect(live_process, mode):
    adapter, client, cwd = live_process
    client.list_tools()
    call(client, "echo", {"value": "warmup"})
    names = set(adapter._process_names)
    token = CancellationToken()
    timer = threading.Timer(0.15, token.cancel) if mode == "cancelled" else None
    try:
        if timer is not None:
            timer.start()
        result = call(client, "slow", timeout=5 if timer else 0.15, token=token)
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()
    assert result.metadata["execution_status"] == mode
    assert client._portal is None
    assert_removed(adapter, names)
    time.sleep(1)
    assert not (cwd / "late-effect.txt").exists()


def test_real_container_crash_cleanup_and_reconnect(live_process):
    adapter, client, _ = live_process
    client.list_tools()
    call(client, "echo", {"value": "before crash"})
    names = set(adapter._process_names)
    result = client.call_tool(
        "crash",
        {},
        control=ToolExecutionControl(timeout_seconds=5, max_output_chars=4000),
    )
    assert result.metadata["exit_code"] == 1
    assert client._portal is None
    assert_removed(adapter, names)
    restarted = json.loads(call(client, "echo", {"value": "after crash"}).content)
    assert restarted["calls"] == 1
    assert not names.intersection(adapter._process_names)


def test_real_container_handshake_timeout_removes_container(live_process):
    adapter, client, cwd = live_process
    created = []
    original = adapter._lifecycle_runner

    def record(argv, **kwargs):
        if argv[1] == "create":
            created.append(argv[argv.index("--name") + 1])
        return original(argv, **kwargs)

    adapter._lifecycle_runner = record
    bad = StdioMCPClient(
        replace(
            client.config, args=["-c", "import time; time.sleep(30)"], startup_timeout=1
        ),
        cwd=cwd,
        sandbox_adapter=adapter,
    )
    try:
        with pytest.raises(MCPConnectionError):
            bad.list_tools()
        assert bad._portal is None
        assert not adapter._process_names
        assert_removed(adapter, created)
    finally:
        bad.close()
        adapter._lifecycle_runner = original


def test_real_agent_isolated_mcp_entry_and_close(live_process):
    adapter, client, cwd = live_process
    agent = RepoAgent(
        FakeModelClient(()),
        WorkspaceContext.build(adapter.workspace),
        SessionStore(cwd / "sessions"),
        sandbox_adapter=adapter,
        require_isolation=True,
        mcp_servers={"docs": client},
        approval_policy="auto",
    )
    try:
        assert agent.execute_tool("mcp_docs_echo", {"value": "runtime"}).status == "ok"
        names = set(adapter._process_names)
    finally:
        asyncio.run(agent.aclose())
    assert_removed(adapter, names)
