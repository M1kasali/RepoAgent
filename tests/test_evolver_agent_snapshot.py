from dataclasses import replace
import os
import shutil

import pytest

from repoagent.evolver import (
    CandidateEvaluationError,
    ScriptedAgentSnapshotEvaluator,
)
from repoagent.evolver import agent_snapshot
from repoagent.evolver.agent_snapshot import _grade, _inventory
from repoagent.tool_execution import ProcessOutcome
from test_evolver_agent_snapshot_live import (
    source_repository as _source_repository,
    _task,
)

source_repository = _source_repository


@pytest.mark.parametrize(
    "changes",
    [
        {"task_id": "../escape"},
        {"prompt": " "},
        {"max_calls": True},
        {"max_calls": 21},
        {"max_output_tokens": 0},
        {"timeout_seconds": 301},
        {"responses": "<final>x</final>"},
        {"responses": ()},
        {"responses": (1,)},
        {"expected_files": {}},
        {"files": {"../escape": "x"}},
        {"files": {"a/.repoagent/x": "x"}},
        {"files": {"/absolute": "x"}},
        {"files": {"a": "x", "a/b": "y"}},
        {"files": {"a\\b": "x"}},
        {"files": {"nul\0": "x"}},
    ],
)
def test_task_rejects_unsafe_or_unbounded_contracts(changes):
    with pytest.raises(ValueError):
        _task(**changes)


def test_task_freezes_inputs_binds_grading_but_never_sends_answers():
    files = {"readme.txt": "input"}
    task = _task(files=files, expected_files={"result.txt": "hidden-answer"})
    original = task.check()
    files["readme.txt"] = "changed"
    assert task.files["readme.txt"] == "input"
    assert "hidden-answer" not in str(task.worker_input())
    assert replace(task, expected_files={"result.txt": "different"}).check() != original
    with pytest.raises(TypeError):
        task.files["new"] = "x"


def test_grader_checks_exact_bytes_and_rejects_links_and_special_files(tmp_path):
    (tmp_path / "good").write_text("answer")
    (tmp_path / "link").symlink_to(tmp_path / "good")
    (tmp_path / "dir-link").symlink_to(tmp_path, target_is_directory=True)
    os.mkfifo(tmp_path / "fifo")
    rows = _grade(
        tmp_path,
        {
            "good": "answer",
            "link": "answer",
            "dir-link/good": "answer",
            "missing": "",
            "fifo": "",
        },
    )
    assert [row["passed"] for row in rows] == [True, False, False, False, False]
    assert not _grade(tmp_path, {"good": "wrong"})[0]["passed"]


def test_inventory_rejects_symlinks_and_detects_permissions(tmp_path):
    (tmp_path / "file").write_text("source")
    before = _inventory(tmp_path)
    (tmp_path / "file").chmod(0o700)
    assert _inventory(tmp_path) != before
    (tmp_path / "link").symlink_to(tmp_path / "file")
    with pytest.raises(CandidateEvaluationError, match="link"):
        _inventory(tmp_path)


def test_descriptor_binds_driver_tasks_and_offline_dependency(monkeypatch):
    monkeypatch.setattr(
        agent_snapshot.DockerCandidateEvaluator,
        "descriptor",
        lambda *args: {"image_id": "fixed"},
    )
    backend = ScriptedAgentSnapshotEvaluator([_task()])
    desc = backend.descriptor("unused")
    assert desc["dependency"]["name"] == "json-repair"
    assert desc["dependency"]["files_digest"].startswith("sha256:")
    assert any("LICENSE" in name for name in backend._dependencies)
    assert backend.checks() == (_task().check(),)
    backend._driver += "\n"
    assert backend.descriptor("unused") != desc


@pytest.mark.parametrize(
    "target",
    [
        "harness/repoagent/prompt_prefix.py",
        "input.json",
        "dependencies/json_repair/__init__.py",
    ],
)
def test_post_execution_source_input_dependency_drift_is_rejected(
    source_repository, monkeypatch, target
):
    root, proposal, evolver = source_repository
    identity = evolver.materialize_candidate(root, proposal)
    task = _task()
    backend = ScriptedAgentSnapshotEvaluator([task])
    monkeypatch.setattr(backend, "descriptor", lambda root: {"image_id": "fixture"})
    directories = []

    class Modified:
        def __init__(self, directory, **kwargs):
            self.directory = directory
            directories.append(directory)

        def execute(self, *args, **kwargs):
            (self.directory / target).write_text("modified")
            return ProcessOutcome("completed", 0, "{}", "", 2, 0, False)

        def stop(self):
            pass

    monkeypatch.setattr(agent_snapshot, "PersistentDockerSandboxAdapter", Modified)
    with pytest.raises(CandidateEvaluationError, match="changed during execution"):
        backend.run_trial(
            root, identity, task.check(), 0, backend.descriptor(root), cost_limit_usd=0
        )
    assert all(not directory.exists() for directory in directories)


def test_failed_cleanup_keeps_directory_and_does_not_grade(
    source_repository, monkeypatch
):
    root, proposal, evolver = source_repository
    identity = evolver.materialize_candidate(root, proposal)
    task = _task()
    backend = ScriptedAgentSnapshotEvaluator([task])
    monkeypatch.setattr(backend, "descriptor", lambda root: {"image_id": "fixture"})
    instances = []

    class Broken:
        def __init__(self, directory, **kwargs):
            self.directory, self.stops = directory, 0
            instances.append(self)

        def execute(self, *args, **kwargs):
            return ProcessOutcome("completed", 0, "{}", "", 2, 0, False)

        def stop(self):
            self.stops += 1
            raise OSError("daemon unavailable")

    monkeypatch.setattr(agent_snapshot, "PersistentDockerSandboxAdapter", Broken)
    try:
        with pytest.raises(CandidateEvaluationError, match="retained directory"):
            backend.run_trial(
                root,
                identity,
                task.check(),
                0,
                backend.descriptor(root),
                cost_limit_usd=0,
            )
        assert instances[0].stops == 1
        assert instances[0].directory.exists()
    finally:
        for instance in instances:
            shutil.rmtree(instance.directory)


def test_changed_task_handle_is_rejected_before_materialization(monkeypatch):
    backend = ScriptedAgentSnapshotEvaluator([_task()])
    monkeypatch.setattr(backend, "descriptor", lambda root: {"image_id": "fixture"})
    with pytest.raises(CandidateEvaluationError, match="frozen plan"):
        backend.run_trial(
            "nonexistent",
            {},
            replace(_task().check(), command="unexpected"),
            0,
            {"image_id": "fixture"},
            cost_limit_usd=0,
        )
