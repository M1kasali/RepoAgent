import asyncio
import hashlib
import json

import pytest

from repoagent.session_store import SessionStore
from repoagent.tui_rpc import RpcError, TUIRPCServer
from test_rpc_sessions import factory


def test_session_title_export_delete_and_stale_writer_fencing(tmp_path):
    async def scenario():
        make = factory(tmp_path)
        agent = make()
        old_id = agent.session["id"]

        async def send(frame):
            pass

        server = TUIRPCServer(agent, send, session_factory=make)
        await server.start()
        try:
            await server.dispatch("session.title", {"title": "A named session"})
            turn = await server.dispatch(
                "turn.send", {"content": "hello", "submission_id": "one"}
            )
            await server.host.wait(turn["turn_id"])
            assert agent.session_store.inspect(old_id)["title"] == "A named session"
            exported = await server.dispatch("session.export", {})
            from pathlib import Path

            payload = json.loads(Path(exported["path"]).read_text())
            assert (
                hashlib.sha256(
                    json.dumps(
                        payload["session"], sort_keys=True, ensure_ascii=True
                    ).encode()
                ).hexdigest()
                == exported["sha256"]
            )
            assert payload["session"]["history"][0]["content"] == "hello"
            with pytest.raises(RpcError, match="switch"):
                await server.dispatch("session.delete", {})
            await server.dispatch("session.create", {})
            stale = SessionStore(agent.session_store.root)
            stale_payload = stale.load(old_id)
            pending = await server.dispatch("session.delete", {"session_id": old_id})
            await server.dispatch(
                "session.title", {"session_id": old_id, "title": "Changed"}
            )
            with pytest.raises(RpcError, match="changed"):
                await server.dispatch(
                    "session.delete",
                    {
                        "session_id": old_id,
                        "confirmed": True,
                        "revision": pending["revision"],
                    },
                )
            pending = await server.dispatch("session.delete", {"session_id": old_id})
            assert await server.dispatch(
                "session.delete",
                {
                    "session_id": old_id,
                    "confirmed": True,
                    "revision": pending["revision"],
                },
            ) == {"deleted": old_id}
            with pytest.raises(ValueError, match="deleted"):
                stale.save(stale_payload)
            assert not (stale.root / f"{old_id}.json").exists()
            assert all(
                row["session_id"] != old_id
                for row in (await server.dispatch("session.list", {}))["sessions"]
            )
        finally:
            await server.close()

    asyncio.run(scenario())


def test_export_and_titles_are_redacted_and_workspace_scoped(tmp_path):
    async def scenario():
        agent = factory(tmp_path)()
        agent.register_secret("saved-private-key")

        async def send(frame):
            pass

        server = TUIRPCServer(agent, send)
        await server.start()
        try:
            result = await server.dispatch(
                "session.title", {"title": "saved-private-key"}
            )
            assert result["title"] == "<redacted>"
            for method in ("session.title", "session.delete", "session.export"):
                with pytest.raises(RpcError):
                    await server.dispatch(method, {"session_id": "../other"})
            for title in (True, [], "x" * 201, "bad\ntitle"):
                with pytest.raises(RpcError):
                    await server.dispatch("session.title", {"title": title})
        finally:
            await server.close()

    asyncio.run(scenario())
