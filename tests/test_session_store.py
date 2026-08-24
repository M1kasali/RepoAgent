import json

import pytest

from repoagent.atomic_io import StorageCorruptionError
from repoagent.session_store import SessionStore, StaleSessionWriteError


def test_session_store_saves_loads_and_finds_latest_session(tmp_path):
    store = SessionStore(tmp_path / ".repoagent" / "sessions")
    first = {"id": "session_001", "history": [{"role": "user", "content": "first"}]}
    second = {"id": "session_002", "history": [{"role": "user", "content": "second"}]}

    first_path = store.save(first)
    second_path = store.save(second)

    assert first_path == store.path("session_001")
    assert json.loads(first_path.read_text(encoding="utf-8"))["id"] == "session_001"
    assert store.load("session_002") == second
    assert store.latest() == second_path.stem


def test_session_store_latest_is_none_when_empty(tmp_path):
    store = SessionStore(tmp_path / ".repoagent" / "sessions")

    assert store.latest() is None


def test_session_store_persists_version_and_revision_metadata(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = {"id": "session_001", "history": []}

    path = store.save(session)
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert persisted["_schema_version"] == 1
    assert persisted["_revision"] == 1
    assert store.load(session["id"]) == session


def test_session_store_loads_legacy_unversioned_session(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session = {"id": "legacy", "history": []}
    store.path("legacy").write_text(json.dumps(session), encoding="utf-8")

    assert store.load("legacy") == session


def test_session_store_rejects_unsupported_schema_on_load_and_save(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    path = store.path("future")
    path.write_text(
        json.dumps({"id": "future", "_schema_version": 99, "_revision": 1}),
        encoding="utf-8",
    )

    with pytest.raises(StorageCorruptionError, match="unsupported"):
        store.load("future")
    with pytest.raises(StorageCorruptionError, match="unsupported"):
        store.save({"id": "future", "history": []})


def test_session_store_rejects_stale_writer(tmp_path):
    root = tmp_path / "sessions"
    creator = SessionStore(root)
    session = {"id": "shared", "history": []}
    creator.save(session)

    first = SessionStore(root)
    second = SessionStore(root)
    first_value = first.load("shared")
    second_value = second.load("shared")
    first_value["history"].append({"role": "user", "content": "first"})
    first.save(first_value)
    second_value["history"].append({"role": "user", "content": "second"})

    with pytest.raises(StaleSessionWriteError, match="stale"):
        second.save(second_value)

    assert SessionStore(root).load("shared")["history"] == [
        {"role": "user", "content": "first"}
    ]
