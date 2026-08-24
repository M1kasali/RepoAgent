"""Versioned, atomic session JSON persistence."""

import json
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
        return self.root / f"{session_id}.json"

    def save(self, session):
        path = self.path(session["id"])
        lock_path = self.root / ".lock" / f"{path.name}.lock"
        with file_lock(lock_path):
            existing = self._read_persisted(path) if path.exists() else None
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
        payload = self._read_persisted(self.path(session_id))
        _version, revision = self._metadata(payload, self.path(session_id))
        payload.pop("_schema_version", None)
        payload.pop("_revision", None)
        self._revisions[str(session_id)] = revision
        return payload

    def latest(self):
        files = sorted(self.root.glob("*.json"), key=lambda path: path.stat().st_mtime)
        return files[-1].stem if files else None

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
            raise StorageCorruptionError(
                f"invalid session metadata: {path}"
            ) from exc
        if version not in {0, SESSION_FORMAT_VERSION}:
            raise StorageCorruptionError(
                f"unsupported session schema version {version}: {path}"
            )
        if revision < 0:
            raise StorageCorruptionError(f"invalid session revision: {path}")
        return version, revision
