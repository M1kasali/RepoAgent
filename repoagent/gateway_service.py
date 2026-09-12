"""Foreground gateway service using the existing scheduler and channel contracts."""

import asyncio
import os
from pathlib import Path
import signal

from .channels import DirectoryChannel
from .gateway import LocalGateway
from .paths import workspace_state_root
from .runtime_host import RuntimeHost


async def run_gateway(
    agent,
    *,
    directory=None,
    allow_from,
    channel_kind="directory",
    stop_event=None,
    on_ready=None,
):
    if agent.approval_policy == "ask":
        raise ValueError(
            "gateway requires non-interactive approval: never or explicit auto"
        )
    if not allow_from:
        raise ValueError("gateway requires an explicit sender allowlist")
    if channel_kind == "directory":
        if not directory:
            raise ValueError("directory gateway requires --directory")
        root = Path(directory).expanduser()
        if not root.is_absolute():
            root = Path(agent.root) / root
        channel = DirectoryChannel(root, allow_from=allow_from)
        details = {"directory": str(root.resolve())}
    elif channel_kind == "qq":
        from .qq_channel import QQChannel

        if directory:
            raise ValueError("QQ gateway does not use --directory")
        secret = os.environ.get("REPOAGENT_QQ_SECRET", "")
        agent.register_secret(secret)
        channel = QQChannel(
            app_id=os.environ.get("REPOAGENT_QQ_APP_ID", ""),
            secret=secret,
            allow_from=allow_from,
        )
        details = {"platform": "qq"}
    else:
        raise ValueError("unsupported gateway channel")
    gateway = LocalGateway(
        RuntimeHost(agent),
        state_root=workspace_state_root(agent.root),
        channels=(channel,),
        durable_directory=True,
    )
    stopped = stop_event if stop_event is not None else asyncio.Event()
    loop = asyncio.get_running_loop()
    installed = False
    try:
        if stop_event is None:
            try:
                loop.add_signal_handler(signal.SIGTERM, stopped.set)
                installed = True
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        await gateway.start()
        if on_ready is not None:
            on_ready({**gateway.health(), **details})
        if channel_kind == "qq":
            waiter = asyncio.create_task(stopped.wait())
            try:
                done, _ = await asyncio.wait(
                    (waiter, channel.task), return_when=asyncio.FIRST_COMPLETED
                )
                if channel.task in done and not stopped.is_set():
                    raise RuntimeError("QQ connection stopped; restart the gateway")
            finally:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
        else:
            await stopped.wait()
        return 0
    finally:
        try:
            await gateway.stop()
        finally:
            if installed:
                loop.remove_signal_handler(signal.SIGTERM)
