"""RepoAgent dictionary facade over Pico's JSONL SessionManager.

The manager owns append/rewrite, partial tails and epoch/content fences. Only
RepoAgent's opaque IDs and non-transcript state are mapped here.
"""

import json
import re
from copy import deepcopy
from pathlib import Path

from .atomic_io import StorageCorruptionError, atomic_replace_unlocked, file_lock
from .session.manager import Session, SessionManager
from .session.atomic_io import StorageCorruptionError as SessionCorruptionError

SESSION_FORMAT_VERSION = 1


class StaleSessionWriteError(RuntimeError, ValueError):
    pass


class _RootedSessionManager(SessionManager):
    def __init__(self, root):
        # SessionStore historically accepts the sessions directory itself.
        self.workspace = root.parent
        self.sessions_dir = root
        self._cache = {}


class SessionStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.manager = _RootedSessionManager(self.root)
        self._loaded = {}
        self._revisions = {}

    @staticmethod
    def _key(session_id):
        if (
            not isinstance(session_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", session_id) is None
        ):
            raise ValueError("invalid session identifier")
        # RepoAgent's public ID remains opaque; the storage key follows Pico.
        return "cli:" + session_id

    def path(self, session_id):
        path = self.manager._get_session_path(self._key(session_id))
        if (
            path.is_symlink()
            or path.parent.is_symlink()
            or path.resolve().parent != self.root.resolve() / "cli"
        ):
            raise ValueError("session path escapes storage root")
        return path

    def ids(self):
        ids = {
            p.stem for p in (self.root / "cli").glob("*.jsonl") if not p.is_symlink()
        }
        ids.update(
            p.stem
            for p in self.root.glob("*.json")
            if not p.is_symlink() and not (self.root / ".deleted" / p.name).exists()
        )
        return sorted(ids)

    @staticmethod
    def _metadata(payload, path):
        version = payload.get("_schema_version", 0)
        revision = payload.get("_revision", 0)
        if type(version) is not int or version not in {0, SESSION_FORMAT_VERSION}:
            raise StorageCorruptionError(
                f"unsupported session schema version {version}: {path}"
            )
        if type(revision) is not int or revision < 0:
            raise StorageCorruptionError(f"invalid session revision: {path}")
        return version, revision

    def _decode(self, value, session_id):
        payload = deepcopy(value.metadata.get("repoagent"))
        if not isinstance(payload, dict) or payload.get("id") != session_id:
            raise StorageCorruptionError("session identity does not match filename")
        payload["history"] = deepcopy(value.messages)
        self._metadata(payload, self.path(session_id))
        return payload

    @staticmethod
    def _assign(value, payload):
        value.messages = deepcopy(payload.get("history", []))
        if not isinstance(value.messages, list) or not all(
            isinstance(m, dict) for m in value.messages
        ):
            raise StorageCorruptionError("session history must contain message objects")
        if any(m.get("_type") == "metadata" for m in value.messages):
            raise StorageCorruptionError("reserved session metadata record in history")
        value.metadata["repoagent"] = deepcopy(
            {k: v for k, v in payload.items() if k != "history"}
        )
        value.metadata["title"] = payload.get("title")

    def _legacy_payload(self, session_id):
        self._key(session_id)
        legacy = self.root / f"{session_id}.json"
        if legacy.is_symlink():
            raise ValueError("invalid legacy session path")
        try:
            payload = json.loads(legacy.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise StorageCorruptionError(f"invalid legacy session: {legacy}") from exc
        if not isinstance(payload, dict) or payload.get("id") != session_id:
            raise StorageCorruptionError("session identity does not match filename")
        self._metadata(payload, legacy)
        payload.setdefault("_schema_version", SESSION_FORMAT_VERSION)
        payload.setdefault("_revision", 0)
        return payload

    def _migrate(self, session_id):
        """One-way migration, preserving the old file and fencing old writers."""
        path = self.path(session_id)
        legacy = self.root / f"{session_id}.json"
        marker = self.root / ".deleted" / legacy.name
        if not legacy.exists() or marker.exists():
            return
        if legacy.is_symlink() or marker.parent.is_symlink():
            raise ValueError("invalid legacy session path")
        # This is the exact lock anchor used by the previous JSON SessionStore.
        with file_lock(self.root / ".lock" / f"{legacy.name}.lock"):
            if marker.exists():
                return
            payload = self._legacy_payload(session_id)
            existing = self.manager._load(self._key(session_id))
            if existing is None:
                value = self.manager.get_or_create(self._key(session_id))
                self._assign(value, payload)
                self.manager.save(value)
            elif self._decode(existing, session_id) != {
                **payload,
                "history": payload.get("history", []),
            }:
                raise StorageCorruptionError(
                    "legacy and JSONL sessions differ; migration refused"
                )
            # Retain legacy bytes as a backup; old writers reject this marker.
            atomic_replace_unlocked(
                marker, json.dumps({"id": session_id, "migrated_to": str(path)}) + "\n"
            )

    def _read(self, session_id):
        self.path(session_id)
        try:
            self._migrate(session_id)
            value = self.manager._load(self._key(session_id))
        except SessionCorruptionError as exc:
            raise StorageCorruptionError(str(exc)) from exc
        if value is None:
            raise ValueError(f"session missing or has been deleted: {session_id}")
        return value

    def inspect(self, session_id):
        path = self.path(session_id)
        legacy = self.root / f"{session_id}.json"
        if (
            not path.exists()
            and legacy.exists()
            and not (self.root / ".deleted" / legacy.name).exists()
        ):
            # Listing/exporting old sessions must remain read-only. Migration
            # occurs when the session is loaded for execution or saved.
            return self._legacy_payload(session_id)
        return self._decode(self._read(session_id), session_id)

    def load(self, session_id):
        value = self._read(session_id)
        payload = self._decode(value, session_id)
        self._loaded[session_id] = value
        self._revisions[session_id] = payload.pop("_revision", 0)
        payload.pop("_schema_version", None)
        return payload

    def _commit(self, value, payload, *, force_rewrite=False):
        # Failed writes must not advance the facade's cached expected snapshot.
        candidate = deepcopy(value)
        self._assign(candidate, payload)
        try:
            self.manager.save(candidate, force_rewrite=force_rewrite)
        except SessionCorruptionError as exc:
            raise StorageCorruptionError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise StaleSessionWriteError(
                f"stale session or deleted generation: {value.key}"
            ) from exc
        sid = payload["id"]
        self._loaded[sid] = candidate
        self._revisions[sid] = payload["_revision"]
        return self.path(sid)

    def save(self, session):
        self._key(session["id"])
        with file_lock(self.root / ".operations" / f"{session['id']}.lock"):
            return self._save_locked(session)

    def _save_locked(self, session):
        sid = session["id"]
        path = self.path(sid)
        self._migrate(sid)
        value = self._loaded.get(sid)
        if value is None:
            if path.exists():
                self.inspect(sid)  # Report corruption before a missing-load error.
                raise StaleSessionWriteError(
                    f"session {sid} must be loaded before it can be overwritten"
                )
            if (self.root / ".deleted" / f"{sid}.json").exists():
                raise ValueError("session has been deleted")
            from .session.atomic_io import epoch_is_known

            if epoch_is_known(path):
                raise StaleSessionWriteError("session has been deleted")
            value = Session(key=self._key(sid))
        payload = deepcopy(session)
        payload["_schema_version"] = SESSION_FORMAT_VERSION
        payload["_revision"] = self._revisions.get(sid, 0) + 1
        return self._commit(value, payload)

    def latest(self):
        ids = self.ids()
        return (
            max(
                ids,
                key=lambda sid: (
                    (
                        self.path(sid)
                        if self.path(sid).exists()
                        else self.root / f"{sid}.json"
                    )
                    .stat()
                    .st_mtime
                ),
            )
            if ids
            else None
        )

    def delete(self, session_id, *, expected_revision):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("invalid expected session revision")
        self._key(session_id)
        # Serialize facade revision check with other facade mutations. The core
        # delete independently fences the epoch against upstream writers.
        with file_lock(self.root / ".operations" / f"{session_id}.lock"):
            value = self._read(session_id)
            if self._decode(value, session_id).get("_revision", 0) != expected_revision:
                raise StaleSessionWriteError("session changed before deletion")
            try:
                removed = self.manager.delete(
                    value.key, expected_epoch=value._storage_epoch, expected_exists=True
                )
            except FileNotFoundError as exc:
                raise StaleSessionWriteError("session changed before deletion") from exc
            if not removed:
                raise OSError("session deletion failed")
            self._loaded.pop(session_id, None)
            self._revisions.pop(session_id, None)
            return True

    def rewrite(self, session_id, *, expected_revision, transform):
        self._key(session_id)
        with file_lock(self.root / ".operations" / f"{session_id}.lock"):
            return self._rewrite_locked(
                session_id, expected_revision=expected_revision, transform=transform
            )

    def _rewrite_locked(self, session_id, *, expected_revision, transform):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("invalid expected session revision")
        value = self._read(session_id)
        payload = self._decode(value, session_id)
        if payload.get("_revision", 0) != expected_revision:
            raise StaleSessionWriteError("session changed before history rewrite")
        updated = transform(deepcopy(payload))
        if updated.get("id") != session_id or updated.get(
            "workspace_root"
        ) != payload.get("workspace_root"):
            raise ValueError("history rewrite changed session identity")
        updated["_schema_version"] = SESSION_FORMAT_VERSION
        updated["_revision"] = expected_revision + 1
        self._commit(value, updated, force_rewrite=True)
        return updated
