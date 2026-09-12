import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from repoagent.memory_backend import MemoryBackendNotStartedError
from repoagent.memory_contract import (
    MemoryBackendContractTests,
    MemoryBackendLifecycleContractTests,
)
from repoagent.memory_plugins import MemoryPluginError, load_memory_backend
from repoagent.sqlite_memory import SQLiteMemoryBackend
from test_tool_gateway import build_agent


class TestSQLiteContract(
    MemoryBackendContractTests, MemoryBackendLifecycleContractTests
):
    @pytest.fixture(autouse=True)
    def configure(self, tmp_path):
        self.root = tmp_path

    def make_backend(self):
        return SQLiteMemoryBackend(self.root / "memory.db", workspace=self.root)


def backend(root, **kwargs):
    return SQLiteMemoryBackend(root / "memory.db", workspace=root, **kwargs)


def test_restart_recall_and_owner_isolation(tmp_path):
    async def scenario():
        first = backend(tmp_path)
        await first.start()
        await first.store(
            "alice",
            [
                {
                    "role": "user",
                    "content": "Deploy to aurora",
                    "metadata": {"session_id": "old-session", "turn_id": "old-turn"},
                }
            ],
        )
        await first.store("bob", [{"role": "user", "content": "Deploy to borealis"}])
        await first.stop()
        second = backend(tmp_path)
        await second.start()
        hits = await second.recall("deploy", user_id="alice", top_k=3)
        assert [hit.text for hit in hits] == ["Deploy to aurora"]
        assert hits[0].metadata["session_id"] == "old-session"
        assert await second.recall("aurora", user_id="bob", top_k=3) == []
        assert (
            await second.recall("deploy", user_id="alice", agent_id="bob", top_k=3)
            == []
        )
        assert await second.recall("", user_id="alice", top_k=3) == []
        await second.stop()

    asyncio.run(scenario())


def test_same_database_isolates_repositories(tmp_path):
    async def scenario():
        first = backend(tmp_path)
        second = SQLiteMemoryBackend(first.path, workspace=tmp_path / "other")
        await first.start()
        await second.start()
        await first.store(
            "owner", [{"role": "user", "content": "private repository fact"}]
        )
        assert await second.recall("private", user_id="owner", top_k=3) == []
        assert await second.forget("owner") == 0
        assert len(await first.recall("private", user_id="owner", top_k=3)) == 1
        await first.stop()
        await second.stop()

    asyncio.run(scenario())


def test_chinese_retrieval_and_literal_query(tmp_path):
    async def scenario():
        store = backend(tmp_path)
        await store.start()
        await store.store("user", [{"role": "user", "content": "项目部署区域是杭州"}])
        hits = await store.recall('部署区域 " OR * :', user_id="user", top_k=3)
        assert hits and "杭州" in hits[0].text
        assert await store.recall("部署区域", user_id="other", top_k=3) == []
        await store.stop()

    asyncio.run(scenario())


def test_deduplication_retention_forget_and_index_cleanup(tmp_path):
    async def scenario():
        store = backend(tmp_path, max_records=2)
        await store.start()
        for text in ("item oldest", "item newest", "item newest", "item latest"):
            await store.store("owner", [{"role": "user", "content": text}])
        assert len(await store.recall("item", user_id="owner", top_k=10)) == 2
        assert await store.recall("oldest", user_id="owner", top_k=3) == []
        assert await store.forget("owner") == 2
        assert await store.recall("item", user_id="owner", top_k=10) == []
        await store.stop()
        with sqlite3.connect(store.path) as db:
            assert db.execute("SELECT count(*) FROM memory_search").fetchone()[0] == 0

    asyncio.run(scenario())


def test_multiple_instances_write_without_loss(tmp_path):
    async def scenario():
        stores = [backend(tmp_path) for _ in range(3)]
        await asyncio.gather(*(s.start() for s in stores))
        await asyncio.gather(
            *(
                stores[i % 3].store(
                    "owner", [{"role": "user", "content": f"message unique_{i}"}]
                )
                for i in range(20)
            )
        )
        assert len(await stores[0].recall("message", user_id="owner", top_k=50)) == 20
        await asyncio.gather(*(s.stop() for s in stores))

    asyncio.run(scenario())


def test_lifecycle_validation_and_atomic_batch(tmp_path):
    async def scenario():
        store = backend(tmp_path)
        with pytest.raises(MemoryBackendNotStartedError):
            await store.recall("q", user_id="owner", top_k=3)
        await store.start()
        with pytest.raises(ValueError):
            await store.store(
                "owner",
                [
                    {"role": "user", "content": "not committed"},
                    {"role": "tool", "content": "output"},
                ],
            )
        assert await store.recall("committed", user_id="owner", top_k=3) == []
        with pytest.raises(TypeError):
            await store.recall("q", user_id="owner", top_k=True)
        await store.feedback({"kind": "audit-only"})
        await store.stop()
        with pytest.raises(MemoryBackendNotStartedError):
            await store.store("owner", [])

    asyncio.run(scenario())


def test_foreign_database_is_not_modified(tmp_path):
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated(value TEXT)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="refusing migration"):
        asyncio.run(backend(tmp_path).start())
    assert path.read_bytes() == before


def test_cli_loader_does_not_import_external_plugins(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "repoagent.memory_plugins.metadata.entry_points",
        lambda **kwargs: pytest.fail("built-in backend must not discover plugins"),
    )
    store = load_memory_backend("sqlite", workspace=tmp_path)
    assert isinstance(store, SQLiteMemoryBackend)
    assert store.path == tmp_path / ".repoagent" / "memory.sqlite3"
    config = tmp_path / "config.json"
    config.write_text('{"unknown": true}')
    with pytest.raises(MemoryPluginError, match="unknown"):
        load_memory_backend("sqlite", workspace=tmp_path, config_path=config)


