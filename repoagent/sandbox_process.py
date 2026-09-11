"""Docker-owned long-running stdio processes using the official MCP bridge."""

from contextlib import asynccontextmanager
import math
import os
import subprocess
import uuid

from .sandbox import SandboxConfigurationError


def _run(adapter, args, *, timeout=10):
    try:
        return adapter._lifecycle_runner(
            [adapter.executable, *args],
            cwd=adapter.workspace,
            env=adapter._docker_environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SandboxConfigurationError(
            "sandbox process lifecycle command failed"
        ) from exc


def remove_process_container(adapter, name):
    result = _run(adapter, ["rm", "--force", "--volumes", name])
    if result.returncode != 0:
        # An absent container is success only if a reachable daemon confirms it.
        remaining = _run(
            adapter,
            [
                "container",
                "ls",
                "--all",
                "--filter",
                f"name=^/{name}$",
                "--format",
                "{{.Names}}",
            ],
        )
        if remaining.returncode != 0 or remaining.stdout.strip():
            raise SandboxConfigurationError("sandbox process container cleanup failed")
    with adapter._process_lock:
        adapter._process_names.discard(name)


@asynccontextmanager
async def docker_process(adapter, command, args, *, cwd, env, startup_timeout):
    import anyio
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    if (
        not isinstance(command, str)
        or not command.strip()
        or not isinstance(args, (list, tuple))
        or not all(isinstance(arg, str) for arg in args)
        or any("\0" in arg for arg in (command, *args))
    ):
        raise SandboxConfigurationError("sandbox process requires executable and argv")
    if (
        isinstance(startup_timeout, bool)
        or not isinstance(startup_timeout, (int, float))
        or not math.isfinite(startup_timeout)
        or startup_timeout <= 0
    ):
        raise SandboxConfigurationError("sandbox process timeout must be positive")
    if not isinstance(env, dict) or not all(
        isinstance(key, str)
        and adapter._ENV_NAME.fullmatch(key)
        and isinstance(value, str)
        and "\0" not in value
        for key, value in env.items()
    ):
        raise SandboxConfigurationError("sandbox process env must be a string mapping")

    options = adapter._container_options(cwd=cwd, env=env, explicit_env=True)
    name = f"repoagent-mcp-{uuid.uuid4().hex}"
    create = [
        "create",
        "--name",
        name,
        "--interactive",
        "--init",
        "--pull",
        "never",
        *options,
        "--entrypoint",
        command,
        adapter.image,
        *args,
    ]
    with adapter._process_lock:
        adapter._process_names.add(name)
    try:
        # Create finishes before attach can start; cancellation cannot leave a
        # background docker-run request creating an untracked late container.
        with anyio.CancelScope(shield=True):
            result = await anyio.to_thread.run_sync(
                lambda: _run(adapter, create, timeout=startup_timeout)
            )
        if result.returncode != 0:
            raise SandboxConfigurationError("sandbox process container creation failed")
        await anyio.lowlevel.checkpoint()
        params = StdioServerParameters(
            command=adapter.executable,
            args=["start", "--attach", "--interactive", name],
            cwd=str(adapter.workspace),
            env=adapter._docker_environment(),
        )
        with open(os.devnull, "w") as errlog:
            async with stdio_client(params, errlog=errlog) as streams:
                try:
                    yield streams
                finally:
                    # Kill the container before awaiting host CLI teardown.
                    # Killing docker.exe alone does not stop daemon-owned work.
                    with anyio.CancelScope(shield=True):
                        await anyio.to_thread.run_sync(
                            remove_process_container, adapter, name
                        )
    finally:
        with adapter._process_lock:
            pending = name in adapter._process_names
        if pending:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(remove_process_container, adapter, name)
