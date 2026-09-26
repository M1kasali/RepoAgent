"""Bridge Pico's async BoxLite executor to RepoAgent's synchronous tools.

One owned asyncio loop holds the VM, shell executions and MCP bridges. No
BoxLite object or AnyIO stream is used directly from another event loop.
"""

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
import threading

from .sandbox import SandboxAdapter, SandboxConfigurationError
from .tool_execution import ProcessOutcome


_RUNTIME_CREATION_LOCK = threading.Lock()


class BoxliteSandboxAdapter(SandboxAdapter):
    def __init__(
        self, workspace, *, config=None, executor_factory=None, owned_ids=None
    ):
        from .boxlite_sandbox import SandboxConfig, build_executor

        self.workspace = Path(workspace).resolve()
        self.config = config or SandboxConfig(backend="boxlite")
        if self.config.backend not in {"boxlite", "auto"}:
            raise SandboxConfigurationError(
                "BoxLite adapter requires boxlite or auto backend"
            )
        self._factory = executor_factory or build_executor
        self._executor = None
        self._loop = None
        self._thread = None
        self._lock = threading.RLock()
        self._started = False
        self._debug_server = None
        self.owned_ids = owned_ids if owned_ids is not None else set()

    @property
    def identity(self):
        return "boxlite_microvm"

    @property
    def is_isolated(self):
        return True

    @property
    def supports_process_spawning(self):
        return True

    def prompt_context(self, *, cwd):
        return (
            "Shell and stdio MCP execute in a shared BoxLite microVM. "
            "The workspace is mounted read-write at /workspace. "
            f"Network policy: {self.config.allow_net!r}. "
            "Host changes inside the mounted workspace persist after shutdown."
        )

    def _ensure_loop(self):
        with self._lock:
            if self._loop is not None:
                return
            self._loop = asyncio.new_event_loop()

            def run():
                asyncio.set_event_loop(self._loop)
                self._loop.run_forever()

            self._thread = threading.Thread(
                target=run, name="repoagent-boxlite", daemon=True
            )
            self._thread.start()

    def _submit(self, coro):
        with self._lock:
            self._ensure_loop()
            return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _start(self):
        # All callers execute on the owned loop; the lock covers awaited startup.
        if not hasattr(self, "_start_lock"):
            self._start_lock = asyncio.Lock()
        async with self._start_lock:
            if self._started:
                return
            self._executor = self._factory(
                self.config, self.workspace, owned_ids=self.owned_ids
            )
            # Pico normally constructs runtimes on one event loop. Our hosts
            # have separate owner threads, so serialize the first cache fill.
            from .boxlite_sandbox.boxlite_executor import BoxliteExecutor

            if isinstance(self._executor, BoxliteExecutor):
                from .boxlite_sandbox._runtime import get_boxlite_runtime

                with _RUNTIME_CREATION_LOCK:
                    get_boxlite_runtime()
            await self._executor.start()
            self._started = True
            if self.config.debug.enabled:
                from .boxlite_sandbox.debug_server import SandboxDebugServer
                from .boxlite_sandbox.paths import get_data_dir

                try:
                    server = SandboxDebugServer(
                        SandboxDebugServer.resolve_socket_path(
                            self.config.debug.socket, get_data_dir()
                        ),
                        self.owned_ids,
                        self.config.debug.max_message_bytes,
                    )
                    await server.start()
                    self._debug_server = server
                except Exception as exc:
                    logging.getLogger(__name__).error(
                        "Failed to start sandbox debug server: %s", exc
                    )

    def fork(self, workspace):
        config = self.config.model_copy(deep=True)
        config.debug.enabled = False
        return type(self)(
            workspace,
            config=config,
            executor_factory=self._factory,
            owned_ids=self.owned_ids,
        )

    def start_runtime(self):
        return self.verify_available()

    def verify_available(self):
        try:
            self._submit(self._start()).result()
        except Exception as exc:
            self.close_processes()
            raise SandboxConfigurationError(
                f"BoxLite sandbox unavailable: {exc}"
            ) from exc
        return "BoxLite VM probe passed"

    async def _exec(self, command, cwd, env, timeout):
        await self._start()
        return await self._executor.exec(
            command, cwd=str(cwd), env=env, timeout=timeout
        )

    def execute(self, command, *, cwd, env, control):
        status = control.status()
        if status != "running":
            return ProcessOutcome(status, None, "", "", 0, 0, False)
        future = self._submit(self._exec(command, cwd, env, control.remaining_seconds))
        try:
            while True:
                try:
                    result = future.result(timeout=0.02)
                    break
                except FutureTimeout:
                    if control.cancelled:
                        status = "cancelled"
                        future.cancel()
                        # Pico stops its executor when the owning loop is
                        # cancelled. Preserve that boundary across our bridge.
                        self.close_processes()
                        return ProcessOutcome(
                            status, None, "", "", 0, 0, False, "vm_stop"
                        )
        except Exception as exc:
            # Pico's ExecTool reports a failed command while keeping its
            # executor alive. A spawn error must not erase /tmp or stop MCP.
            # Failed startup still owns partial resources that must be closed.
            if not self._started:
                self.close_processes()
            raise SandboxConfigurationError(f"BoxLite execution failed: {exc}") from exc
        status = (
            "timeout"
            if result.exit_code == -1
            and result.stderr.startswith("Command timed out after ")
            else "completed"
        )
        cap = control.max_output_chars
        stdout = result.stdout[:cap]
        stderr = result.stderr[: max(0, cap - len(stdout))]
        return ProcessOutcome(
            status,
            result.exit_code,
            stdout,
            stderr,
            len(result.stdout),
            len(result.stderr),
            len(stdout) + len(stderr) < len(result.stdout) + len(result.stderr),
        )

    @asynccontextmanager
    async def start_process(self, command, args, *, cwd, env, startup_timeout=10):
        from anyio.abc import ObjectReceiveStream, ObjectSendStream

        async def start():
            await self._start()
            if not hasattr(self, "_process_start_lock"):
                self._process_start_lock = asyncio.Lock()
            async with self._process_start_lock:
                before_tasks = len(self._executor._process_tasks)
                before_execs = len(self._executor._process_executions)
                streams = await self._executor.start_process(
                    command, list(args), env=env
                )
                return (
                    streams,
                    self._executor._process_tasks[before_tasks:],
                    self._executor._process_executions[before_execs:],
                )

        owner_loop = None

        def stream_submit(coro):
            if owner_loop is None or not owner_loop.is_running():
                coro.close()
                from anyio import ClosedResourceError

                raise ClosedResourceError
            return asyncio.run_coroutine_threadsafe(coro, owner_loop)

        class Receive(ObjectReceiveStream):
            async def receive(self):
                return await asyncio.wrap_future(stream_submit(streams[0].receive()))

            async def aclose(self):
                if owner_loop.is_running():
                    await asyncio.wrap_future(stream_submit(streams[0].aclose()))

        class Send(ObjectSendStream):
            async def send(self, item):
                await asyncio.wrap_future(stream_submit(streams[1].send(item)))

            async def aclose(self):
                if owner_loop.is_running():
                    await asyncio.wrap_future(stream_submit(streams[1].aclose()))

        future = self._submit(start())
        owner_loop = self._loop
        try:
            streams, tasks, executions = await asyncio.wait_for(
                asyncio.wrap_future(future), startup_timeout
            )
        except BaseException:
            future.cancel()
            import anyio

            with anyio.CancelScope(shield=True):
                await asyncio.to_thread(self.close_processes)
            raise
        try:
            yield Receive(), Send()
        finally:

            async def cleanup():
                await streams[1].aclose()
                await streams[0].aclose()
                for execution in executions:
                    try:
                        await execution.kill()
                    except Exception:
                        # BoxLite 0.9.5 reports "Failed to send signal" when
                        # an MCP server has already exited. Confirm its exit;
                        # a dead server must not tear down the shared VM.
                        # If exit cannot be confirmed, the outer handler still
                        # stops the VM instead of hiding a cleanup failure.
                        await asyncio.wait_for(execution.wait(), timeout=1)
                    if (
                        self._executor
                        and execution in self._executor._process_executions
                    ):
                        self._executor._process_executions.remove(execution)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if self._executor:
                    self._executor._process_tasks[:] = [
                        t for t in self._executor._process_tasks if t not in tasks
                    ]

            # MCP SDK uses AnyIO cancel scopes; shield owned cleanup from them.
            import anyio

            with anyio.CancelScope(shield=True):
                if owner_loop.is_running():
                    try:
                        await asyncio.wrap_future(stream_submit(cleanup()))
                    except BaseException:
                        await asyncio.to_thread(self.close_processes)
                        raise

    def close_processes(self):
        with self._lock:
            if self._loop is None:
                return
            loop, thread = self._loop, self._thread

            async def stop():
                if self._debug_server is not None:
                    try:
                        await self._debug_server.stop()
                    finally:
                        self._debug_server = None
                if self._executor is not None:
                    await self._executor.stop()
                pending = [
                    t for t in asyncio.all_tasks() if t is not asyncio.current_task()
                ]
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

            try:
                asyncio.run_coroutine_threadsafe(stop(), loop).result()
            finally:
                loop.call_soon_threadsafe(loop.stop)
                thread.join()
                loop.close()
                self._loop = self._thread = self._executor = None
                self._started = False
                if hasattr(self, "_start_lock"):
                    del self._start_lock
                if hasattr(self, "_process_start_lock"):
                    del self._process_start_lock


def load_boxlite_config(path=None, *, backend="boxlite", image=None):
    from .boxlite_sandbox.config import SandboxConfig

    raw = json.loads(Path(path).read_text(encoding="utf-8")) if path else {}
    raw["backend"] = backend
    if image is not None:
        raw["image"] = image
    return SandboxConfig.model_validate(raw)
