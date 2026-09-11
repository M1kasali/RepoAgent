"""Execution-location adapters for shell tools."""

from __future__ import annotations

import os
import math
import re
import subprocess
import threading
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from .tool_execution import ProcessOutcome, run_bounded_process


class SandboxConfigurationError(ValueError):
    pass


class SandboxAdapter(ABC):
    @property
    def supports_process_spawning(self):
        return False

    def start_process(self, command, args, *, cwd, env, startup_timeout=10):
        """Return an owned async context of MCP streams, never host fallback."""
        raise SandboxConfigurationError("sandbox does not support process spawning")

    def close_processes(self):
        """Retry cleanup of owned persistent processes after clients close."""

    @property
    @abstractmethod
    def identity(self) -> str: ...

    @property
    @abstractmethod
    def is_isolated(self) -> bool: ...

    @abstractmethod
    def execute(self, command, *, cwd, env, control) -> ProcessOutcome: ...

    def prompt_context(self, *, cwd) -> str:
        """Return adapter-owned execution facts, without probing the backend."""
        return ""


class DirectSandboxAdapter(SandboxAdapter):
    @property
    def identity(self):
        return "direct_host"

    @property
    def is_isolated(self):
        return False

    def execute(self, command, *, cwd, env, control):
        return run_bounded_process(
            command, cwd=cwd, env=env, shell=True, control=control
        )


class IsolatedSandboxAdapter(SandboxAdapter):
    """Adapter for an injected VM/container backend with explicit isolation."""

    def __init__(self, backend, *, identity="isolated"):
        if not isinstance(identity, str) or not identity.strip():
            raise SandboxConfigurationError("sandbox identity must be non-empty")
        if not callable(getattr(backend, "execute", None)):
            raise SandboxConfigurationError("isolated backend must implement execute")
        if getattr(backend, "is_isolated", None) is not True:
            raise SandboxConfigurationError(
                "isolated backend must explicitly declare is_isolated=True"
            )
        self._backend = backend
        self._identity = identity.strip()

    @property
    def identity(self):
        return self._identity

    @property
    def is_isolated(self):
        return True

    @property
    def supports_process_spawning(self):
        return (
            getattr(self._backend, "supports_process_spawning", False) is True
            and callable(getattr(self._backend, "start_process", None))
            and callable(getattr(self._backend, "close_processes", None))
        )

    def start_process(self, command, args, *, cwd, env, startup_timeout=10):
        if not self.supports_process_spawning:
            return super().start_process(command, args, cwd=cwd, env=env)
        return self._backend.start_process(
            command, args, cwd=cwd, env=dict(env), startup_timeout=startup_timeout
        )

    def close_processes(self):
        if self.supports_process_spawning:
            self._backend.close_processes()

    def execute(self, command, *, cwd, env, control):
        outcome = self._backend.execute(
            command, cwd=cwd, env=dict(env), control=control
        )
        if not isinstance(outcome, ProcessOutcome):
            raise TypeError("isolated sandbox backend must return ProcessOutcome")
        return outcome


