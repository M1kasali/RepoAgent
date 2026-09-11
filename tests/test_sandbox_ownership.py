import json
import subprocess
from types import SimpleNamespace
import uuid

import pytest

from repoagent.sandbox import SandboxConfigurationError
from repoagent.sandbox_ownership import SandboxOwnership


@pytest.fixture
def owner(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = SandboxOwnership(workspace, root=tmp_path / "host-state")
    state = {"engine": "engine-a", "ids": [], "commands": [], "fail": False}

    def runner(argv, **kwargs):
        state["commands"].append(argv)
        if state["fail"]:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if argv[1] == "info":
            output = state["engine"]
        elif argv[1] == "container":
            output = "\n".join(state["ids"])
        else:
            state["ids"].remove(argv[-1])
            output = ""
        return subprocess.CompletedProcess(argv, 0, output, "")

    adapter = SimpleNamespace(
        executable="docker", workspace=workspace, _lifecycle_runner=runner,
        _docker_environment=lambda: {},
    )
    try:
        yield journal, adapter, state
    finally:
        journal.release()


def claim(journal, adapter):
    name = f"repoagent-session-{uuid.uuid4().hex}"
    return journal.claim(adapter, name)


def test_intent_is_persisted_before_create_and_active_owner_is_skipped(owner):
    journal, adapter, state = owner
    labels = claim(journal, adapter)
    path = next(journal.root.glob("*.json"))
    assert json.loads(path.read_text())["state"] == "intent"
    assert f"repoagent.scope={journal.scope}" in labels
    state["ids"] = ["a" * 64]
    assert journal.reconcile(adapter)[0]["status"] == "active"
    assert not any(command[1] == "rm" for command in state["commands"])


def test_unacknowledged_absence_remains_watchable_until_late_create(owner):
    journal, adapter, state = owner
    claim(journal, adapter)
    journal.cleanup_owned(adapter)
    assert journal.reconcile(adapter)[0]["status"] == "watching"
    assert json.loads(next(journal.root.glob("*.json")).read_text())["state"] == "intent"
    state["ids"] = ["a" * 64]
    assert journal.reconcile(adapter)[0]["status"] == "removed"
    assert not state["ids"]
    assert journal.reconcile(adapter)[0]["status"] == "closed"


def test_acknowledged_absence_closes_record(owner):
    journal, adapter, _ = owner
    claim(journal, adapter)
    journal.created()
    journal.release()
    assert journal.reconcile(adapter)[0]["status"] == "absent"
    assert journal.reconcile(adapter)[0]["status"] == "closed"


def test_engine_mismatch_never_removes_or_closes_record(owner):
    journal, adapter, state = owner
    claim(journal, adapter)
    journal.created()
    journal.release()
    state["engine"] = "engine-b"
    state["ids"] = ["a" * 64]
    assert journal.reconcile(adapter)[0]["status"] == "other_engine"
    assert state["ids"]
    assert journal.record["state"] == "created"


def test_removal_uses_exact_id_and_name_plus_both_ownership_labels(owner):
    journal, adapter, state = owner
    claim(journal, adapter)
    state["ids"] = ["a" * 64]
    journal.cleanup_owned(adapter)
    lookup = next(command for command in state["commands"] if command[1] == "container")
    assert f"name=^/{journal.record['name']}$" in lookup
    assert f"label=repoagent.owner={journal.record['token']}" in lookup
    assert f"label=repoagent.scope={journal.scope}" in lookup
    assert state["commands"][-1] == ["docker", "rm", "--force", "--volumes", "a" * 64]


@pytest.mark.parametrize("value", ["not-an-id", "--all", "abc"])
def test_malformed_daemon_result_cannot_be_used_for_removal(owner, value):
    journal, adapter, state = owner
    claim(journal, adapter)
    journal.release()
    state["ids"] = [value]
    assert journal.reconcile(adapter)[0]["status"] == "failed"
    assert not any(command[1] == "rm" for command in state["commands"])


def test_daemon_failure_preserves_record_and_releases_owner_lock(owner):
    journal, adapter, state = owner
    claim(journal, adapter)
    state["fail"] = True
    with pytest.raises(SandboxConfigurationError):
        journal.cleanup_owned(adapter)
    state["fail"] = False
    assert journal.reconcile(adapter)[0]["status"] == "watching"


@pytest.mark.parametrize("field,value", [
    ("name", "other-project"), ("scope", "other-workspace"),
    ("schema", "unknown"), ("token", "another-token"), ("state", "unknown"),
])
def test_invalid_record_is_not_a_deletion_instruction(owner, field, value):
    journal, adapter, state = owner
    claim(journal, adapter)
    journal.release()
    record = dict(journal.record, **{field: value})
    next(journal.root.glob("*.json")).write_text(json.dumps(record))
    assert journal.reconcile(adapter)[0]["status"] == "invalid"
    assert not any(command[1] == "rm" for command in state["commands"])


def test_journal_cannot_be_inside_guest_mount(tmp_path):
    with pytest.raises(SandboxConfigurationError, match="outside"):
        SandboxOwnership(tmp_path, root=tmp_path / ".state")


def test_journal_write_failure_prevents_claim(owner, monkeypatch):
    journal, adapter, _ = owner
    monkeypatch.setattr(journal, "_save", lambda record: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        claim(journal, adapter)
    assert journal._lease is None
    assert not list(journal.root.glob("*.json"))
