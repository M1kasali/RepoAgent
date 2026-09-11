"""Opt-in persistent-shell acceptance against a real local Docker daemon."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import threading
import time

import pytest

from repoagent import CancellationToken, FakeModelClient, RepoAgent, SessionStore, WorkspaceContext
from repoagent.evaluation.container import wsl_windows_path
from repoagent.sandbox_session import PersistentDockerSandboxAdapter
from repoagent.tool_execution import ToolExecutionControl
from repoagent.mcp_transport import MCPConnectionError, StdioMCPClient, StdioServerConfig


pytestmark = pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"),
    reason="explicit local Docker acceptance required",
)


@pytest.fixture
def persistent():
    root = Path(__file__).resolve().parents[1]
    artifacts = root / "artifacts" / "sandbox-session-live"
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="case-", dir=artifacts) as directory:
        adapter = PersistentDockerSandboxAdapter(
            root, executable=os.environ["REPOAGENT_TEST_DOCKER"],
            image=os.environ.get("REPOAGENT_TEST_DOCKER_IMAGE", "python:3.12-slim"),
            workspace_path_converter=wsl_windows_path
            if os.environ.get("REPOAGENT_TEST_DOCKER_WSL") == "1" else None,
        )
        try:
            yield adapter, Path(directory)
        finally:
            adapter.stop()


def run(adapter, cwd, command, timeout=10, token=None):
    return adapter.execute(
        command, cwd=cwd, env={},
        control=ToolExecutionControl(
            timeout_seconds=timeout, max_output_chars=4000, cancellation_token=token
        ),
    )


def assert_absent(adapter, name):
    result = subprocess.run(
        [adapter.executable, "container", "ls", "--all", "--filter",
         f"name=^/{name}$", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=10, check=True,
    )
    assert not result.stdout.strip()


def test_real_scratch_persistence_reset_and_shell_local_state(persistent):
    adapter, cwd = persistent
    result = run(adapter, cwd, "printf shared > /tmp/session-state; printf durable > workspace-state; export LOCAL_ONLY=yes; cd /tmp")
    assert result.exit_code == 0, result
    name = adapter.container_name
    result = run(adapter, cwd, 'cat /tmp/session-state; printf "|%s|" "${LOCAL_ONLY-unset}"; pwd')
    assert result.exit_code == 0, result
    assert result.stdout == f"shared|unset|{adapter._guest_paths(cwd)[1]}\n"
    assert adapter.container_name == name
    adapter.stop()
    assert_absent(adapter, name)
    result = run(adapter, cwd, "test ! -e /tmp/session-state && cat workspace-state")
    assert result.exit_code == 0 and result.stdout == "durable", result
    assert adapter.container_name != name


@pytest.mark.parametrize("cancel", [False, True])
def test_real_interruption_reaps_daemon_children(persistent, cancel):
    adapter, cwd = persistent
    adapter.start()
    name = adapter.container_name
    token = CancellationToken()
    timer = threading.Timer(1.0, token.cancel) if cancel else None
    if timer:
        timer.start()
    try:
        result = run(
            adapter, cwd,
            "printf started > started; (sleep 3; printf leaked > late-effect) & wait",
            timeout=10 if cancel else 1.0, token=token,
        )
    finally:
        if timer:
            timer.cancel()
            timer.join()
    assert result.status == ("cancelled" if cancel else "timeout"), result
    assert (cwd / "started").exists(), "test must reach guest code before interruption"
    assert adapter.container_name == name
    time.sleep(3.2)
    assert not (cwd / "late-effect").exists()
    assert run(adapter, cwd, "echo still-usable").exit_code == 0


def test_real_runtime_shutdown_closes_persistent_shell(persistent):
    adapter, cwd = persistent
    agent = RepoAgent(
        model_client=FakeModelClient(()), workspace=WorkspaceContext.build(cwd),
        session_store=SessionStore(cwd / ".sessions"),
        approval_policy="auto", sandbox_adapter=adapter, require_isolation=True,
    )
    try:
        result = agent.execute_tool("run_shell", {"command": "printf runtime > /tmp/state"})
        assert result.status == "ok", result
        name = adapter.container_name
        assert name
    finally:
        asyncio.run(agent.aclose())
    assert_absent(adapter, name)
    assert adapter.container_name is None


@pytest.fixture
def shared_clients(persistent):
    pytest.importorskip("mcp")
    adapter, cwd = persistent
    for filename in ("mcp_server.py", "mcp_shared_server.py"):
        shutil.copyfile(adapter.workspace / "tests" / "fixtures" / filename, cwd / filename)
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    root = adapter._guest_paths(cwd)[0]
    config = StdioServerConfig(
        "python", args=["-B", "mcp_shared_server.py"],
        env={"PYTHONPATH": f"{root}/.venv/lib/{version}/site-packages"},
        startup_timeout=10,
    )
    clients = [StdioMCPClient(config, cwd=cwd, sandbox_adapter=adapter) for _ in range(2)]
    try:
        yield adapter, cwd, clients
    finally:
        for client in clients:
            client.close()


def mcp_call(client, name, args=None, timeout=10, token=None):
    return client.call_tool(name, args or {}, control=ToolExecutionControl(
        timeout_seconds=timeout, max_output_chars=4000, cancellation_token=token
    ))


def echo(client):
    result = mcp_call(client, "echo", {"value": "hello"})
    assert result.metadata.get("exit_code", 0) == 0, result
    return json.loads(result.content)


def test_real_shell_mcp_exchange_and_independent_close(shared_clients):
    adapter, cwd, (first, second) = shared_clients
    assert run(adapter, cwd, "printf shell > /tmp/shared-mcp-state").exit_code == 0
    name = adapter.container_name
    assert len(first.list_tools()) == 6
    result = mcp_call(first, "exchange", {"value": "mcp"})
    assert result.content == "shell", result
    assert run(adapter, cwd, "cat /tmp/shared-mcp-state").stdout == "mcp"
    first_info, peer = echo(first), echo(second)
    assert first_info["pid"] != peer["pid"]
    first.close()
    assert adapter.container_name == name
    assert echo(second)["pid"] == peer["pid"]
    assert run(adapter, cwd, "cat /tmp/shared-mcp-state").stdout == "mcp"
    assert not run(adapter, cwd, f"test -d /proc/{first_info['pid']}").exit_code == 0


@pytest.mark.parametrize("mode", ["timeout", "cancelled", "shell-timeout", "crash"])
def test_real_shared_cancellation_and_crash_preserve_peer(shared_clients, mode):
    adapter, cwd, (first, second) = shared_clients
    echo(first)
    peer = echo(second)
    name = adapter.container_name
    assert run(adapter, cwd, "printf retained > /tmp/shared-mcp-state").exit_code == 0
    token = CancellationToken()
    timer = threading.Timer(0.15, token.cancel) if mode == "cancelled" else None
    try:
        if timer:
            timer.start()
        if mode == "shell-timeout":
            result = run(adapter, cwd, "sleep 30", timeout=0.3)
            assert result.status == "timeout", result
        elif mode == "crash":
            result = mcp_call(first, "crash")
            assert result.metadata["exit_code"] == 1, result
        else:
            result = mcp_call(first, "slow", timeout=5 if timer else 0.15, token=token)
            assert result.metadata["execution_status"] == mode, result
    finally:
        if timer:
            timer.cancel()
            timer.join()
    time.sleep(1)
    assert not (cwd / "late-effect.txt").exists()
    assert adapter.container_name == name
    assert echo(second)["pid"] == peer["pid"]
    assert run(adapter, cwd, "cat /tmp/shared-mcp-state").stdout == "retained"
    assert echo(first)["value"] == "hello"


def test_real_shared_handshake_timeout_preserves_peer(shared_clients):
    adapter, cwd, (_, peer) = shared_clients
    info = echo(peer)
    bad = StdioMCPClient(
        replace(peer.config, args=["-c", "import time; time.sleep(30)"], startup_timeout=0.5),
        cwd=cwd, sandbox_adapter=adapter,
    )
    try:
        with pytest.raises(MCPConnectionError):
            bad.list_tools()
    finally:
        bad.close()
    assert echo(peer)["pid"] == info["pid"]
    assert run(adapter, cwd, "echo healthy").exit_code == 0


def test_real_cancel_before_launch_fences_delayed_exec(persistent):
    adapter, cwd = persistent
    handle, argv = adapter.prepare_process(
        "sh", ["-c", "printf leaked > late-start"], cwd=cwd, env={}, timeout=10
    )
    adapter.finish_process(handle)
    result = subprocess.run(
        [adapter.executable, *argv], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 125, result
    assert not (cwd / "late-start").exists()
    assert run(adapter, cwd, "echo healthy").exit_code == 0


def test_real_mixed_runtime_shutdown_and_reuse(shared_clients):
    adapter, cwd, (client, _) = shared_clients
    agent = RepoAgent(
        FakeModelClient(()), WorkspaceContext.build(cwd), SessionStore(cwd / ".sessions"),
        approval_policy="auto", sandbox_adapter=adapter, require_isolation=True,
        mcp_servers={"shared": client},
    )
    try:
        shell = agent.execute_tool("run_shell", {"command": "printf runtime > /tmp/shared-mcp-state"})
        assert shell.status == "ok", shell
        exchange = agent.execute_tool("mcp_shared_exchange", {"value": "changed"})
        assert exchange.status == "ok" and "runtime" in exchange.content, exchange
        name = adapter.container_name
        asyncio.run(agent.aclose())
        assert_absent(adapter, name)
        restarted = agent.execute_tool("mcp_shared_exchange", {"value": "new"})
        assert restarted.status == "ok", restarted
        assert adapter.container_name != name
        assert run(adapter, cwd, "cat /tmp/shared-mcp-state").stdout == "new"
    finally:
        asyncio.run(agent.aclose())


def test_real_explicit_path_cannot_replace_supervisor(persistent):
    adapter, cwd = persistent
    handle, argv = adapter.prepare_process(
        "/bin/sh", ["-c", "printf '%s' \"$PATH\""], cwd=cwd,
        env={"PATH": "/custom-service-path"}, timeout=10,
    )
    try:
        result = subprocess.run(
            [adapter.executable, *argv], stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result
        assert result.stdout == "/custom-service-path"
    finally:
        adapter.finish_process(handle)


def test_real_failed_runtime_discovery_releases_container(shared_clients):
    adapter, cwd, (client, _) = shared_clients
    adapter.start()
    name = adapter.container_name
    bad = StdioMCPClient(
        replace(client.config, args=["-c", "raise SystemExit(17)"]),
        cwd=cwd, sandbox_adapter=adapter,
    )
    try:
        with pytest.raises(MCPConnectionError):
            RepoAgent(
                FakeModelClient(()), WorkspaceContext.build(cwd), SessionStore(cwd / ".sessions"),
                sandbox_adapter=adapter, require_isolation=True, mcp_servers={"bad": bad},
            )
        assert adapter.container_name is None
        assert_absent(adapter, name)
    finally:
        bad.close()


def test_real_host_sigkill_orphan_recovered_without_harming_live_peer(persistent, tmp_path):
    adapter, cwd = persistent
    from repoagent.sandbox_ownership import SandboxOwnership

    state = tmp_path / "owners"
    adapter.ownership = SandboxOwnership(adapter.workspace, root=state)
    adapter.start()
    live_name = adapter.container_name
    code = (
        "import os, signal, sys; "
        "from repoagent.sandbox_session import PersistentDockerSandboxAdapter; "
        "from repoagent.evaluation.container import wsl_windows_path; "
        "a=PersistentDockerSandboxAdapter(sys.argv[1], executable=sys.argv[2], "
        "ownership_root=sys.argv[3], workspace_path_converter=wsl_windows_path "
        "if sys.argv[4]=='1' else None); "
        "a.start(); print(a.container_name, flush=True); os.kill(os.getpid(), signal.SIGKILL)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(adapter.workspace), adapter.executable,
         str(state), os.environ.get("REPOAGENT_TEST_DOCKER_WSL", "0")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == -9, result
    orphan = result.stdout.strip()
    assert orphan.startswith("repoagent-session-"), result
    try:
        rows = adapter.ownership.reconcile(adapter)
        assert {row["status"] for row in rows} == {"active", "removed"}, rows
        assert_absent(adapter, orphan)
        assert adapter.container_name == live_name
        assert run(adapter, cwd, "echo retained").exit_code == 0
    finally:
        adapter.ownership.reconcile(adapter)


def test_real_delayed_create_is_reclaimed_after_initial_absence(persistent, tmp_path):
    adapter, _ = persistent
    from repoagent.sandbox import SandboxConfigurationError
    from repoagent.sandbox_ownership import SandboxOwnership

    adapter.ownership = SandboxOwnership(adapter.workspace, root=tmp_path / "owners")
    original = adapter._lifecycle_runner
    delayed = []

    def timeout_create(argv, **kwargs):
        if argv[1] == "create":
            delayed.append(argv)
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return original(argv, **kwargs)

    adapter._lifecycle_runner = timeout_create
    with pytest.raises(SandboxConfigurationError):
        adapter.start()
    adapter._lifecycle_runner = original
    assert adapter.ownership.reconcile(adapter)[0]["status"] == "watching"
    assert len(delayed) == 1
    name = delayed[0][delayed[0].index("--name") + 1]
    try:
        # Controlled late daemon completion, not a claim of reproducing a real
        # Docker engine outage: the captured create runs after the absence check.
        subprocess.run(delayed[0], check=True, capture_output=True, text=True, timeout=20)
        assert adapter.ownership.reconcile(adapter)[0]["status"] == "removed"
        assert_absent(adapter, name)
    finally:
        adapter.ownership.reconcile(adapter)


def test_real_reconcile_never_deletes_foreign_labels(persistent, tmp_path):
    adapter, _ = persistent
    from repoagent.sandbox_ownership import SandboxOwnership

    adapter.ownership = SandboxOwnership(adapter.workspace, root=tmp_path / "owners")
    import uuid
    name = f"repoagent-session-{uuid.uuid4().hex}"
    adapter.ownership.claim(adapter, name)
    adapter.ownership.release()
    subprocess.run(
        [adapter.executable, "create", "--name", name, "--pull", "never",
         "--label", "repoagent.owner=foreign", "--entrypoint", "sh", adapter.image, "-c", "true"],
        check=True, capture_output=True, text=True, timeout=20,
    )
    try:
        assert adapter.ownership.reconcile(adapter)[0]["status"] == "watching"
        inspect = subprocess.run(
            [adapter.executable, "inspect", name], capture_output=True, text=True, timeout=10
        )
        assert inspect.returncode == 0
    finally:
        subprocess.run([adapter.executable, "rm", "--force", name], check=True, capture_output=True, timeout=10)