def test_new_session_recalls_sqlite_into_prompt(tmp_path):
    first = build_agent(
        tmp_path,
        ["<final>Noted.</final>"],
        memory_backend=backend(tmp_path),
        memory_track_id="developer",
    )
    first.ask("Our staging cluster is aurora stagingcluster")
    second = build_agent(
        tmp_path,
        ["<final>Aurora.</final>"],
        memory_backend=backend(tmp_path),
        memory_track_id="developer",
    )
    assert first.session["id"] != second.session["id"]
    second.ask("Which stagingcluster?")
    assert (
        "Our staging cluster is aurora stagingcluster" in second.model_client.prompts[0]
    )
    assert second.last_memory_backend_metadata["recalled_count"] >= 1
    assert second.last_memory_backend_metadata["store_status"] == "completed"
    third = build_agent(
        tmp_path,
        ["<final>Unknown.</final>"],
        memory_backend=backend(tmp_path),
        memory_track_id="other",
    )
    third.ask("Which stagingcluster?")
    assert (
        "Our staging cluster is aurora stagingcluster"
        not in third.model_client.prompts[0]
    )
    assert third.last_memory_backend_metadata["recalled_count"] == 0


def test_process_restart_persists_actual_database(tmp_path):
    script = """
import asyncio, json, sys
from pathlib import Path
from repoagent.sqlite_memory import SQLiteMemoryBackend
async def main():
    root = Path(sys.argv[1])
    store = SQLiteMemoryBackend(root / "memory.db", workspace=root)
    await store.start()
    if sys.argv[2] == "store":
        await store.store("owner", [{"role":"user", "content":"persistent recall marker"}])
    else:
        print(json.dumps([h.text for h in await store.recall("marker", user_id="owner", top_k=3)]))
    await store.stop()
asyncio.run(main())
"""
    for mode in ("store", "recall"):
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), mode],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
    assert json.loads(result.stdout) == ["persistent recall marker"]


def test_long_messages_are_chunked_without_discarding_tail(tmp_path):
    async def scenario():
        store = backend(tmp_path)
        await store.start()
        await store.store(
            "owner", [{"role": "user", "content": "padding " * 2000 + "tailmarker"}]
        )
        hits = await store.recall("tailmarker", user_id="owner", top_k=3)
        assert len(hits) == 1 and "tailmarker" in hits[0].text
        assert len(hits[0].text) <= 2000
        await store.stop()

    asyncio.run(scenario())


def test_runtime_redacts_before_persistence_and_preserves_track(tmp_path, monkeypatch):
    from repoagent import FakeModelClient, RepoAgent

    monkeypatch.setenv("TEST_API_KEY", "sk-private-memory-marker")
    agent = build_agent(
        tmp_path,
        ["<final>Done.</final>"],
        memory_backend=backend(tmp_path),
        memory_track_id="personal",
    )
    agent.ask("Please inspect sk-private-memory-marker")
    with sqlite3.connect(tmp_path / "memory.db") as db:
        stored = db.execute("SELECT text FROM memories").fetchall()
    assert "sk-private-memory-marker" not in str(stored)
    resumed = RepoAgent.from_session(
        model_client=FakeModelClient([]),
        workspace=agent.workspace,
        session_store=agent.session_store,
        session_id=agent.session["id"],
        memory_backend=backend(tmp_path),
        checkpoint_policy="never",
    )
    assert resumed.memory_track_id == "personal"
    with pytest.raises(ValueError, match="cannot change"):
        RepoAgent.from_session(
            model_client=FakeModelClient([]),
            workspace=agent.workspace,
            session_store=agent.session_store,
            session_id=agent.session["id"],
            memory_backend=backend(tmp_path),
            memory_track_id="other",
            checkpoint_policy="never",
        )


def test_cli_assembly_selects_builtin_and_explicit_track(tmp_path):
    from repoagent import FakeModelClient
    from repoagent.cli import build_arg_parser
    from repoagent.providers.profiles import ModelProfile
    from repoagent.runtime_assembly import RuntimeAssembly

    args = build_arg_parser().parse_args(
        [
            "--cwd",
            str(tmp_path),
            "--memory-backend",
            "sqlite",
            "--memory-track",
            "personal",
            "--checkpoint-policy",
            "never",
        ]
    )
    client = FakeModelClient(["<final>Noted.</final>"])
    client.profile = ModelProfile(
        name="offline",
        provider="offline",
        protocol="openai",
        model="offline",
        base_url="http://127.0.0.1:9",
        max_output_tokens=64,
        context_window_tokens=4096,
        context_window_source="test",
    )
    agent = RuntimeAssembly.from_arguments(
        args,
        model_client_factory=lambda args: client,
        secret_names_factory=lambda args: (),
    ).build()
    assert isinstance(agent.memory_backend, SQLiteMemoryBackend)
    assert agent.memory_track_id == "personal"
    agent.ask("The deployment region is aurora.")
    assert agent.last_memory_backend_metadata["store_status"] == "completed"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file mode check")
def test_new_database_has_private_permissions(tmp_path):
    store = backend(tmp_path)
    asyncio.run(store.start())
    asyncio.run(store.stop())
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_future_schema_is_rejected_without_migration(tmp_path):
    store = backend(tmp_path)
    asyncio.run(store.start())
    asyncio.run(store.stop())
    with sqlite3.connect(store.path) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="refusing migration"):
        asyncio.run(backend(tmp_path).start())
