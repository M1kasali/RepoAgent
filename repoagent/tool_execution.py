"""Bounded execution controls and subprocess lifecycle ownership for tools."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import signal
import subprocess
import threading
import time
from typing import Any


@dataclass(frozen=True)
class ToolRunnerOutput:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolExecutionControl:
    """One monotonic deadline, cancellation source, and output budget."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        max_output_chars: int,
        cancellation_token=None,
        clock=time.monotonic,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ValueError("tool timeout_seconds must be positive")
        if (
            isinstance(max_output_chars, bool)
            or not isinstance(max_output_chars, int)
            or max_output_chars <= 0
        ):
            raise ValueError("tool max_output_chars must be positive")
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_chars = max_output_chars
        self.cancellation_token = cancellation_token
        self._clock = clock
        self.started_at = clock()
        self.deadline = self.started_at + self.timeout_seconds

    @property
    def cancelled(self) -> bool:
        return bool(
            self.cancellation_token is not None and self.cancellation_token.cancelled
        )

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - self._clock())

    def status(self) -> str:
        if self.cancelled:
            return "cancelled"
        if self.remaining_seconds <= 0:
            return "timeout"
        return "running"


@dataclass(frozen=True)
class ProcessOutcome:
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_chars: int
    stderr_chars: int
    output_truncated: bool
    termination_signal: str = ""

    def metadata(self) -> dict[str, Any]:
        return {
            "execution_status": self.status,
            "exit_code": self.exit_code,
            "stdout_chars": self.stdout_chars,
            "stderr_chars": self.stderr_chars,
            "output_truncated": self.output_truncated,
            "termination_signal": self.termination_signal,
        }


class _CappedTextCapture:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.total_chars = 0
        self._captured_chars = 0
        self._parts: list[str] = []

    @property
    def truncated(self) -> bool:
        return self.total_chars > self._captured_chars

    def consume(self, stream) -> None:
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                self.total_chars += len(chunk)
                remaining = self.limit - self._captured_chars
                if remaining > 0:
                    captured = chunk[:remaining]
                    self._parts.append(captured)
                    self._captured_chars += len(captured)
        finally:
            stream.close()

    def text(self) -> str:
        return "".join(self._parts)


def _signal_process_tree(process: subprocess.Popen, *, force: bool) -> str:
    if process.poll() is not None:
        return ""
    try:
        if os.name == "posix":
            os.killpg(
                process.pid,
                signal.SIGKILL if force else signal.SIGTERM,
            )
            return "sigkill" if force else "sigterm"
        if os.name == "nt":
            command = ["taskkill", "/PID", str(process.pid), "/T"]
            if force:
                command.append("/F")
            subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=2,
            )
            return "taskkill_force" if force else "taskkill"
        if force:
            process.kill()
            return "kill"
        process.terminate()
        return "terminate"
    except ProcessLookupError:
        return ""


def _reap_process(process: subprocess.Popen, *, grace_seconds: float = 0.5) -> str:
    signal_name = _signal_process_tree(process, force=os.name == "nt")
    try:
        process.wait(timeout=grace_seconds)
        return signal_name
    except subprocess.TimeoutExpired:
        forced = _signal_process_tree(process, force=True)
        process.wait(timeout=grace_seconds)
        return forced or signal_name


def run_bounded_process(
    command,
    *,
    cwd,
    env,
    shell: bool,
    control: ToolExecutionControl,
) -> ProcessOutcome:
    """Run and reap one process group while retaining bounded output."""

    if control.status() != "running":
        status = control.status()
        return ProcessOutcome(status, None, "", "", 0, 0, False)
    creation: dict[str, Any] = {}
    if os.name == "posix":
        creation["start_new_session"] = True
    elif hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creation["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    process_env = dict(env or {})
    if os.name == "nt":
        for name in ("ComSpec", "SystemRoot", "WINDIR"):
            if name not in process_env and os.environ.get(name):
                process_env[name] = os.environ[name]
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=process_env,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **creation,
    )
    stdout = _CappedTextCapture(control.max_output_chars)
    stderr = _CappedTextCapture(control.max_output_chars)
    stdout_thread = threading.Thread(
        target=stdout.consume, args=(process.stdout,), daemon=True
    )
    stderr_thread = threading.Thread(
        target=stderr.consume, args=(process.stderr,), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()
    callback_signal = []

    def cancel_process() -> None:
        signalled = _signal_process_tree(process, force=True)
        if signalled:
            callback_signal.append(signalled)

    remove_callback = (
        control.cancellation_token.add_callback(cancel_process)
        if control.cancellation_token is not None
        else lambda: None
    )
    status = "completed"
    termination_signal = ""
    try:
        while process.poll() is None:
            status = control.status()
            if status != "running":
                termination_signal = _reap_process(process)
                break
            time.sleep(min(0.02, max(0.001, control.remaining_seconds)))
        if control.cancelled:
            status = "cancelled"
        elif status == "running":
            status = "completed"
        if process.poll() is None:
            termination_signal = _reap_process(process)
        else:
            process.wait()
    finally:
        remove_callback()
        stdout_thread.join(timeout=1.0)
        stderr_thread.join(timeout=1.0)
    if callback_signal:
        termination_signal = callback_signal[-1]
    return ProcessOutcome(
        status=status,
        exit_code=process.returncode,
        stdout=stdout.text(),
        stderr=stderr.text(),
        stdout_chars=stdout.total_chars,
        stderr_chars=stderr.total_chars,
        output_truncated=stdout.truncated or stderr.truncated,
        termination_signal=termination_signal,
    )


def limit_output(text: str, limit: int) -> tuple[str, bool, int]:
    text = str(text)
    total = len(text)
    if total <= limit:
        return text, False, total
    omitted = total - limit
    marker = f"\n...[truncated {omitted} chars]"
    if len(marker) >= limit:
        return marker[:limit], True, total
    retained = max(0, limit - len(marker))
    return text[:retained] + marker, True, total


__all__ = [
    "ProcessOutcome",
    "ToolExecutionControl",
    "ToolRunnerOutput",
    "limit_output",
    "run_bounded_process",
]
