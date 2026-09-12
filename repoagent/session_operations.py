"""Version-fenced session metadata and portable history operations."""

from copy import deepcopy
import hashlib
import json
from uuid import uuid4

from .atomic_io import atomic_replace
from .session_store import SessionStore, StaleSessionWriteError
from .features.memory import default_memory_state
from .workspace import now


def rewritten_session(source, history, *, operation, session_id=None, title=None):
    """Retain conversation only; old checkpoints and summaries are not replayable."""
    return {
        "id": session_id or source["id"],
        "created_at": now() if session_id else source.get("created_at", now()),
        "workspace_root": source["workspace_root"],
        "title": source.get("title", "") if title is None else title,
        "history": deepcopy(history),
        "memory": default_memory_state(),
        "memory_track_id": "memory_" + uuid4().hex,
        "checkpoints": {"current_id": "", "items": {}},
        "runtime_identity": {},
        "resume_state": {},
        "history_rewrite": {
            "operation": operation,
            "source_session_id": source["id"],
            "source_revision": source.get("_revision", 0),
            "created_at": now(),
        },
    }


async def rewrite_operation(server, method, params, payload):
    from .tui_rpc import RpcError

    session_id = payload["id"]
    current = session_id == server.session_id
    history = payload["history"]
    if method == "session.branch":
        if not history:
            return {"session_id": None, "message_count": 0}
        title = params.get("name", payload.get("title", ""))
        if (
            not isinstance(title, str)
            or len(title) > 200
            or any(ord(ch) < 32 for ch in title)
        ):
            raise RpcError(-32602, "invalid branch title")
        child_id = "branch_" + uuid4().hex
        child = rewritten_session(
            payload,
            server.agent.redact_artifact(history),
            operation="branch",
            session_id=child_id,
            title=server.agent.redact_text(title),
        )
        server.agent.session_store.save(child)
        return {
            "session_id": child_id,
            "title": child["title"],
            "message_count": len(history),
            "source_revision": payload.get("_revision", 0),
        }
    if current and server.session_factory is None:
        raise RpcError(-32601, "history editing requires a runtime session factory")
    boundaries = [
        index for index, row in enumerate(history) if row.get("role") == "user"
    ]
    if method == "session.undo":
        count = params.get("n", 1)
        if type(count) is not int or not 1 <= count <= 1000:
            raise RpcError(-32602, "n must be an integer between 1 and 1000")
        if not boundaries:
            return {"session_id": session_id, "removed": 0, "changed": False}
        retained = history[: boundaries[-min(count, len(boundaries))]]
        removed = min(count, len(boundaries))
    else:
        retained, removed = [], len(boundaries)
    revision = payload.get("_revision", 0)
    if params.get("confirmed") is not True:
        return {
            "session_id": session_id,
            "confirmation_required": True,
            "revision": revision,
            "removed": removed,
            "operation": method,
        }
    if type(params.get("revision")) is not int or params["revision"] != revision:
        raise RpcError(-32006, "session changed; request confirmation again")
    server._configuring = True
    try:
        try:
            server.agent.session_store.rewrite(
                session_id,
                expected_revision=revision,
                transform=lambda source: rewritten_session(
                    source, retained, operation=method.split(".")[1]
                ),
            )
        except Exception:
            # Atomic replacement may have succeeded before a durability error.
            try:
                changed = server._session(session_id).get("_revision", 0) != revision
            except Exception:
                changed = True
            if current and changed:
                server._switch_failed = True
                await server.close()
            raise
        if current:
            try:
                await server._switch_session(session_id, force=True)
            except RpcError:
                raise RpcError(
                    -32007, "history was committed but runtime reload failed; reconnect"
                ) from None
        return {
            "session_id": session_id,
            "changed": True,
            "cleared": method == "session.clear",
            "removed": removed,
            "resubscribe": current,
            "workspace_files_unchanged": True,
            "shared_memory_retained": True,
        }
    finally:
        server._configuring = False


async def dispatch_session_operation(server, method, params):
    from .tui_rpc import RpcError

    session_id = params.get("session_id", server.session_id)
    payload = server._session(session_id)
    if method == "session.export":
        exported = server.agent.redact_artifact(
            {
                "schema": "repoagent.session-export/v1",
                "session_id": session_id,
                "title": payload.get("title", ""),
                "history": payload["history"],
            }
        )
        encoded = json.dumps(exported, sort_keys=True, ensure_ascii=True)
        envelope = {
            "session": exported,
            "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        }
        root = server.agent.session_store.root.parent / "exports"
        if root.is_symlink():
            raise RpcError(-32602, "invalid export directory")
        path = root / f"{session_id}-{uuid4().hex}.json"
        atomic_replace(
            path, json.dumps(envelope, sort_keys=True, ensure_ascii=True) + "\n"
        )
        verified = json.loads(path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(
            json.dumps(verified["session"], sort_keys=True, ensure_ascii=True).encode()
        ).hexdigest()
        if digest != verified["sha256"]:
            raise RpcError(-32006, "export verification failed")
        return {"exported": True, "path": str(path), "sha256": digest}
    if method == "session.title" and "title" not in params:
        return {"session_id": session_id, "title": payload.get("title", "")}
    if server.host.busy or server.confirmations.pending() or server.questions.pending:
        raise RpcError(
            -32003, "finish or cancel the active Turn before editing sessions"
        )
    if method in {"session.clear", "session.undo", "session.branch"}:
        return await rewrite_operation(server, method, params, payload)
    if method == "session.title":
        title = params.get("title")
        if (
            not isinstance(title, str)
            or len(title) > 200
            or any(ord(ch) < 32 for ch in title)
        ):
            raise RpcError(
                -32602, "title must be a single line of at most 200 characters"
            )
        title = server.agent.redact_text(title)
        if session_id == server.session_id:
            updated = deepcopy(server.agent.session)
            updated["title"] = title
            server.agent.session_store.save(updated)
            server.agent.session["title"] = title
        else:
            store = SessionStore(server.agent.session_store.root)
            updated = store.load(session_id)
            if updated != {
                key: value
                for key, value in payload.items()
                if key not in {"_revision", "_schema_version"}
            }:
                raise StaleSessionWriteError("session changed before title edit")
            updated["title"] = title
            store.save(updated)
        return {"session_id": session_id, "title": title, "pending": False}
    if method == "session.delete":
        if session_id == server.session_id:
            raise RpcError(-32003, "switch to another session before deleting this one")
        if params.get("confirmed") is not True:
            return {
                "deleted": None,
                "confirmation_required": True,
                "revision": payload.get("_revision", 0),
            }
        revision = params.get("revision")
        if type(revision) is not int or revision != payload.get("_revision", 0):
            raise RpcError(
                -32006, "session changed; request deletion confirmation again"
            )
        server.agent.session_store.delete(session_id, expected_revision=revision)
        return {"deleted": session_id}
    raise RpcError(-32601, "method not found")
