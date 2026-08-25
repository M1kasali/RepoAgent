"""Local gateway lifecycle with crash-recoverable single-instance ownership."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from uuid import uuid4

from .atomic_io import atomic_replace


class GatewayAlreadyRunningError(RuntimeError):
    pass


def _pid_alive(pid):
    if pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class GatewayLease:
    def __init__(self, state_root):
        self.directory = Path(state_root) / "gateway.lease"
        self.owner_path = self.directory / "owner.json"
        self.owner_id = "gateway_" + uuid4().hex
        self.held = False

    def status(self):
        try:
            payload = json.loads(self.owner_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"running": False, "pid": -1, "owner_id": ""}
        return {
            "running": _pid_alive(int(payload.get("pid", -1))),
            "pid": int(payload.get("pid", -1)),
            "owner_id": str(payload.get("owner_id", "")),
            "started_at": float(payload.get("started_at", 0.0)),
        }

    def acquire(self):
        for _attempt in range(2):
            try:
                self.directory.mkdir(parents=True)
            except FileExistsError:
                status = self.status()
                if status["running"]:
                    raise GatewayAlreadyRunningError(
                        f"gateway already running (pid {status['pid']})"
                    )
                self.owner_path.unlink(missing_ok=True)
                try:
                    self.directory.rmdir()
                except OSError:
                    pass
                continue
            atomic_replace(
                self.owner_path,
                json.dumps(
                    {
                        "owner_id": self.owner_id,
                        "pid": os.getpid(),
                        "started_at": time.time(),
                    },
                    sort_keys=True,
                )
                + "\n",
                lock_path=self.directory.parent / "gateway-owner.lock",
            )
            self.held = True
            return self.status()
        raise GatewayAlreadyRunningError("gateway lease could not be acquired")

    def release(self):
        if not self.held:
            return False
        status = self.status()
        if status.get("owner_id") != self.owner_id:
            self.held = False
            return False
        self.owner_path.unlink(missing_ok=True)
        try:
            self.directory.rmdir()
        except OSError:
            pass
        self.held = False
        return True


class LocalGateway:
    def __init__(self, host, *, state_root, channels=()):
        self.host = host
        channels = tuple(channels)
        self.channels = {channel.name: channel for channel in channels}
        if len(self.channels) != len(channels):
            raise ValueError("gateway channel names must be unique")
        self.lease = GatewayLease(state_root)
        self.running = False

    async def start(self):
        self.lease.acquire()
        try:
            await self.host.start()
            for channel in self.channels.values():
                channel.intake.wire(
                    lambda message, current=channel: self.host.submit(
                        message, deliver=current.send
                    )
                )
                await channel.start()
        except BaseException:
            self.lease.release()
            raise
        self.running = True

    async def stop(self, grace=5.0):
        for channel in self.channels.values():
            channel.intake.seal()
        for channel in reversed(tuple(self.channels.values())):
            await channel.stop()
        await self.host.stop(grace=grace)
        self.running = False
        self.lease.release()

    def health(self):
        lease = self.lease.status()
        return {
            "schema": "repoagent.gateway-health/v1",
            "status": "healthy" if self.running and lease["running"] else "stopped",
            "running": self.running,
            "lease": lease,
            "channels": sorted(self.channels),
        }


__all__ = [
    "GatewayAlreadyRunningError",
    "GatewayLease",
    "LocalGateway",
]
