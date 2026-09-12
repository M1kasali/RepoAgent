import asyncio
import json

import pytest

from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext
from repoagent.atomic_io import StorageCorruptionError
from repoagent.session_store import StaleSessionWriteError
from repoagent.tui_rpc import TUIRPCServer
from test_tui_rpc import request


def factory(root):
    def make(session_id=None):
        options = dict(
            model_client=FakeModelClient(["<final>done</final>"]),
            workspace=WorkspaceContext.build(root),
            session_store=SessionStore(root / ".repoagent" / "sessions"),
            approval_policy="never",
        )
        return (
            RepoAgent.from_session(session_id=session_id, **options)
            if session_id
            else RepoAgent(**options)
        )

    return make


@pytest.mark.parametrize(
    "name", ["../outside", "/tmp/outside", "a/b", "a\\b", "", "..", "a\n", "x" * 129]
)
def test_session_store_rejects_invalid_ids(tmp_path, name):
    with pytest.raises(ValueError):
        SessionStore(tmp_path).path(name)


def test_session_store_rejects_symlink_and_mismatched_payload(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    target = tmp_path / "outside.json"
    target.write_text('{"id":"link"}')
    (store.root / "link.json").symlink_to(target)
    with pytest.raises(ValueError):
        store.load("link")
    store.path("one").write_text('{"id":"other"}')
    with pytest.raises(StorageCorruptionError):
        store.load("one")


def test_readonly_inspection_does_not_advance_writer_revision(tmp_path):
    first, second = SessionStore(tmp_path), SessionStore(tmp_path)
    session = {"id": "one", "history": []}
    first.save(session)
    stale = second.load("one")
    first.save(session)
    assert second.inspect("one")["_revision"] == 2
    with pytest.raises(StaleSessionWriteError):
        second.save(stale)


def test_rpc_new_select_and_history_use_distinct_runtime_state(tmp_path):
    async def scenario():
        make = factory(tmp_path)
        old = make()
        old_id = old.session["id"]
        frames = []

        async def send(frame):
            frames.append(frame)

        server = TUIRPCServer(old, send, session_factory=make)
        await server.start()
        try:
            await server.receive(
                request("turn.send", {"content": "old marker", "submission_id": "one"})
            )
            turn = frames[-1]["result"]["turn_id"]
            await server.host.wait(turn)
            await server.receive(request("turn.subscribe", request_id=2))
            await server.receive(request("session.new", request_id=3))
            new_id = frames[-1]["result"]["session_id"]
            assert new_id != old_id
            assert frames[-1]["result"]["resubscribe"]
            assert (
                not server._subscriptions
                and not server._turns
                and not server._submissions
            )
            assert server.agent.session["history"] == []
            assert not old._memory_backend_started
            assert server.agent.memory is not old.memory
            await server.receive(
                request(
                    "turn.send", {"content": "new marker", "submission_id": "one"}, 4
                )
            )
            await server.host.wait(frames[-1]["result"]["turn_id"])
            await server.receive(request("session.select", {"session_id": old_id}, 5))
            assert frames[-1]["result"]["session_id"] == old_id
            await server.receive(request("session.history", {"limit": 1}, 6))
            assert frames[-1]["result"]["messages"] == [
                {"role": "user", "content": "old marker"}
            ]
            await server.receive(request("session.history", {"session_id": new_id}, 7))
            assert frames[-1]["result"]["messages"][0]["content"] == "new marker"
            await server.receive(request("session.list", {"limit": 1}, 8))
            assert frames[-1]["result"]["total"] == 2
            assert len(frames[-1]["result"]["sessions"]) == 1
        finally:
            await server.close()

    asyncio.run(scenario())


def test_switch_rejects_active_confirmation_without_interrupting_it(tmp_path):
    async def scenario():
        make = factory(tmp_path)
        agent = make()
        agent.model_client = FakeModelClient(
            [
                '<tool>{"name":"write_file","args":{"path":"no.txt","content":"no"}}</tool>',
                "<final>done</final>",
            ]
        )
        agent.approval_engine.mode = "ask"
        frames, pending = [], asyncio.Event()

        async def send(frame):
            frames.append(frame)
            if frame.get("method") == "confirm.request":
                pending.set()

        server = TUIRPCServer(agent, send, session_factory=make)
        await server.start()
        try:
            await server.receive(
                request("turn.send", {"content": "write", "submission_id": "one"})
            )
            await asyncio.wait_for(pending.wait(), 2)
            await server.receive(request("session.new", request_id=2))
            assert frames[-1]["error"]["code"] == -32003
            assert server.agent is agent and server.confirmations.pending()
        finally:
            await server.close()
        assert not (tmp_path / "no.txt").exists()

    asyncio.run(scenario())


def test_wrong_workspace_corrupt_and_traversal_are_not_exposed(tmp_path):
    async def scenario():
        agent = factory(tmp_path)()
        store = agent.session_store
        store.save(
            {"id": "foreign", "workspace_root": str(tmp_path / "other"), "history": []}
        )
        store.path("broken").write_text("{invalid")
        frames = []

        async def send(frame):
            frames.append(frame)

        server = TUIRPCServer(agent, send)
        await server.start()
        try:
            await server.receive(request("session.list"))
            assert frames[-1]["result"]["total"] == 1
            for name in ("foreign", "broken", "../secret"):
                await server.receive(request("session.history", {"session_id": name}))
                assert frames[-1]["error"]["code"] == -32602
        finally:
            await server.close()

    asyncio.run(scenario())


def test_failed_factory_sends_error_and_closes_connection(tmp_path):
    async def scenario():
        agent = factory(tmp_path)()
        frames = []

        async def send(frame):
            frames.append(frame)

        def broken(session_id):
            raise RuntimeError("private details")

        server = TUIRPCServer(agent, send, session_factory=broken)
        await server.start()
        try:
            await server.receive(request("session.new"))
            assert frames[-1]["error"]["code"] == -32004
            assert "private details" not in json.dumps(frames)
            assert server.closed and not agent._memory_backend_started
        finally:
            await server.close()

    asyncio.run(scenario())


def test_canonical_methods_and_redaction_before_truncation(tmp_path, monkeypatch):
    secret = "sk-" + "SECRET" * 20
    monkeypatch.setenv("TEST_API_KEY", secret)

    async def scenario():
        make = factory(tmp_path)
        agent = make()
        agent.session["history"] = [{"role": "user", "content": "x" * 15995 + secret}]
        agent.session_store.save(agent.session)
        frames = []

        async def send(frame):
            frames.append(frame)

        server = TUIRPCServer(agent, send, session_factory=make)
        await server.start()
        try:
            await server.receive(request("session.history"))
            assert secret[:5] not in frames[-1]["result"]["messages"][0]["content"]
            previous = server.session_id
            await server.receive(request("session.create"))
            assert frames[-1]["result"]["changed"]
            await server.receive(request("session.resume", {"session_id": previous}))
            assert server.session_id == previous
            await server.receive(request("session.resume", {"session_id": previous}))
            assert not frames[-1]["result"]["changed"]
        finally:
            await server.close()

    asyncio.run(scenario())