class DockerSandboxAdapter(SandboxAdapter):
    """Run shell tools in a disposable, resource-bounded Docker container."""

    _ENV_ALLOWLIST = frozenset({"LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ"})
    _ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

    def __init__(
        self,
        workspace,
        *,
        executable="docker",
        image="python:3.12-slim",
        memory="2g",
        cpus=2.0,
        pids_limit=256,
        network="none",
        workspace_path_converter=None,
        process_runner=run_bounded_process,
        cleanup_runner=subprocess.run,
        lifecycle_runner=subprocess.run,
    ):
        self.workspace = Path(workspace).expanduser().resolve()
        self.executable = str(executable).strip()
        self.image = str(image).strip()
        if not self.executable or not self.image:
            raise SandboxConfigurationError(
                "Docker executable and image must be non-empty"
            )
        if not re.fullmatch(r"[0-9]+(?:[kKmMgG])?", str(memory)):
            raise SandboxConfigurationError("Docker memory must be a positive size")
        if isinstance(cpus, bool) or not isinstance(cpus, (int, float)) or cpus <= 0:
            raise SandboxConfigurationError("Docker CPUs must be positive")
        if (
            isinstance(pids_limit, bool)
            or not isinstance(pids_limit, int)
            or pids_limit <= 0
        ):
            raise SandboxConfigurationError("Docker PID limit must be positive")
        if network != "none":
            raise SandboxConfigurationError(
                "Docker Agent sandbox currently requires network=none"
            )
        if "," in str(self.workspace):
            raise SandboxConfigurationError(
                "Docker workspace path must not contain a comma"
            )
        self.memory = str(memory)
        self.cpus = float(cpus)
        self.pids_limit = pids_limit
        self.network = network
        self._workspace_path_converter = workspace_path_converter or str
        self._process_runner = process_runner
        self._cleanup_runner = cleanup_runner
        self._lifecycle_runner = lifecycle_runner
        self._process_names = set()
        self._process_lock = threading.Lock()

    @property
    def supports_process_spawning(self):
        return True

    def start_process(self, command, args, *, cwd, env, startup_timeout=10):
        from .sandbox_process import docker_process

        return docker_process(
            self, command, args, cwd=cwd, env=env, startup_timeout=startup_timeout
        )

    def close_processes(self):
        from .sandbox_process import remove_process_container

        with self._process_lock:
            names = tuple(self._process_names)
        errors = []
        for name in names:
            try:
                remove_process_container(self, name)
            except SandboxConfigurationError as exc:
                errors.append(exc)
        if errors:
            raise SandboxConfigurationError("sandbox process cleanup failed") from errors[0]

    @property
    def identity(self):
        return f"docker:{self.image}"

    @property
    def is_isolated(self):
        return True

    def _guest_paths(self, cwd):
        cwd = Path(cwd).expanduser().resolve()
        if not cwd.is_relative_to(self.workspace):
            raise SandboxConfigurationError(
                "Docker command cwd must remain inside the configured workspace"
            )
        relative_cwd = cwd.relative_to(self.workspace).as_posix()
        workspace_name = self.workspace.name
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", workspace_name):
            raise SandboxConfigurationError(
                f"Docker workspace basename is unsafe: {workspace_name}"
            )
        guest_root = f"/workspace/{workspace_name}"
        guest_cwd = guest_root + (
            f"/{relative_cwd}" if relative_cwd != "." else ""
        )
        return guest_root, guest_cwd

    def prompt_context(self, *, cwd):
        guest_root, guest_cwd = self._guest_paths(cwd)
        return (
            "Shell execution environment:\n"
            f"- run_shell working directory: {guest_cwd}\n"
            f"- Persistent workspace mount: {guest_root}\n"
            "- Host absolute paths are not container paths. Use repository-relative "
            "paths for file tools and run_shell, or obtain shell paths with pwd.\n"
            "- A fresh container for each run_shell call resets shell state and "
            "files outside the workspace mount. Workspace files persist.\n"
            "- /tmp is per-call scratch space; keep build artifacts that must "
            "persist or execute inside the workspace mount.\n"
            "- The container root filesystem is read-only and network is disabled."
        )

    def _container_options(self, *, cwd, env, explicit_env=False):
        guest_root, guest_cwd = self._guest_paths(cwd)
        if not self.workspace.is_dir():
            raise FileNotFoundError(
                f"Docker sandbox workspace does not exist: {self.workspace}"
            )
        mount_source = str(self._workspace_path_converter(self.workspace)).strip()
        if not mount_source or "," in mount_source:
            raise SandboxConfigurationError(
                "Docker workspace path conversion produced an invalid mount source"
            )
        argv = [
            "--network",
            self.network,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory,
            "--cpus",
            str(self.cpus),
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=256m",
            "--mount",
            f"type=bind,source={mount_source},target={guest_root}",
            "--workdir",
            guest_cwd,
        ]
        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            argv.extend(("--user", f"{os.getuid()}:{os.getgid()}"))
        for name, value in sorted(dict(env or {}).items()):
            name = str(name)
            if not explicit_env and name not in self._ENV_ALLOWLIST:
                continue
            if self._ENV_NAME.fullmatch(name) is None:
                raise SandboxConfigurationError(
                    f"invalid Docker environment name: {name}"
                )
            argv.extend(("--env", f"{name}={value}"))
        return argv

    def execute(self, command, *, cwd, env, control):
        container_name = f"repoagent-{uuid.uuid4().hex}"
        argv = [self.executable, "run", "--name", container_name, "--rm"]
        argv.extend(self._container_options(cwd=cwd, env=env))
        argv.extend((self.image, "sh", "-lc", str(command)))
        try:
            return self._process_runner(
                argv,
                cwd=self.workspace,
                env=self._docker_environment(),
                shell=False,
                control=control,
            )
        finally:
            try:
                self._cleanup_runner(
                    [
                        self.executable,
                        "rm",
                        "--force",
                        "--volumes",
                        container_name,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                )
            except (OSError, subprocess.SubprocessError):
                pass

    def verify_available(self, *, timeout=10):
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise SandboxConfigurationError(
                "Docker probe timeout must be finite and positive"
            )
        deadline = time.monotonic() + timeout
        version = ""
        probes = (
            ("version", ["info", "--format", "{{.ServerVersion}}"]),
            # Metadata can remain available while Desktop cannot wake its engine.
            (
                "container control",
                ["container", "ls", "--all", "--filter",
                 "name=repoagent-health-probe", "--format", "{{.ID}}"],
            ),
        )
        for stage, args in probes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SandboxConfigurationError(
                    f"Docker sandbox is unavailable: {stage} probe timed out"
                )
            try:
                result = subprocess.run(
                    [self.executable, *args],
                    cwd=self.workspace,
                    env=self._docker_environment(),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=remaining,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise SandboxConfigurationError(
                    f"Docker sandbox is unavailable: {stage} probe {type(exc).__name__}"
                ) from exc
            if result.returncode != 0 or (
                stage == "version" and not result.stdout.strip()
            ):
                detail = result.stderr.strip() or "Docker daemon probe failed"
                raise SandboxConfigurationError(
                    f"Docker sandbox is unavailable: {stage} probe: {detail}"
                )
            if stage == "version":
                version = result.stdout.strip()
        return version

    @staticmethod
    def _docker_environment():
        names = ("HOME", "PATH", "SystemRoot", "WINDIR", "WSLENV")
        return {name: os.environ[name] for name in names if os.environ.get(name)}


def build_sandbox_adapter(
    backend,
    workspace,
    *,
    docker_executable="docker",
    docker_image="python:3.12-slim",
    docker_memory="2g",
    docker_cpus=2.0,
    docker_pids_limit=256,
    docker_workspace_path_converter=None,
    verify=False,
):
    backend = str(backend or "direct").strip().lower()
    if backend == "direct":
        return DirectSandboxAdapter()
    if backend in {"docker", "docker-persistent"}:
        adapter_type = DockerSandboxAdapter
        if backend == "docker-persistent":
            from .sandbox_session import PersistentDockerSandboxAdapter

            adapter_type = PersistentDockerSandboxAdapter
        adapter = adapter_type(
            workspace,
            executable=docker_executable,
            image=docker_image,
            memory=docker_memory,
            cpus=docker_cpus,
            pids_limit=docker_pids_limit,
            workspace_path_converter=docker_workspace_path_converter,
        )
        if verify:
            adapter.verify_available()
        return adapter
    raise SandboxConfigurationError(f"unsupported sandbox backend: {backend}")


__all__ = [
    "DirectSandboxAdapter",
    "DockerSandboxAdapter",
    "IsolatedSandboxAdapter",
    "SandboxAdapter",
    "SandboxConfigurationError",
    "build_sandbox_adapter",
]
