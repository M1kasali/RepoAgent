"""Versioned, atomic session JSON persistence."""

import json
import re
from copy import deepcopy
from pathlib import Path

from .atomic_io import StorageCorruptionError, atomic_replace_unlocked, file_lock


SESSION_FORMAT_VERSION = 1


class StaleSessionWriteError(RuntimeError):
    pass


class SessionStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._revisions = {}

    def path(self, session_id):
        if (
            not isinstance(session_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", session_id) is None
        ):
            raise ValueError("invalid session identifier")
        path = self.root / f"{session_id}.json"
        if path.is_symlink() or path.resolve().parent != self.root.resolve():
            raise ValueError("session path escapes storage root")
        marker = self.root / ".deleted" / path.name
        if marker.parent.is_symlink() or marker.is_symlink():
            raise ValueError("invalid session deletion marker")
        if marker.exists():
            raise ValueError("session has been deleted")
        return path

    def save(self, session):
        path = self.path(session["id"])
        lock_path = self.root / ".lock" / f"{path.name}.lock"
        with file_lock(lock_path):
            self.path(session["id"])
            existing = self._read_persisted(path) if path.exists() else None
            if existing is not None and existing.get("id") != session["id"]:
                raise StorageCorruptionError("session identity does not match filename")
            actual_revision = self._metadata(existing, path)[1] if existing else 0
            expected_revision = self._revisions.get(str(session["id"]))
            if existing is not None and expected_revision is None:
                raise StaleSessionWriteError(
                    f"session {session['id']} must be loaded before it can be overwritten"
                )
            if expected_revision is not None and expected_revision != actual_revision:
                raise StaleSessionWriteError(
                    f"stale session revision for {session['id']}: "
                    f"expected {expected_revision}, found {actual_revision}"
                )
            next_revision = actual_revision + 1
            payload = dict(session)
            payload["_schema_version"] = SESSION_FORMAT_VERSION
            payload["_revision"] = next_revision
            atomic_replace_unlocked(
                path,
                json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            )
            self._revisions[str(session["id"])] = next_revision
        return path

    def load(self, session_id):
        payload = self.inspect(session_id)
        _version, revision = self._metadata(payload, self.path(session_id))
        payload.pop("_schema_version", None)
        payload.pop("_revision", None)
        self._revisions[str(session_id)] = revision
        return payload

    def inspect(self, session_id):
        """Read without changing the caller's optimistic-write revision."""
        path = self.path(session_id)
        payload = self._read_persisted(path)
        self._metadata(payload, path)
        if payload.get("id") != session_id:
            raise StorageCorruptionError("session identity does not match filename")
        return payload

    def latest(self):
        files = sorted(
            (
                path
                for path in self.root.glob("*.json")
                if not path.is_symlink()
                and not (self.root / ".deleted" / path.name).exists()
            ),
            key=lambda path: path.stat().st_mtime,
        )
        return files[-1].stem if files else None

    def delete(self, session_id, *, expected_revision):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("invalid expected session revision")
        path = self.path(session_id)
        with file_lock(self.root / ".lock" / f"{path.name}.lock"):
            payload = self.inspect(session_id)
            if self._metadata(payload, path)[1] != expected_revision:
                raise StaleSessionWriteError("session changed before deletion")
            marker = self.root / ".deleted" / path.name
            if marker.parent.is_symlink():
                raise ValueError("invalid session deletion directory")
            atomic_replace_unlocked(
                marker,
                json.dumps({"id": session_id, "revision": expected_revision}) + "\n",
            )
            path.unlink()
            self._revisions.pop(session_id, None)
        return True

    def rewrite(self, session_id, *, expected_revision, transform):
        """Commit a revision-fenced rewrite without advancing stale readers."""
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("invalid expected session revision")
        path = self.path(session_id)
        with file_lock(self.root / ".lock" / f"{path.name}.lock"):
            payload = self.inspect(session_id)
            if payload.get("_revision", 0) != expected_revision:
                raise StaleSessionWriteError("session changed before history rewrite")
            updated = transform(deepcopy(payload))
            if updated.get("id") != session_id or updated.get(
                "workspace_root"
            ) != payload.get("workspace_root"):
                raise ValueError("history rewrite changed session identity")
            updated["_schema_version"] = SESSION_FORMAT_VERSION
            updated["_revision"] = expected_revision + 1
            atomic_replace_unlocked(
                path, json.dumps(updated, sort_keys=True, ensure_ascii=True) + "\n"
            )
            self._revisions[session_id] = expected_revision + 1
            return updated

    @staticmethod
    def _read_persisted(path):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageCorruptionError(f"invalid session file: {path}") from exc
        if not isinstance(payload, dict):
            raise StorageCorruptionError(f"session file must contain an object: {path}")
        return payload

    @staticmethod
    def _metadata(payload, path):
        try:
            version = int(payload.get("_schema_version", 0))
            revision = int(payload.get("_revision", 0))
        except (TypeError, ValueError) as exc:
            raise StorageCorruptionError(f"invalid session metadata: {path}") from exc
        if version not in {0, SESSION_FORMAT_VERSION}:
            raise StorageCorruptionError(
                f"unsupported session schema version {version}: {path}"
            )
        if revision < 0:
            raise StorageCorruptionError(f"invalid session revision: {path}")
        return version, revision
