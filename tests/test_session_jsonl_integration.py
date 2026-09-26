"""JSONL storage through the actual RepoAgent SessionStore facade."""

import json
from copy import deepcopy

import pytest

from repoagent.atomic_io import StorageCorruptionError
from repoagent.session.atomic_io import read_epoch
from repoagent.session_store import SessionStore, StaleSessionWriteError


def test_normal_save_appends_metadata_and_new_messages_without_rewriting(tmp_path):
    store = SessionStore(tmp_path)
    session = {"id": "chat", "history": [{"role": "user", "content": "你好"}]}
    path = store.save(session)
    before = path.read_bytes()
    inode = path.stat().st_ino
    epoch = read_epoch(path)
    session["history"].append({"role": "assistant", "content": "hello"})
    store.save(session)
    assert path.suffix == ".jsonl"
    assert path.parent.name == "cli"
    assert path.read_bytes().startswith(before)
    assert path.stat().st_ino == inode
    assert read_epoch(path) == epoch
    tail = [json.loads(line) for line in path.read_bytes()[len(before) :].splitlines()]
    assert tail[0]["_type"] == "metadata"
    assert tail[1:] == session["history"][1:]
    assert SessionStore(tmp_path).load("chat") == session


def test_history_rewrite_increments_epoch_and_fences_loaded_writer(tmp_path):
    store, stale = SessionStore(tmp_path), SessionStore(tmp_path)
    value = {"id": "chat", "history": [{"role": "user", "content": "old"}]}
    path = store.save(value)
    old = stale.load("chat")
    epoch = read_epoch(path)
    store.rewrite("chat", expected_revision=1, transform=lambda s: {**s, "history": []})
    assert read_epoch(path) == epoch + 1
    old["history"].append({"role": "assistant", "content": "late"})
    with pytest.raises(StaleSessionWriteError):
        stale.save(old)
    assert store.inspect("chat")["history"] == []


def test_legacy_migration_preserves_payload_backup_and_fences_old_format(tmp_path):
    payload = {
        "id": "legacy",
        "history": [{"role": "user", "content": "旧会话"}],
        "memory": {"facts": ["abc"]},
        "checkpoints": {"current_id": "a"},
        "_schema_version": 1,
        "_revision": 7,
    }
    legacy = tmp_path / "legacy.json"
    original = json.dumps(payload).encode()
    legacy.write_bytes(original)
    store = SessionStore(tmp_path)
    loaded = store.load("legacy")
    assert loaded == {k: v for k, v in payload.items() if not k.startswith("_")}
    assert legacy.read_bytes() == original
    assert (tmp_path / ".deleted" / legacy.name).is_file()
    assert store.inspect("legacy") == payload
    store.save(loaded)
    assert SessionStore(tmp_path).inspect("legacy")["_revision"] == 8
    assert store.ids() == ["legacy"]
    store.delete("legacy", expected_revision=8)
    assert store.ids() == []
    with pytest.raises(ValueError, match="deleted"):
        SessionStore(tmp_path).load("legacy")
    assert legacy.read_bytes() == original


def test_partial_tail_recovered_by_pico_rewrite(tmp_path):
    store = SessionStore(tmp_path)
    path = store.save({"id": "chat", "history": [{"role": "user", "content": "saved"}]})
    with path.open("ab") as stream:
        stream.write(b'{"role":"assistant","content":"interrupted')
    reopened = SessionStore(tmp_path)
    payload = reopened.load("chat")
    epoch = read_epoch(path)
    assert len(payload["history"]) == 1
    reopened.save(payload)
    assert read_epoch(path) == epoch + 1
    assert b"interrupted" not in path.read_bytes()


def test_complete_corrupt_record_is_not_silently_dropped(tmp_path):
    store = SessionStore(tmp_path)
    path = store.save({"id": "chat", "history": []})
    with path.open("ab") as stream:
        stream.write(b"{broken}\n")
    before = path.read_bytes()
    with pytest.raises(StorageCorruptionError):
        SessionStore(tmp_path).load("chat")
    assert path.read_bytes() == before


def test_metadata_and_tool_call_fields_survive_roundtrip(tmp_path):
    store = SessionStore(tmp_path)
    payload = {
        "id": "chat",
        "history": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path":"a"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "name": "read_file",
                "content": "hi",
            },
        ],
        "memory_track_id": "track",
        "runtime_identity": {"x": 1},
    }
    expected = deepcopy(payload)
    store.save(payload)
    assert SessionStore(tmp_path).load("chat") == expected


def test_legacy_inspection_does_not_migrate_or_change_revision(tmp_path):
    old = tmp_path / "old.json"
    payload = {"id": "old", "history": [], "_schema_version": 1, "_revision": 3}
    old.write_text(json.dumps(payload))
    store = SessionStore(tmp_path)
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    assert store.inspect("old") == payload
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before
    assert not store.path("old").exists()
