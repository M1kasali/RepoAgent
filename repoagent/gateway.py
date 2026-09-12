"""Local gateway lifecycle with crash-recoverable single-instance ownership."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from uuid import uuid4

from .atomic_io import atomic_replace, file_lock


class GatewayAlreadyRunningError(RuntimeError):
    pass


def _windows_pid_alive(pid):
    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5
    exit_code = ctypes.c_ulong()
    try:
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid):
    if pid < 1:
        return False
    if os.name == "nt":
        return _windows_pid_alive(pid)
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
        with file_lock(self.directory.parent / "gateway-transition.lock"):
            return self._acquire_locked()

    def _acquire_locked(self):
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
        with file_lock(self.directory.parent / "gateway-transition.lock"):
            return self._release_locked()

    def _release_locked(self):
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
    def __init__(self, host, *, state_root, channels=(), durable_directory=False):
        self.host = host
        channels = tuple(channels)
        self.channels = {channel.name: channel for channel in channels}
        if len(self.channels) != len(channels):
            raise ValueError("gateway channel names must be unique")
        self.lease = GatewayLease(state_root)
        self.running = False
        self._started_channels = []
        self._host_started = False
        self.durable_directory = durable_directory
        self._delivery_workers = []

    async def start(self):
        if self.running:
            return False
        self.lease.acquire()
        try:
            self._host_started = True
            await self.host.start()
            for channel in self.channels.values():
                if self.durable_directory and channel.name == "directory":
                    from .channel_receipts import ChannelReceipts, DurableDirectoryDelivery

                    worker = DurableDirectoryDelivery(
                        self.host, channel,
                        ChannelReceipts(self.lease.directory.parent / "channel-receipts.sqlite3"),
                    )
                    self._delivery_workers.append(worker)
                    channel.intake.wire(worker.submit)
                    await worker.start()
                else:
                    channel.intake.wire(
                        lambda message, current=channel: self.host.submit(
                            message, deliver=current.send
                        )
                    )
                self._started_channels.append(channel)
                await channel.start()
        except BaseException:
            try:
                await self.stop()
            except Exception:
                pass
            raise
        self.running = True
        return True

    async def stop(self, grace=5.0):
        for channel in self._started_channels:
            channel.intake.seal()
        errors = []
        try:
            for channel in reversed(self._started_channels):
                try:
                    await channel.stop()
                except Exception as exc:
                    errors.append(exc)
            if self._host_started:
                try:
                    await self.host.stop(grace=grace)
                except Exception as exc:
                    errors.append(exc)
            for worker in self._delivery_workers:
                try:
                    await worker.stop()
                    await worker.reconcile()
                except Exception as exc:
                    errors.append(exc)
        finally:
            self._delivery_workers.clear()
            self._started_channels.clear()
            self._host_started = False
            self.running = False
            self.lease.release()
        if errors:
            raise errors[0]

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
