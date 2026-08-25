import asyncio
from dataclasses import FrozenInstanceError

import pytest

from repoagent import (
    FakeModelClient,
    InMemoryMemoryBackend,
    MemoryBackend,
    MemoryBackendNotStartedError,
    MemoryHit,
    RepoAgent,
    SessionStore,
    WorkspaceContext,
)
from repoagent.memory_contract import (
    MemoryBackendContractTests,
    MemoryBackendLifecycleContractTests,
)


class TestInMemoryMemoryBackendContract(MemoryBackendContractTests):
    def make_backend(self):
        return InMemoryMemoryBackend()


class TestInMemoryMemoryBackendLifecycle(MemoryBackendLifecycleContractTests):
    def make_backend(self):
        return InMemoryMemoryBackend()


def run(operation):
    return asyncio.run(operation)


def test_memory_hit_validates_standard_fields_and_is_frozen():
    hit = MemoryHit("Use pytest", score=1, metadata={"source": "test"})

    assert hit.score == 1.0
    assert hit.metadata == {"source": "test"}
    with pytest.raises(FrozenInstanceError):
        hit.text = "changed"
    with pytest.raises(ValueError):
        MemoryHit("bad", score=1.1)
    with pytest.raises(TypeError):
        MemoryHit("bad", metadata=[])


def test_runtime_checkable_memory_backend_rejects_incomplete_adapter():
    class IncompleteBackend:
        async def recall(self, query, *, user_id=None, agent_id=None, top_k=3):
            return []

    assert isinstance(InMemoryMemoryBackend(), MemoryBackend)
    assert not isinstance(IncompleteBackend(), MemoryBackend)


def test_in_memory_backend_requires_started_lifecycle():
    backend = InMemoryMemoryBackend()

    with pytest.raises(MemoryBackendNotStartedError):
        run(backend.recall("query", user_id="session", top_k=3))
    with pytest.raises(MemoryBackendNotStartedError):
        run(backend.store("session", []))


def test_in_memory_backend_recalls_ranked_hits_with_owner_provenance():
    async def scenario():
        backend = InMemoryMemoryBackend()
        await backend.start()
        await backend.store(
            "alice",
            [
                {"role": "user", "content": "Python project uses pytest"},
                {"role": "assistant", "content": "Run pytest for tests"},
                {"role": "user", "content": "Unrelated deployment note"},
            ],
        )

        hits = await backend.recall(
            "Python pytest", user_id="alice", top_k=2
        )
        missing = await backend.recall(
            "Python", user_id="bob", top_k=2
        )
        ambiguous = await backend.recall(
            "Python", user_id="alice", agent_id="agent", top_k=2
        )
        await backend.stop()
        return hits, missing, ambiguous

    hits, missing, ambiguous = run(scenario())

    assert [hit.text for hit in hits] == [
        "Python project uses pytest",
        "Run pytest for tests",
    ]
    assert hits[0].score == 1.0
    assert hits[0].metadata == {
        "session_id": "alice",
        "message_index": 0,
        "role": "user",
        "owner_track": "user",
    }
    assert missing == []
    assert ambiguous == []


def test_in_memory_backend_defensively_copies_messages_feedback_and_snapshots():
    async def scenario():
        backend = InMemoryMemoryBackend()
        await backend.start()
        message = {"role": "user", "content": "original", "meta": {"n": 1}}
        signal = {"kind": "used", "ids": ["a"]}
        await backend.store("session", [message])
        await backend.feedback(signal)
        message["meta"]["n"] = 2
        signal["ids"].append("b")
        first = await backend.snapshot()
        first["sessions"]["session"][0]["content"] = "mutated snapshot"
        second = await backend.snapshot()
        return first, second

    first, second = run(scenario())

    assert first["sessions"]["session"][0]["meta"] == {"n": 1}
    assert first["feedback"] == [{"kind": "used", "ids": ["a"]}]
    assert second["sessions"]["session"][0]["content"] == "original"


