import concurrent.futures
import subprocess
import threading

import pytest

from repoagent import CancellationToken
from repoagent.sandbox import SandboxConfigurationError, build_sandbox_adapter
from repoagent.sandbox_session import PersistentDockerSandboxAdapter
from repoagent.tool_execution import ProcessOutcome, ToolExecutionControl


def control(timeout=5, token=None):
    return ToolExecutionControl(
        timeout_seconds=timeout, max_output_chars=4000, cancellation_token=token
    )


def completed(code=0):
    return ProcessOutcome("completed", code, "ok", "", 2, 0, False)


@pytest.fixture
def session(tmp_path):
    lifecycle, executions = [], []
    containers = set()

    def lifecycle_runner(argv, **kwargs):
        lifecycle.append((argv, kwargs))
        output = ""
        if argv[1] == "info":
            output = "engine-1"
        elif argv[1] == "create":
            containers.add("a" * 64)
        elif argv[1] == "container":
            output = "\n".join(containers)
        elif argv[1] == "rm":
            containers.discard(argv[-1])
        elif argv[1] == "exec":
            output = "/usr/local/bin/python\n"
        return subprocess.CompletedProcess(argv, 0, output, "")

    def process_runner(argv, **kwargs):
        executions.append((argv, kwargs))
        return completed()

    adapter = PersistentDockerSandboxAdapter(
        tmp_path, lifecycle_runner=lifecycle_runner, process_runner=process_runner,
        ownership_root=tmp_path.parent / "owners",
    )
    return adapter, lifecycle, executions


def execute(adapter, **kwargs):
    return adapter.execute("echo ok", cwd=adapter.workspace, env={}, control=control(), **kwargs)


def test_reuses_container_with_per_call_cwd_env_and_explicit_stop(session, tmp_path):
    adapter, lifecycle, executions = session
    nested = tmp_path / "nested"
    nested.mkdir()
    adapter.start()
    name = adapter.container_name
    execute(adapter)
    adapter.execute("pwd", cwd=nested, env={"LANG": "C", "API_KEY": "secret"}, control=control())
    adapter.start()
    assert [argv[1] for argv, _ in lifecycle if argv[1] not in {"exec", "info"}] == ["create", "start"]
    create = next(argv for argv, _ in lifecycle if argv[1] == "create")
    assert "--read-only" in create and "--init" in create
    assert create[create.index("--pull") + 1] == "never"
    assert create[create.index("--network") + 1] == "none"
    assert all(name in argv for argv, _ in executions)
    assert executions[1][0][3].endswith("/nested")
    assert "LANG=C" in executions[1][0]
    assert not any("API_KEY" in value for value in executions[1][0])
    adapter.close_processes()
    adapter.stop()
    assert [argv[1] for argv, _ in lifecycle].count("rm") == 1
    assert adapter.container_name is None
    adapter.start()
    assert adapter.container_name != name
    adapter.stop()


def test_concurrent_start_creates_one_container(session):
    adapter, lifecycle, _ = session
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: adapter.start(), range(16)))
    assert [argv[1] for argv, _ in lifecycle] == ["info", "create", "start", "exec"]
    adapter.stop()


@pytest.mark.parametrize("status", ["timeout", "cancelled"])
def test_interruption_cleans_only_execution_and_preserves_session(session, status):
    adapter, lifecycle, _ = session
    adapter._process_runner = lambda *args, **kwargs: ProcessOutcome(status, None, "", "", 0, 0, False)
    assert execute(adapter).status == status
    assert lifecycle[-1][0][1] == "exec"
    name = adapter.container_name
    adapter._process_runner = lambda *args, **kwargs: completed()
    assert execute(adapter).status == "completed"
    assert adapter.container_name == name
    adapter.stop()


def test_nonzero_shell_exit_does_not_reset_state(session):
    adapter, lifecycle, _ = session
    adapter._process_runner = lambda *args, **kwargs: completed(1)
    assert execute(adapter).exit_code == 1
    assert execute(adapter).exit_code == 1
    assert [argv[1] for argv, _ in lifecycle if argv[1] not in {"exec", "info"}] == ["create", "start"]
    adapter.stop()


def test_runner_exception_cleans_execution_without_invalidating_session(session):
    adapter, lifecycle, _ = session

    def fail(*args, **kwargs):
        raise OSError("CLI disconnected")

    adapter._process_runner = fail
    with pytest.raises(OSError):
        execute(adapter)
    assert lifecycle[-1][0][1] == "exec"
    adapter.start()
    adapter.stop()


def test_failed_cleanup_keeps_ownership_for_retry(session):
    adapter, _, _ = session
    adapter.start()
    name = adapter.container_name
    original = adapter._lifecycle_runner
    adapter._lifecycle_runner = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", "unavailable")
    with pytest.raises(SandboxConfigurationError, match="identity unavailable"):
        adapter.stop()
    assert adapter.container_name == name
    assert name in adapter._process_names
    with pytest.raises(SandboxConfigurationError, match="state was lost"):
        adapter.start()
    adapter._lifecycle_runner = original
    adapter.stop()
    assert not adapter._process_names


