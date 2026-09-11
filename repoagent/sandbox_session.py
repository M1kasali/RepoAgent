"""Persistent Docker environment shared by shell and owned MCP processes."""

import math
import threading
import time
import uuid

from .sandbox import DockerSandboxAdapter, SandboxConfigurationError
from .sandbox_process import _run
from .sandbox_ownership import SandboxOwnership
from .tool_execution import ProcessOutcome
from .sandbox_exec import guest_argv, shared_docker_process


class PersistentDockerSandboxAdapter(DockerSandboxAdapter):
    """Own one container until stop; independently reap each execution group."""

    def __init__(self, *args, ownership_root=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._session_lock = threading.RLock()
        self._session_name = None
        self._session_broken = False
        self._guest_python = "python"
        self.ownership = SandboxOwnership(self.workspace, root=ownership_root)

    @property
    def identity(self):
        return f"docker-persistent:{self.image}"

    @property
    def supports_process_spawning(self):
        return True

    def start_process(self, command, args, *, cwd, env, startup_timeout=10):
        return shared_docker_process(
            self, command, args, cwd=cwd, env=env, startup_timeout=startup_timeout
        )

    def prepare_process(self, command, args, *, cwd, env, timeout):
        if (
            not isinstance(command, str) or not command.strip()
            or not isinstance(args, (list, tuple))
            or not all(isinstance(arg, str) for arg in args)
            or any("\0" in arg for arg in (command, *args))
        ):
            raise SandboxConfigurationError("sandbox process requires executable and argv")
        _, guest_cwd = self._guest_paths(cwd)
        if not isinstance(env, dict) or not all(
            isinstance(key, str) and self._ENV_NAME.fullmatch(key)
            and isinstance(value, str) and "\0" not in value
            for key, value in env.items()
        ):
            raise SandboxConfigurationError("sandbox process env must be a string mapping")
        started = time.monotonic()
        self.start(timeout=timeout)
        if not self._session_lock.acquire(timeout=max(0, timeout - (time.monotonic() - started))):
            raise SandboxConfigurationError("sandbox process startup lock timed out")
        try:
            if self._session_name is None or self._session_broken:
                raise SandboxConfigurationError("sandbox stopped during process startup")
            handle = (self._session_name, uuid.uuid4().hex)
            argv = ["exec", "--interactive", "--workdir", guest_cwd]
            for key, value in sorted(env.items()):
                argv.extend(("--env", f"{key}={value}"))
            argv.extend((handle[0], *guest_argv(
                "run", handle[1], [command, *args], executable=self._guest_python
            )))
            return handle, argv
        finally:
            self._session_lock.release()

    def finish_process(self, handle):
        name, token = handle
        # Bind cleanup to the captured generation, never a newly started sandbox.
        if name != self._session_name:
            return
        try:
            result = _run(self, ["exec", name, *guest_argv(
                "cancel", token, executable=self._guest_python
            )])
            if result.returncode:
                raise SandboxConfigurationError("sandbox execution cleanup failed")
        except SandboxConfigurationError:
            with self._session_lock:
                if name == self._session_name:
                    self._invalidate_locked()
                else:
                    return
            raise

    @property
    def container_name(self):
        with self._session_lock:
            return self._session_name

    def start(self, *, timeout=10):
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise SandboxConfigurationError("sandbox start timeout must be positive")
        deadline = time.monotonic() + timeout
        if not self._session_lock.acquire(timeout=timeout):
            raise SandboxConfigurationError("sandbox start lock timed out")
        try:
            self._start_locked(deadline)
        finally:
            self._session_lock.release()

    def _start_locked(self, deadline):
        if self._session_broken:
            raise SandboxConfigurationError(
                "persistent sandbox state was lost; call stop before restarting"
            )
        if self._session_name is not None:
            return
        recovery = self.ownership.reconcile(self, timeout=max(0.001, deadline - time.monotonic()))
        if any(row["status"] in {"failed", "invalid"} for row in recovery):
            raise SandboxConfigurationError("sandbox orphan reconciliation requires attention")
        options = self._container_options(cwd=self.workspace, env={})
        name = f"repoagent-session-{uuid.uuid4().hex}"
        labels = self.ownership.claim(self, name, timeout=max(0.001, deadline - time.monotonic()))
        self._session_name = name
        with self._process_lock:
            self._process_names.add(name)
        commands = (
            [
                "create", "--name", name, "--init", "--pull", "never",
                *labels, *options, "--entrypoint", "sh", self.image,
                "-c", "while :; do sleep 3600; done",
            ],
            ["start", name],
            ["exec", name, "python", "-I", "-B", "-c",
             "import fcntl, os, sys; assert hasattr(os, 'waitid'); print(sys.executable)"],
        )
        try:
            for command in commands:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SandboxConfigurationError("sandbox start timed out")
                result = _run(self, command, timeout=remaining)
                if result.returncode:
                    raise SandboxConfigurationError("persistent sandbox start failed")
                if command[0] == "create":
                    self.ownership.created()
                if command[0] == "exec":
                    executable = result.stdout.strip()
                    if not executable.startswith("/") or "\0" in executable or "\n" in executable:
                        raise SandboxConfigurationError("sandbox Python probe returned an invalid path")
                    self._guest_python = executable
        except BaseException:
            self._invalidate_locked()
            raise

    def _invalidate_locked(self):
        # Killing a Docker CLI does not terminate daemon-owned exec descendants.
        # Keep a broken-state latch even after successful removal: never silently
        # replace a lost environment with an empty one on the next tool call.
        self._session_broken = True
        if self._session_name is not None:
            self.ownership.cleanup_owned(self)
            with self._process_lock:
                self._process_names.discard(self._session_name)
            self._session_name = None

    def stop(self):
        with self._session_lock:
            if self._session_name is not None:
                self._invalidate_locked()
            self._session_broken = False

    def close_processes(self):
        self.stop()

    def prompt_context(self, *, cwd):
        root, guest_cwd = self._guest_paths(cwd)
        return (
            "Shell execution environment:\n"
            f"- run_shell working directory: {guest_cwd}\n"
            f"- Persistent workspace mount: {root}\n"
            "- Host absolute paths are not container paths. Use repository-relative paths.\n"
            "- Calls share one container and /tmp until sandbox stop. Each call has "
            "a new shell; cd and export do not carry over.\n"
            "- Shell and MCP calls share scratch files. Completed or cancelled executions "
            "have their process groups reaped; detached sessions are not supported.\n"
            "- Failed process cleanup invalidates the container; an explicit lifecycle reset "
            "is required before reuse. Workspace files persist.\n"
            "- The container root filesystem is read-only and network is disabled."
        )

    def execute(self, command, *, cwd, env, control):
        _, guest_cwd = self._guest_paths(cwd)
        options = ["--workdir", guest_cwd]
        for key, value in sorted(dict(env or {}).items()):
            if key in self._ENV_ALLOWLIST:
                if not isinstance(value, str) or "\0" in value:
                    raise SandboxConfigurationError("invalid sandbox environment value")
                options.extend(("--env", f"{key}={value}"))
        while control.status() == "running":
            if self._session_lock.acquire(timeout=min(0.05, control.remaining_seconds)):
                break
        else:
            return self._interrupted(control)
        try:
            if control.status() != "running":
                return self._interrupted(control)
            self._start_locked(time.monotonic() + control.remaining_seconds)
            if control.status() != "running":
                return self._interrupted(control)
            handle = (self._session_name, uuid.uuid4().hex)
            try:
                outcome = self._process_runner(
                    [self.executable, "exec", *options, self._session_name,
                     *guest_argv("run", handle[1], ["sh", "-lc", str(command)], executable=self._guest_python)],
                    cwd=self.workspace, env=self._docker_environment(),
                    shell=False, control=control,
                )
                if not isinstance(outcome, ProcessOutcome):
                    raise TypeError("sandbox runner must return ProcessOutcome")
            finally:
                self.finish_process(handle)
            return outcome
        finally:
            self._session_lock.release()

    @staticmethod
    def _interrupted(control):
        return ProcessOutcome(control.status(), None, "", "", 0, 0, False)
