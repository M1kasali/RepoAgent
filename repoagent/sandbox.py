"""Execution-location adapters for shell tools."""

from __future__ import annotations

import os
import re
import subprocess
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from .tool_execution import ProcessOutcome, run_bounded_process


class SandboxConfigurationError(ValueError):
    pass


class SandboxAdapter(ABC):
    @property
    @abstractmethod
    def identity(self) -> str: ...

    @property
    @abstractmethod
    def is_isolated(self) -> bool: ...

    @abstractmethod
    def execute(self, command, *, cwd, env, control) -> ProcessOutcome: ...


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
        process_runner=run_bounded_process,
        cleanup_runner=subprocess.run,
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
        self._process_runner = process_runner
        self._cleanup_runner = cleanup_runner

    @property
    def identity(self):
        return f"docker:{self.image}"

    @property
    def is_isolated(self):
        return True

    def execute(self, command, *, cwd, env, control):
        cwd = Path(cwd).expanduser().resolve()
        if not cwd.is_relative_to(self.workspace):
            raise SandboxConfigurationError(
                "Docker command cwd must remain inside the configured workspace"
            )
        if not self.workspace.is_dir():
            raise FileNotFoundError(
                f"Docker sandbox workspace does not exist: {self.workspace}"
            )
        relative_cwd = cwd.relative_to(self.workspace).as_posix()
        guest_cwd = "/workspace" + (f"/{relative_cwd}" if relative_cwd != "." else "")
        container_name = f"repoagent-{uuid.uuid4().hex}"
        argv = [
            self.executable,
            "run",
            "--name",
            container_name,
            "--rm",
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
            f"type=bind,source={self.workspace},target=/workspace",
            "--workdir",
            guest_cwd,
        ]
        if hasattr(os, "getuid") and hasattr(os, "getgid"):
            argv.extend(("--user", f"{os.getuid()}:{os.getgid()}"))
        for name, value in sorted(dict(env or {}).items()):
            name = str(name)
            if name not in self._ENV_ALLOWLIST:
                continue
            if self._ENV_NAME.fullmatch(name) is None:
                raise SandboxConfigurationError(
                    f"invalid Docker environment name: {name}"
                )
            argv.extend(("--env", f"{name}={value}"))
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
        try:
            result = subprocess.run(
                [self.executable, "info", "--format", "{{.ServerVersion}}"],
                cwd=self.workspace,
                env=self._docker_environment(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SandboxConfigurationError(
                f"Docker sandbox is unavailable: {type(exc).__name__}"
            ) from exc
        if result.returncode != 0 or not result.stdout.strip():
            detail = result.stderr.strip() or "Docker daemon probe failed"
            raise SandboxConfigurationError(f"Docker sandbox is unavailable: {detail}")
        return result.stdout.strip()

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
    verify=False,
):
    backend = str(backend or "direct").strip().lower()
    if backend == "direct":
        return DirectSandboxAdapter()
    if backend == "docker":
        adapter = DockerSandboxAdapter(
            workspace,
            executable=docker_executable,
            image=docker_image,
            memory=docker_memory,
            cpus=docker_cpus,
            pids_limit=docker_pids_limit,
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
