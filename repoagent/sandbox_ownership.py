"""Durable, host-only ownership and conservative Docker orphan reconciliation."""

import hashlib
import json
import os
from pathlib import Path
import re
import time

from .atomic_io import LockUnavailableError, atomic_replace_unlocked, file_lock
from .sandbox import SandboxConfigurationError
from .sandbox_process import _run


class SandboxOwnership:
    def __init__(self, workspace, *, root=None):
        self.workspace = Path(workspace).resolve()
        self.scope = hashlib.sha256(str(self.workspace).encode()).hexdigest()
        base = Path(root) if root is not None else (
            Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
            / "repoagent/sandbox-owners"
        )
        self.root = base.expanduser().resolve() / self.scope
        if self.root.is_relative_to(self.workspace):
            raise SandboxConfigurationError("sandbox ownership must be outside the mounted workspace")
        self._lease = None
        self.record = None

    def _directory(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _save(self, record):
        atomic_replace_unlocked(
            self.root / f"{record['token']}.json", json.dumps(record, sort_keys=True) + "\n"
        )

    @staticmethod
    def daemon_id(adapter, *, timeout=10):
        result = _run(adapter, ["info", "--format", "{{.ID}}"], timeout=timeout)
        identity = result.stdout.strip()
        if result.returncode or not identity:
            raise SandboxConfigurationError("Docker engine identity unavailable")
        return identity

    def claim(self, adapter, name, *, timeout=10):
        self._directory()
        daemon = self.daemon_id(adapter, timeout=timeout)
        token = name.removeprefix("repoagent-session-")
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            raise SandboxConfigurationError("invalid owned sandbox name")
        lease = file_lock(self.root / f"{token}.lease", blocking=False)
        lease.__enter__()
        record = {
            "schema": "repoagent.sandbox-owner/v1", "token": token,
            "scope": self.scope, "name": name, "daemon": daemon,
            "state": "intent", "created_at": time.time(),
        }
        try:
            self._save(record)
        except BaseException:
            lease.__exit__(None, None, None)
            raise
        self._lease, self.record = lease, record
        return ["--label", f"repoagent.owner={token}", "--label", f"repoagent.scope={self.scope}"]

    def created(self):
        self.record["state"] = "created"
        self._save(self.record)

    def release(self):
        if self._lease is not None:
            lease, self._lease = self._lease, None
            lease.__exit__(None, None, None)

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SandboxConfigurationError("sandbox reconciliation timed out")
        return remaining

    def _cleanup(self, adapter, record, daemon, deadline):
        if record["daemon"] != daemon:
            return "other_engine"
        if record["state"] == "closed":
            return "closed"
        result = _run(adapter, [
            "container", "ls", "--all", "--no-trunc", "--quiet",
            "--filter", f"name=^/{record['name']}$",
            "--filter", f"label=repoagent.owner={record['token']}",
            "--filter", f"label=repoagent.scope={self.scope}",
        ], timeout=self._remaining(deadline))
        if result.returncode:
            raise SandboxConfigurationError("sandbox ownership lookup failed")
        ids = result.stdout.split()
        if len(ids) > 1 or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in ids):
            raise SandboxConfigurationError("sandbox ownership lookup returned invalid IDs")
        for container_id in ids:
            result = _run(adapter, ["rm", "--force", "--volumes", container_id], timeout=self._remaining(deadline))
            if result.returncode:
                raise SandboxConfigurationError("owned sandbox removal unconfirmed")
        # An intent can still be executing remotely after the CLI timed out.
        # Only an observed deletion or acknowledged create permits final closure.
        if ids or record["state"] == "created":
            record["state"] = "closed"
            self._save(record)
            return "removed" if ids else "absent"
        return "watching"

    def cleanup_owned(self, adapter):
        if self.record is None:
            return
        if self._lease is None:
            lease = file_lock(self.root / f"{self.record['token']}.lease", blocking=False)
            try:
                lease.__enter__()
            except LockUnavailableError as exc:
                raise SandboxConfigurationError("sandbox ownership cleanup is already running") from exc
            self._lease = lease
        deadline = time.monotonic() + 10
        try:
            status = self._cleanup(adapter, self.record, self.daemon_id(adapter), deadline)
            if status == "other_engine":
                raise SandboxConfigurationError("Docker engine changed during sandbox ownership")
        finally:
            self.release()

    def reconcile(self, adapter, *, timeout=10):
        deadline = time.monotonic() + timeout
        self._directory()
        paths = sorted(self.root.glob("*.json"))
        if not paths:
            return []
        daemon = self.daemon_id(adapter, timeout=self._remaining(deadline))
        rows = []
        for path in paths:
            token = path.stem
            if not re.fullmatch(r"[0-9a-f]{32}", token):
                rows.append({"token": token, "status": "invalid"})
                continue
            try:
                with file_lock(self.root / f"{token}.lease", blocking=False):
                    record = json.loads(path.read_text(encoding="utf-8"))
                    if not (
                        isinstance(record, dict)
                        and record.get("schema") == "repoagent.sandbox-owner/v1"
                        and record.get("token") == token
                        and record.get("scope") == self.scope
                        and record.get("name") == f"repoagent-session-{token}"
                        and isinstance(record.get("daemon"), str)
                        and record.get("state") in {"intent", "created", "closed"}
                    ):
                        raise ValueError("invalid ownership record")
                    status = self._cleanup(adapter, record, daemon, deadline)
            except LockUnavailableError:
                status = "active"
            except SandboxConfigurationError:
                status = "failed"
            except (OSError, ValueError, TypeError):
                status = "invalid"
            rows.append({"token": token, "status": status})
        return rows