def test_in_memory_backend_serializes_concurrent_stores_without_loss():
    async def scenario():
        backend = InMemoryMemoryBackend()
        await backend.start()
        await asyncio.gather(
            *(
                backend.store(
                    "session",
                    [{"role": "user", "content": f"message-{index}"}],
                )
                for index in range(20)
            )
        )
        return await backend.snapshot()

    snapshot = run(scenario())

    messages = snapshot["sessions"]["session"]
    assert len(messages) == 20
    assert {message["content"] for message in messages} == {
        f"message-{index}" for index in range(20)
    }


def test_in_memory_backend_validates_store_and_recall_inputs():
    async def scenario():
        backend = InMemoryMemoryBackend()
        await backend.start()
        with pytest.raises(ValueError):
            await backend.store("", [])
        with pytest.raises(TypeError):
            await backend.store("session", {})
        with pytest.raises(TypeError):
            await backend.store("session", ["message"])
        with pytest.raises(TypeError):
            await backend.recall("q", user_id="session", top_k=True)
        assert await backend.recall("q", user_id="session", top_k=0) == []

    run(scenario())


def test_runtime_memory_backend_lifecycle_recall_store_and_evidence(tmp_path):
    class RecordingBackend:
        def __init__(self):
            self.started = 0
            self.stopped = 0
            self.queries = []
            self.stored = []

        async def start(self):
            self.started += 1

        async def stop(self):
            self.stopped += 1

        async def recall(self, query, *, user_id=None, agent_id=None, top_k):
            self.queries.append((query, user_id, agent_id, top_k))
            return [
                MemoryHit(
                    "deploy key is blue",
                    score=0.9,
                    metadata={"source": "remote", "kind": "fact"},
                )
            ]

        async def store(self, session_id, messages):
            self.stored.append((session_id, messages))

        async def feedback(self, signals):
            return None

    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    backend = RecordingBackend()
    client = FakeModelClient(["<final>It is blue.</final>"])
    agent = RepoAgent(
        model_client=client,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        memory_backend=backend,
        approval_policy="auto",
    )

    assert agent.ask("What color is the deploy key?") == "It is blue."

    assert backend.started == 1
    assert backend.stopped == 1
    assert backend.queries[0][3] == 3
    assert "deploy key is blue" in client.prompts[0]
    assert backend.stored[0][1] == [
        {"role": "user", "content": "What color is the deploy key?"},
        {"role": "assistant", "content": "It is blue."},
    ]
    assert agent.last_memory_backend_metadata["recall_status"] == "completed"
    assert agent.last_memory_backend_metadata["store_status"] == "completed"
    report = agent.build_report(agent.current_task_state)
    assert report["memory_backend"]["recalled_count"] == 1
    assert report["memory_backend"]["stored_message_count"] == 2


def test_runtime_redacts_backend_query_store_and_rejects_secret_hits(
    tmp_path, monkeypatch
):
    secret = "sk-runtime-secret"
    monkeypatch.setenv("TEST_API_KEY", secret)

    class SecretBackend:
        def __init__(self):
            self.query = ""
            self.messages = []

        async def start(self):
            pass

        async def stop(self):
            pass

        async def recall(self, query, *, user_id=None, agent_id=None, top_k):
            self.query = query
            return [MemoryHit(f"secret is {secret}", score=1.0)]

        async def store(self, session_id, messages):
            self.messages = messages

        async def feedback(self, signals):
            pass

    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    backend = SecretBackend()
    client = FakeModelClient(["<final>Done.</final>"])
    agent = RepoAgent(
        model_client=client,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        memory_backend=backend,
        approval_policy="auto",
    )

    assert agent.ask(f"Check {secret}") == "Done."

    assert secret not in backend.query
    assert secret not in str(backend.messages)
    assert f"secret is {secret}" not in client.prompts[0]
    assert agent.last_memory_backend_metadata["rejected_secret_hits"] == 1