@pytest.mark.parametrize("stage", ["create", "start", "exec"])
def test_partial_start_is_removed_and_latched(session, stage):
    adapter, lifecycle, _ = session
    original = adapter._lifecycle_runner

    def fail(argv, **kwargs):
        result = original(argv, **kwargs)
        result.returncode = int(argv[1] == stage)
        return result

    adapter._lifecycle_runner = fail
    with pytest.raises(SandboxConfigurationError, match="start failed"):
        adapter.start()
    assert lifecycle[-1][0][1] == "rm"
    assert adapter.container_name is None
    with pytest.raises(SandboxConfigurationError, match="state was lost"):
        adapter.start()


def test_cancelled_waiter_does_not_invalidate_running_peer(session):
    adapter, lifecycle, executions = session
    adapter.start()
    token = CancellationToken()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        with adapter._session_lock:
            future = pool.submit(
                adapter.execute, "must not run", cwd=adapter.workspace,
                env={}, control=control(token=token),
            )
            token.cancel()
            assert future.result(timeout=1).status == "cancelled"
    assert not executions
    assert [argv[1] for argv, _ in lifecycle] == ["info", "create", "start", "exec"]
    adapter.stop()


def test_expired_control_does_not_start_container(session):
    adapter, lifecycle, _ = session
    expired = control()
    expired.deadline = 0
    result = adapter.execute("x", cwd=adapter.workspace, env={}, control=expired)
    assert result.status == "timeout"
    assert not lifecycle


def test_start_lock_wait_is_bounded(session):
    adapter, lifecycle, _ = session
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        with adapter._session_lock:
            future = pool.submit(adapter.start, timeout=0.02)
            with pytest.raises(SandboxConfigurationError, match="lock timed out"):
                future.result(timeout=1)
    assert not lifecycle


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf")])
def test_start_validates_deadline(session, timeout):
    adapter, lifecycle, _ = session
    with pytest.raises(SandboxConfigurationError, match="timeout"):
        adapter.start(timeout=timeout)
    assert not lifecycle


def test_no_mcp_fallback_and_no_product_default_change(session):
    adapter, _, _ = session
    assert adapter.supports_process_spawning is True
    assert type(build_sandbox_adapter("docker", adapter.workspace)) is not type(adapter)
    assert isinstance(build_sandbox_adapter("docker-persistent", adapter.workspace), type(adapter))
    prompt = adapter.prompt_context(cwd=adapter.workspace)
    assert "cd and export do not carry over" in prompt
    assert "explicit lifecycle reset" in prompt


def test_stop_waits_for_active_execution_before_removal(session):
    adapter, lifecycle, _ = session
    entered, finish = threading.Event(), threading.Event()

    def run(*args, **kwargs):
        entered.set()
        assert finish.wait(2)
        assert not any(argv[1] == "rm" for argv, _ in lifecycle)
        return completed()

    adapter._process_runner = run
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        execution = pool.submit(execute, adapter)
        assert entered.wait(2)
        stopped = pool.submit(adapter.stop)
        finish.set()
        assert execution.result(timeout=2).status == "completed"
        stopped.result(timeout=2)
    assert lifecycle[-1][0][1] == "rm"


def test_mcp_exec_handle_has_explicit_env_and_is_generation_bound(session):
    adapter, lifecycle, _ = session
    handle, argv = adapter.prepare_process(
        "service", ["arg with spaces"], cwd=adapter.workspace,
        env={"EXPLICIT": "yes"}, timeout=2,
    )
    assert argv[:2] == ["exec", "--interactive"]
    assert "EXPLICIT=yes" in argv
    assert argv[-2:] == ["service", "arg with spaces"]
    assert handle[0] in argv and handle[1] in argv
    adapter.stop()
    adapter.start()
    count = len(lifecycle)
    adapter.finish_process(handle)
    assert len(lifecycle) == count
    adapter.stop()


def test_failed_exec_cleanup_invalidates_shared_container(session):
    adapter, lifecycle, _ = session
    handle, _ = adapter.prepare_process("service", [], cwd=adapter.workspace, env={}, timeout=2)
    original = adapter._lifecycle_runner

    def fail(argv, **kwargs):
        result = original(argv, **kwargs)
        if "cancel" in argv:
            result.returncode = 1
        return result

    adapter._lifecycle_runner = fail
    with pytest.raises(SandboxConfigurationError, match="execution cleanup failed"):
        adapter.finish_process(handle)
    assert lifecycle[-1][0][1] == "rm"
    with pytest.raises(SandboxConfigurationError, match="state was lost"):
        adapter.start()


def test_cancel_during_start_does_not_kill_shared_peer(session):
    adapter, lifecycle, executions = session
    adapter.start()
    token = CancellationToken()
    original = adapter._start_locked

    def start(deadline):
        original(deadline)
        token.cancel()

    adapter._start_locked = start
    result = adapter.execute("x", cwd=adapter.workspace, env={}, control=control(token=token))
    assert result.status == "cancelled"
    assert not executions
    assert not any(argv[1] == "rm" for argv, _ in lifecycle)
    adapter.stop()


@pytest.mark.parametrize("command,args,env", [
    ("", [], {}), ("python", "bad", {}), ("python", ["\0"], {}),
    ("python", [], {"INVALID-NAME": "x"}), ("python", [], {"X": 1}),
])
def test_process_validation_precedes_start(session, command, args, env):
    adapter, lifecycle, _ = session
    with pytest.raises(SandboxConfigurationError):
        adapter.prepare_process(command, args, cwd=adapter.workspace, env=env, timeout=2)
    assert not lifecycle
