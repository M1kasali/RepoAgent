import subprocess

import pytest

from repoagent.issue_agent.task_source import materialize_task


@pytest.fixture
def source(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()
    git("init", "-q")
    (tmp_path / "main.py").write_text("value = 1\n")
    git("add", "main.py")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "fixture")
    repository = {"path": str(tmp_path), "base_revision": git("rev-parse", "HEAD"),
                  "tree": git("rev-parse", "HEAD^{tree}")}
    (tmp_path / "main.py").write_text("uncommitted repair must not leak\n")
    return repository


def spec(source):
    return {"task_id": "task", "base_revision": source["base_revision"],
            "source_paths": ["main.py"], "prompt": "Repair value.",
            "behavior_files": ["main.py"], "behavior_checks": [{
                "check_id": "value", "program": "print(1)", "expected_json": "1"}]}


def test_materialization_uses_commit_not_dirty_workspace(source):
    result = materialize_task(source, spec(source))
    assert result["files"] == {"main.py": "value = 1\n"}
    assert result["source_provenance"]["tree"] == source["tree"]


@pytest.mark.parametrize("path", ["../main.py", "/main.py", "./main.py", ".git/config"])
def test_source_traversal_rejected(source, path):
    specification = spec(source)
    specification["source_paths"] = [path]
    with pytest.raises(ValueError):
        materialize_task(source, specification)


def test_wrong_revision_or_injected_source_rejected(source):
    specification = spec(source)
    specification["files"] = {"main.py": "fixed"}
    with pytest.raises(ValueError):
        materialize_task(source, specification)
    specification = spec(source)
    specification["base_revision"] = "f" * 40
    with pytest.raises(ValueError):
        materialize_task(source, specification)
