import asyncio

import pytest

from repoagent.session_store import SessionStore, StaleSessionWriteError
from repoagent.tui_rpc import RpcError, TUIRPCServer
from test_rpc_sessions import factory


def seeded(make):
    agent = make()
    agent.session["history"] = [
        {"role": "user", "content": "keep"},
        {"role": "assistant", "content": "first"},
        {"role": "user", "content": "remove-marker"},
        {"role": "assistant", "content": "second"},
        {"role": "tool", "content": "tool-marker"},
    ]
    agent.session["memory"]["working"]["task_summary"] = "remove-marker"
    agent.session["checkpoints"] = {
        "current_id": "old",
        "items": {"old": {"summary": "remove-marker"}},
    }
    agent.session_store.save(agent.session)
    return agent


async def send(frame):
    pass


def test_external_memory_uses_fresh_track_after_history_rewrite(tmp_path):
    from repoagent import InMemoryMemoryBackend

    async def scenario():
        backend = InMemoryMemoryBackend()
        base = factory(tmp_path)

        def make(session_id=None):
            agent = base(session_id)
            agent.memory_backend = backend
            return agent

        agent = make()
        server = TUIRPCServer(agent, send, session_factory=make)
        await server.start()
        try:
            first = await server.dispatch(
                "turn.send", {"content": "unique-old-marker", "submission_id": "first"}
            )
            await server.host.wait(first["turn_id"])
            old_track = agent.memory_track_id
            assert await backend.recall("unique-old-marker", user_id=old_track, top_k=3)
            await confirmed(server, "session.clear")
            new_track = server.agent.memory_track_id
            assert not await backend.recall(
                "unique-old-marker", user_id=new_track, top_k=3
            )
            second = await server.dispatch(
                "turn.send", {"content": "new-marker", "submission_id": "second"}
            )
            await server.host.wait(second["turn_id"])
            assert await backend.recall("new-marker", user_id=new_track, top_k=3)
            assert await backend.recall("unique-old-marker", user_id=old_track, top_k=3)
        finally:
            await server.close()

    asyncio.run(scenario())


async def confirmed(server, method, **params):
    pending = await server.dispatch(method, params)
    return await server.dispatch(
        method, {**params, "confirmed": True, "revision": pending["revision"]}
    )


def test_undo_clear_branch_preserve_files_and_invalidate_derived_state(tmp_path):
    async def scenario():
        make = factory(tmp_path)
        agent = seeded(make)
        parent = agent.session["id"]
        source = agent.session_store.inspect(parent)
        marker = tmp_path / "user-file.txt"
        marker.write_text("unchanged")
        server = TUIRPCServer(agent, send, session_factory=make)
        await server.start()
        try:
            child = await server.dispatch("session.branch", {"name": "Branch"})
            payload = agent.session_store.inspect(child["session_id"])
            assert payload["history"] == source["history"]
            assert payload["memory"]["working"]["task_summary"] == ""
            assert payload["checkpoints"]["items"] == {}
            assert agent.session_store.inspect(parent) == source
            stale = SessionStore(agent.session_store.root)
            stale_payload = stale.load(parent)
            result = await confirmed(server, "session.undo")
            assert result["removed"] == 1 and result["resubscribe"]
            assert server.agent is not agent
            assert server.agent.session["history"] == source["history"][:2]
            assert server.agent.current_checkpoint() is None
            assert server.agent.session["memory"]["working"]["task_summary"] == ""
            assert server.agent.memory_track_id not in (
                parent,
                payload["memory_track_id"],
            )
            with pytest.raises(StaleSessionWriteError):
                stale.save(stale_payload)
            old_track = server.agent.memory_track_id
            await confirmed(server, "session.clear")
            assert server.agent.session["history"] == []
            assert server.agent.memory_track_id != old_track
            assert marker.read_text() == "unchanged"
            turn = await server.dispatch(
                "turn.send", {"content": "new", "submission_id": "new"}
            )
            await server.host.wait(turn["turn_id"])
            assert server.agent.session["history"][0]["content"] == "new"
        finally:
            await server.close()

    asyncio.run(scenario())


def test_rewrite_rejects_stale_confirmation_and_invalid_turn_count(tmp_path):
    async def scenario():
        make = factory(tmp_path)
        server = TUIRPCServer(seeded(make), send, session_factory=make)
        await server.start()
        try:
            for value in (True, 0, -1, 1001, "1"):
                with pytest.raises(RpcError, match="integer"):
                    await server.dispatch("session.undo", {"n": value})
            pending = await server.dispatch("session.clear", {})
            await server.dispatch("session.title", {"title": "changed"})
            with pytest.raises(RpcError, match="changed"):
                await server.dispatch(
                    "session.clear",
                    {"confirmed": True, "revision": pending["revision"]},
                )
            assert len(server.agent.session["history"]) == 5
            await confirmed(server, "session.undo", n=1000)
            assert server.agent.session["history"] == []
            assert (await server.dispatch("session.undo", {}))["changed"] is False
            assert (await server.dispatch("session.branch", {}))["session_id"] is None
        finally:
            await server.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("after_replace", [False, True])
def test_rewrite_persistence_failure_is_fail_closed_when_disk_changed(
    tmp_path, monkeypatch, after_replace
):
    async def scenario():
        make = factory(tmp_path)
        agent = seeded(make)
        server = TUIRPCServer(agent, send, session_factory=make)
        await server.start()
        original = agent.session_store.rewrite

        def fail(*args, **kwargs):
            if after_replace:
                original(*args, **kwargs)
            raise OSError("write failed")

        monkeypatch.setattr(agent.session_store, "rewrite", fail)
        try:
            with pytest.raises(OSError):
                await confirmed(server, "session.clear")
            assert server._switch_failed is after_replace
            assert server.closed is after_replace
            assert len(agent.session["history"]) == 5
        finally:
            await server.close()

    asyncio.run(scenario())


def test_committed_rewrite_survives_factory_failure(tmp_path):
    async def scenario():
        make = factory(tmp_path)
        agent = seeded(make)

        def broken(session_id):
            raise RuntimeError("failed")

        server = TUIRPCServer(agent, send, session_factory=broken)
        await server.start()
        try:
            with pytest.raises(RpcError, match="committed"):
                await confirmed(server, "session.clear")
            assert server._switch_failed
            assert agent.session_store.inspect(agent.session["id"])["history"] == []
        finally:
            await server.close()

    asyncio.run(scenario())
