"""Integration contracts at the Pico executor / RepoAgent host boundary."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pydantic")

pytest.importorskip("anyio")
import anyio

from repoagent.boxlite_adapter import BoxliteSandboxAdapter, load_boxlite_config
from repoagent.boxlite_sandbox import ExecResult, SandboxConfig
from repoagent.sandbox import SandboxConfigurationError, build_sandbox_adapter
from repoagent.tool_execution import ToolExecutionControl


class Executor:
    def __init__(self):
        self.starts = self.stops = 0
        self.calls = []
        self.loops = []
        self._process_tasks = []
        self._process_executions = []
        self.started_command = threading.Event()

    async def start(self):
        self.starts += 1
        self.loops.append(asyncio.get_running_loop())

    async def exec(self, command, **kw):
        self.calls.append((command, kw))
        self.loops.append(asyncio.get_running_loop())
        self.started_command.set()
        if command == "block":
            await asyncio.Future()
        return ExecResult("abcdef", "error", 0)

    async def start_process(self, command, args, env=None):
        self.loops.append(asyncio.get_running_loop())
        tx, rx = anyio.create_memory_object_stream(16)
        write, read = anyio.create_memory_object_stream(16)

        async def echo():
            async with read, tx:
                async for message in read:
                    await tx.send(message)

        self._process_tasks.append(asyncio.create_task(echo()))
        handle = type("Execution", (), {})()
        handle.kill = AsyncMock()
        self._process_executions.append(handle)
        return rx, write

    async def stop(self):
        self.stops += 1
        for task in self._process_tasks:
            task.cancel()
        await asyncio.gather(*self._process_tasks, return_exceptions=True)


def adapter(tmp_path, executor=None):
    executor = executor or Executor()
    return BoxliteSandboxAdapter(
        tmp_path, executor_factory=lambda *a, **kw: executor
    ), executor


def control(**kw):
    return ToolExecutionControl(timeout_seconds=2, max_output_chars=8, **kw)


def test_shell_reuses_eager_vm_and_forwards_arguments(tmp_path):
    box, executor = adapter(tmp_path)
    try:
        box.verify_available()
        for _ in range(2):
            result = box.execute(
                "echo hi", cwd=tmp_path, env={"X": "y"}, control=control()
            )
            assert result.stdout == "abcdef"
            assert result.stderr == "er"
            assert result.output_truncated
        assert executor.starts == 1
        assert executor.calls[0][1]["cwd"] == str(tmp_path)
        assert executor.calls[0][1]["env"] == {"X": "y"}
        assert len(set(executor.loops)) == 1
    finally:
        box.close_processes()
    box.close_processes()
    assert executor.stops == 1
    assert box._thread is None


def test_start_failure_never_falls_back_and_closes_thread(tmp_path):
    executor = Executor()
    executor.start = AsyncMock(side_effect=RuntimeError("no hypervisor"))
    box, _ = adapter(tmp_path, executor)
    with pytest.raises(SandboxConfigurationError, match="no hypervisor"):
        box.verify_available()
    assert box._loop is None
    assert executor.stops == 1
    assert not executor.calls


def test_cancellation_stops_owned_vm(tmp_path):
    box, executor = adapter(tmp_path)
    token = SimpleNamespace(cancelled=False)
    results = []
    worker = threading.Thread(
        target=lambda: results.append(
            box.execute(
                "block", cwd=tmp_path, env={}, control=control(cancellation_token=token)
            )
        )
    )
    worker.start()
    assert executor.started_command.wait(3)
    token.cancelled = True
    worker.join(3)
    assert not worker.is_alive()
    assert results[0].status == "cancelled"
    assert executor.stops == 1
    assert box._loop is None


def test_command_error_preserves_running_vm_like_pico(tmp_path):
    box, executor = adapter(tmp_path)
    original_exec = executor.exec
    executor.exec = AsyncMock(side_effect=RuntimeError("spawn_failed"))
    try:
        box.verify_available()
        loop = box._loop
        with pytest.raises(SandboxConfigurationError, match="spawn_failed"):
            box.execute("failed", cwd=tmp_path, env={}, control=control())
        executor.exec.assert_awaited_once()  # No hidden command retry.
        assert executor.stops == 0
        assert box._loop is loop
        executor.exec = original_exec
        assert (
            box.execute("next", cwd=tmp_path, env={}, control=control()).exit_code == 0
        )
        assert executor.starts == 1
    finally:
        box.close_processes()


def test_lazy_start_failure_still_closes_partial_vm(tmp_path):
    executor = Executor()
    executor.start = AsyncMock(side_effect=RuntimeError("start failed"))
    box, _ = adapter(tmp_path, executor)
    with pytest.raises(SandboxConfigurationError, match="start failed"):
        box.execute("never runs", cwd=tmp_path, env={}, control=control())
    assert executor.stops == 1
    assert box._loop is None
    assert not executor.calls


@pytest.mark.asyncio
async def test_mcp_and_shell_share_loop_and_close_only_owned_process(tmp_path):
    box, executor = adapter(tmp_path)
    try:
        async with box.start_process("server", [], cwd=tmp_path, env={}) as (
            read,
            write,
        ):
            await write.send("one")
            assert await asyncio.wait_for(read.receive(), 2) == "one"
            result = await asyncio.to_thread(
                box.execute, "echo hi", cwd=tmp_path, env={}, control=control()
            )
            assert result.exit_code == 0
            async with box.start_process("other", [], cwd=tmp_path, env={}) as (r2, w2):
                await w2.send("two")
                assert await asyncio.wait_for(r2.receive(), 2) == "two"
            await write.send("still running")
            assert await asyncio.wait_for(read.receive(), 2) == "still running"
        assert executor.starts == 1 and executor.stops == 0
        assert len(set(executor.loops)) == 1
        assert not executor._process_executions
        assert not executor._process_tasks
    finally:
        await asyncio.to_thread(box.close_processes)


def test_fork_owns_separate_workspace_and_executor(tmp_path):
    box, _ = adapter(tmp_path)
    child = box.fork(tmp_path / "child")
    assert child is not box
    assert child.workspace == tmp_path / "child"
    assert child.config is not box.config
    assert child.owned_ids is box.owned_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("exited", [True, False])
async def test_mcp_kill_error_requires_confirmed_exit(tmp_path, exited):
    box, executor = adapter(tmp_path)

    async def session():
        async with box.start_process("server", [], cwd=tmp_path, env={}):
            execution = executor._process_executions[0]
            execution.kill.side_effect = RuntimeError("Failed to send signal")
            execution.wait = AsyncMock(return_value=SimpleNamespace(exit_code=7))
            if not exited:
                execution.wait.side_effect = RuntimeError("exit not confirmed")

    try:
        if exited:
            await session()
            assert executor.stops == 0
            assert box._loop is not None
            assert not executor._process_executions
            assert not executor._process_tasks
        else:
            with pytest.raises(RuntimeError, match="exit not confirmed"):
                await session()
            assert executor.stops == 1
            assert box._loop is None
    finally:
        await asyncio.to_thread(box.close_processes)


def test_factory_auto_selects_boxlite_and_config_matches_pico(tmp_path):
    config = tmp_path / "sandbox.json"
    config.write_text('{"memoryMib":512,"allowNet":["example.com"],"createTimeout":12}')
    settings = load_boxlite_config(config, backend="auto")
    box = build_sandbox_adapter("auto", tmp_path, boxlite_config=settings)
    assert isinstance(box, BoxliteSandboxAdapter)
    assert box.config.memory_mib == 512
    assert box.config.allow_net == ["example.com"]
    assert box.config.image == "ubuntu:22.04"
    assert box.config.create_timeout == 12
    assert SandboxConfig().backend == "none"


def test_real_mcp_sdk_uses_boxlite_streams_for_handshake_and_tool_calls(tmp_path):
    from mcp.shared.message import SessionMessage
    from mcp.types import JSONRPCMessage
    from repoagent.mcp_transport import StdioMCPClient, StdioServerConfig

    class MCPExecutor(Executor):
        async def start_process(self, command, args, env=None):
            tx, rx = anyio.create_memory_object_stream(16)
            write, read = anyio.create_memory_object_stream(16)

            async def server():
                async with read, tx:
                    async for incoming in read:
                        message = incoming.message.model_dump(by_alias=True)
                        if "id" not in message:
                            continue
                        method = message["method"]
                        if method == "initialize":
                            result = {
                                "protocolVersion": message["params"]["protocolVersion"],
                                "capabilities": {"tools": {}},
                                "serverInfo": {
                                    "name": "boxlite-fixture",
                                    "version": "1",
                                },
                            }
                        elif method == "tools/list":
                            result = {
                                "tools": [
                                    {
                                        "name": "echo",
                                        "description": "echo",
                                        "inputSchema": {
                                            "type": "object",
                                            "properties": {"value": {"type": "string"}},
                                        },
                                    }
                                ]
                            }
                        else:
                            result = {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": message["params"]["arguments"]["value"],
                                    }
                                ]
                            }
                        await tx.send(
                            SessionMessage(
                                JSONRPCMessage.model_validate(
                                    {
                                        "jsonrpc": "2.0",
                                        "id": message["id"],
                                        "result": result,
                                    }
                                )
                            )
                        )

            self._process_tasks.append(asyncio.create_task(server()))
            handle = type("Execution", (), {})()
            handle.kill = AsyncMock()
            self._process_executions.append(handle)
            return rx, write

    box, executor = adapter(tmp_path, MCPExecutor())
    client = StdioMCPClient(
        StdioServerConfig("vm-server"), cwd=tmp_path, sandbox_adapter=box
    )
    try:
        assert client.list_tools()[0]["name"] == "echo"
        assert not executor._process_executions
        result = client.call_tool("echo", {"value": "hello"}, control=control())
        assert "hello" in result.content
        assert executor.starts == 1
    finally:
        client.close()
        box.close_processes()
    assert not executor._process_executions


def test_factory_configuration_cannot_claim_direct_executor_is_isolated(tmp_path):
    with pytest.raises(SandboxConfigurationError):
        BoxliteSandboxAdapter(tmp_path, config=SandboxConfig(backend="none"))


def test_cli_status_uses_boxlite_image_and_configuration(tmp_path, monkeypatch):
    from repoagent.cli import main

    seen = []
    monkeypatch.setattr(
        BoxliteSandboxAdapter, "verify_available", lambda self: seen.append(self.config)
    )
    path = tmp_path / "config.json"
    path.write_text('{"memoryMib":700,"allowNet":false}')
    assert (
        main(
            [
                "sandbox",
                "status",
                "--backend",
                "auto",
                "--cwd",
                str(tmp_path),
                "--image",
                "fixture:v1",
                "--sandbox-config",
                str(path),
            ]
        )
        == 0
    )
    assert seen[0].image == "fixture:v1"
    assert seen[0].memory_mib == 700
    assert seen[0].allow_net is False


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", [True, False, ["example.com"]])
async def test_pinned_sdk_box_options_accept_reference_network_policy(
    tmp_path, monkeypatch, policy
):
    boxlite = pytest.importorskip("boxlite")
    from repoagent.boxlite_sandbox.boxlite_executor import BoxliteExecutor
    from repoagent.boxlite_sandbox import _runtime

    box = SimpleNamespace(id="test-box", start=AsyncMock(), stop=AsyncMock())
    runtime = SimpleNamespace(create=AsyncMock(return_value=box), remove=AsyncMock())
    monkeypatch.setattr(_runtime, "get_boxlite_runtime", lambda: runtime)
    executor = BoxliteExecutor("ubuntu:22.04", tmp_path, allow_net=policy)
    try:
        await executor._ensure_box()
        options = runtime.create.call_args.args[0]
        assert isinstance(options, boxlite.BoxOptions)
        if policy is False:
            assert options.network.mode == "disabled"
        elif isinstance(policy, list):
            assert options.network.mode == "enabled"
            assert options.network.allow_net == policy
    finally:
        await executor.stop()


def test_runtime_data_uses_product_config_root_and_explicit_override(
    tmp_path, monkeypatch
):
    from repoagent.boxlite_sandbox.paths import get_sandbox_dir

    monkeypatch.delenv("REPOAGENT_DATA_DIR", raising=False)
    from repoagent.config import user_env_path

    assert get_sandbox_dir("boxlite") == user_env_path().parent / "sandbox/boxlite"
    monkeypatch.setenv("REPOAGENT_DATA_DIR", str(tmp_path / "override"))
    assert get_sandbox_dir("boxlite") == tmp_path / "override/sandbox/boxlite"


def test_boxlite_config_cannot_be_silently_ignored_by_host_backend(tmp_path):
    from repoagent.cli import build_arg_parser
    from repoagent.runtime_assembly import RuntimeAssembly
    from repoagent.product_commands import sandbox_report

    config = tmp_path / "sandbox.json"
    config.write_text('{"backend":"boxlite"}')
    args = build_arg_parser().parse_args(["--sandbox-config", str(config)])
    with pytest.raises(ValueError, match="requires"):
        RuntimeAssembly._boxlite_config(args)
    with pytest.raises(ValueError, match="requires"):
        sandbox_report(sandbox_config=config)
