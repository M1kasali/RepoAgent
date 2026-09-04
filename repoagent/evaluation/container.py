"""Fail-closed container execution for untrusted benchmark graders."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from ..tool_execution import ProcessOutcome, ToolExecutionControl, run_bounded_process


_EXCLUDED_NAMES = frozenset(
    {
        ".env",
        ".git",
        ".pico",
        ".repoagent",
        "artifacts",
        "node_modules",
        "target",
    }
)
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_IMMUTABLE_IMAGE = re.compile(r"[^\s@]+@sha256:[0-9a-f]{64}")


class ContainerConfigurationError(ValueError):
    pass


def is_immutable_container_image(image):
    return _IMMUTABLE_IMAGE.fullmatch(str(image)) is not None


def _ignore_stage(directory, names):
    return [name for name in names if name in _EXCLUDED_NAMES or name.startswith(".env.")]


def _identity_path(path):
    return str(path)


def wsl_windows_path(path):
    """Convert a WSL path for a Windows Docker CLI bind mount."""

    result = subprocess.run(
        ["wslpath", "-w", str(Path(path).resolve())],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    converted = result.stdout.strip()
    if result.returncode != 0 or not converted:
        raise ContainerConfigurationError(
            f"failed to convert WSL Docker mount path: {result.stderr.strip()}"
        )
    return converted


class DockerContainerRunner:
    """Copy a workspace into a disposable, resource-bounded Docker container."""

    is_isolated = True

    def __init__(
        self,
        *,
        docker_executable="docker",
        image="alpine:3.20",
        staging_root=None,
        path_converter=None,
        memory="1g",
        cpus=1.0,
        pids_limit=128,
        process_runner=run_bounded_process,
        cleanup_runner=subprocess.run,
    ):
        if not str(docker_executable).strip() or not str(image).strip():
            raise ContainerConfigurationError("Docker executable and image must not be empty")
        if not re.fullmatch(r"[0-9]+(?:[kKmMgG])?", str(memory)):
            raise ContainerConfigurationError("Docker memory must be a positive Docker size")
        if isinstance(cpus, bool) or not isinstance(cpus, (int, float)) or cpus <= 0:
            raise ContainerConfigurationError("Docker CPUs must be positive")
        if isinstance(pids_limit, bool) or not isinstance(pids_limit, int) or pids_limit <= 0:
            raise ContainerConfigurationError("Docker PID limit must be positive")
        self.docker_executable = str(docker_executable)
        self.image = str(image)
        self.staging_root = Path(staging_root).resolve() if staging_root else None
        if self.staging_root is not None:
            try:
                self.staging_root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ContainerConfigurationError(
                    f"failed to prepare Docker staging root: {self.staging_root}"
                ) from exc
        self.path_converter = path_converter or _identity_path
        self.memory = str(memory)
        self.cpus = float(cpus)
        self.pids_limit = pids_limit
        self._process_runner = process_runner
        self._cleanup_runner = cleanup_runner

    @property
    def identity(self):
        return f"docker:{self.image}"

    @property
    def image_is_immutable(self):
        return is_immutable_container_image(self.image)

    def execute(self, command, *, cwd, env, control) -> ProcessOutcome:
        if not isinstance(control, ToolExecutionControl):
            raise TypeError("Docker container runner requires ToolExecutionControl")
        workspace = Path(cwd).resolve()
        if not workspace.is_dir():
            raise FileNotFoundError(f"benchmark workspace does not exist: {workspace}")
        self._reject_symlinks(workspace)
        parent = str(self.staging_root) if self.staging_root else None
        temporary = Path(
            tempfile.mkdtemp(prefix="repoagent-container-", dir=parent)
        )
        try:
            staged = temporary / "workspace"
            shutil.copytree(workspace, staged, ignore=_ignore_stage)
            container_workspace = self._container_workspace(workspace.name)
            container_name = f"repoagent-eval-{uuid.uuid4().hex}"
            docker_command = self._docker_command(
                command,
                staged=staged,
                container_name=container_name,
                container_workspace=container_workspace,
                env=env,
            )
            try:
                return self._process_runner(
                    docker_command,
                    cwd=workspace,
                    env=self._docker_environment(),
                    shell=False,
                    control=control,
                )
            finally:
                self._cleanup_runner(
                    [self.docker_executable, "rm", "--force", "--volumes", container_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                )
                self._scrub_staging(staged, container_workspace)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
            if temporary.exists():
                raise ContainerConfigurationError(
                    f"Docker staging cleanup failed: {temporary}"
                )

    def _docker_command(
        self,
        command,
        *,
        staged,
        container_name,
        container_workspace,
        env,
    ):
        if isinstance(command, str):
            shell_command = command
        else:
            shell_command = shlex.join(str(item) for item in command)
        if not shell_command.strip():
            raise ValueError("container command must not be empty")
        mount_source = self.path_converter(staged)
        if not isinstance(mount_source, str) or not mount_source.strip():
            raise ContainerConfigurationError("Docker mount path conversion failed")
        argv = [
            self.docker_executable,
            "run",
            "--name",
            container_name,
            "--rm",
            "--network",
            "none",
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
            "/tmp:rw,noexec,nosuid,nodev,size=128m",
            "--mount",
            f"type=bind,source={mount_source},target={container_workspace}",
            "--workdir",
            container_workspace,
        ]
        for name, value in sorted(dict(env or {}).items()):
            if _ENV_NAME.fullmatch(str(name)) is None:
                raise ValueError(f"invalid container environment name: {name}")
            argv.extend(("--env", f"{name}={value}"))
        argv.extend((self.image, "sh", "-lc", shell_command))
        return argv

    def _scrub_staging(self, staged, container_workspace):
        if not staged.exists():
            return
        mount_source = self.path_converter(staged)
        self._cleanup_runner(
            [
                self.docker_executable,
                "run",
                "--rm",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=bind,source={mount_source},target={container_workspace}",
                self.image,
                "sh",
                "-lc",
                (
                    f"chmod -R u+w {shlex.quote(container_workspace)} "
                    "2>/dev/null || true; "
                    f"find {shlex.quote(container_workspace)} -mindepth 1 -delete"
                ),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )

    @staticmethod
    def _container_workspace(name):
        name = str(name)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name):
            raise ContainerConfigurationError(
                f"workspace basename is unsafe for container execution: {name}"
            )
        return f"/workspace/{name}"

    @staticmethod
    def _reject_symlinks(workspace):
        for item in workspace.rglob("*"):
            if item.is_symlink():
                raise ValueError(f"benchmark workspace must not contain symlinks: {item}")

    @staticmethod
    def _docker_environment():
        names = ("HOME", "PATH", "SystemRoot", "WINDIR", "WSLENV")
        return {name: os.environ[name] for name in names if os.environ.get(name)}


__all__ = [
    "ContainerConfigurationError",
    "DockerContainerRunner",
    "is_immutable_container_image",
    "wsl_windows_path",
]
