"""Private stdio transport for host-budgeted model calls from sandbox workers."""

import asyncio
import json
import math
from pathlib import Path
import subprocess
import time

from ..atomic_io import atomic_replace_unlocked, _fsync_directory
from .ledger import EvolutionLedger
from .model_proxy import ModelProxyError, _object, _reject_constant


class ModelCallJournal:
    """Exclusive trial journal. The host must own its parent directory."""

    def __init__(self, directory, *, worker_root):
        directory = Path(directory).resolve()
        worker_root = Path(worker_root).resolve()
        if directory == worker_root or directory.is_relative_to(worker_root):
            raise ValueError("model journal must be outside worker mounts")
        directory.mkdir(mode=0o700, exist_ok=False)
        _fsync_directory(directory.parent)
        self.path = directory / "calls.jsonl"
        atomic_replace_unlocked(self.path, "")
        self.ledger = EvolutionLedger(self.path)
        self.ledger.append("model.channel_opened", actor="host-model-proxy")

    def __call__(self, record):
        self.ledger.append("model.call", actor="host-model-proxy", payload=record)


async def run_model_worker(adapter, command, args, *, cwd, proxy, timeout_seconds=60):
    """Run one worker over its owned Docker exec pipes, without network access.

    Provider calls execute on a host thread and must honor the gateway timeout.
    Cancellation waits for that call to settle before closing the proxy. This is
    deliberately not a hard kill/account-level billing guarantee for leaf clients.
    Returned worker_result is untrusted; callers must perform host-side grading.
    """
    if (
        type(timeout_seconds) not in {int, float}
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise ValueError("worker timeout must be finite and positive")
    deadline = time.monotonic() + timeout_seconds
    handle = process = None

    async def bounded(operation):
        return await asyncio.wait_for(
            operation, max(0.001, deadline - time.monotonic())
        )

    async def settled(operation):
        task = asyncio.create_task(asyncio.to_thread(operation))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            finally:
                raise

    try:
        # Capture ownership even if cancellation arrives during Docker startup.
        startup = asyncio.create_task(
            asyncio.to_thread(
                adapter.prepare_process,
                command,
                args,
                cwd=cwd,
                env={},
                timeout=timeout_seconds,
            )
        )
        try:
            handle, argv = await asyncio.shield(startup)
        except asyncio.CancelledError:
            handle, _ = await startup
            raise
        launch = asyncio.create_task(
            asyncio.create_subprocess_exec(
                adapter.executable,
                *argv,
                cwd=str(adapter.workspace),
                env=adapter._docker_environment(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                limit=1_000_001,
            )
        )
        try:
            process = await asyncio.shield(launch)
        except asyncio.CancelledError:
            process = await launch
            raise
        while True:
            line = await bounded(process.stdout.readline())
            if not line or len(line) > 1_000_000 or not line.endswith(b"\n"):
                raise ModelProxyError("worker_frame")
            try:
                envelope = json.loads(
                    line, object_pairs_hook=_object, parse_constant=_reject_constant
                )
            except (ValueError, RecursionError) as exc:
                raise ModelProxyError("worker_frame") from exc
            if isinstance(envelope, dict) and set(envelope) == {"worker_result"}:
                if not isinstance(envelope["worker_result"], dict):
                    raise ModelProxyError("worker_result")
                process.stdin.close()
                # Drain to EOF before wait: trailing stdout must not block exit.
                trailing = await bounded(process.stdout.read(1))
                if trailing or await bounded(process.wait()) != 0:
                    raise ModelProxyError("worker_exit")
                return envelope["worker_result"]
            if time.monotonic() >= deadline:
                raise TimeoutError("worker deadline")
            response = await settled(lambda: proxy.dispatch(line))
            if time.monotonic() >= deadline:
                raise TimeoutError("worker deadline")
            process.stdin.write(response + b"\n")
            await bounded(process.stdin.drain())
    finally:
        try:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
        finally:
            try:
                if handle is not None:
                    await settled(lambda: adapter.finish_process(handle))
            finally:
                proxy.close()
