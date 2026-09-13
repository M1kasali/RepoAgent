from dataclasses import replace
import os

import pytest

from repoagent.evolver import BehaviorCheck
from repoagent.evolver.behavior_grading import grade_behavior
from repoagent.tool_execution import ProcessOutcome
from test_evolver_agent_snapshot_live import _task, source_repository as source_repository


def test_behavior_contract_is_host_only_and_versioned():
    check = BehaviorCheck("probe", "print(42)", "42")
    task = _task(expected_files={}, behavior_files=("solution.py",), behavior_checks=(check,))
    assert "print(42)" not in str(task.worker_input())
    assert "expected_json" not in str(task.worker_input())
    changed = replace(task, behavior_checks=(replace(check, expected_json="43"),))
    assert changed.check() != task.check()
    assert replace(task, behavior_files=("other.py",)).check() != task.check()
    with pytest.raises(ValueError):
        replace(task, behavior_files=("../escape",))
    with pytest.raises(ValueError):
        replace(task, behavior_checks=(check, check))
    with pytest.raises(ValueError):
        replace(check, expected_json="NaN")


@pytest.mark.parametrize("status,code,stdout,truncated,valid,passed", [
    ("completed", 0, '{"b":2,"a":1}', False, True, True),
    ("completed", 0, '{"a":0}', False, True, False),
    ("completed", 0, 'not json', False, True, False),
    ("completed", 1, '', False, True, False),
    ("completed", 127, '', False, False, None),
    ("timed_out", None, '', False, False, None),
    ("completed", 0, '{}', True, False, None),
])
def test_behavior_grades_copied_outputs_without_host_answers(
    tmp_path, monkeypatch, status, code, stdout, truncated, valid, passed,
):
    (tmp_path / "solution.py").write_text("original")
    (tmp_path / "candidate_test.py").write_text("untrusted")
    stopped = []

    class Adapter:
        def __init__(self, root, **kwargs):
            self.root = root

        def execute(self, command, **kwargs):
            assert not (self.root / "candidate_test.py").exists()
            assert (self.root / "solution.py").read_text() == "original"
            assert '"a":1' not in command
            (self.root / "solution.py").write_text("mutated copy")
            return ProcessOutcome(status, code, stdout, "", len(stdout), 0, truncated)

        def stop(self):
            stopped.append(True)

    monkeypatch.setattr("repoagent.evolver.behavior_grading.PersistentDockerSandboxAdapter", Adapter)
    result = grade_behavior(tmp_path, ("solution.py",),
                            (BehaviorCheck("probe", "print('probe')", '{"a":1,"b":2}'),),
                            executable="docker", image="pinned")
    assert (result["status"] == "completed") is valid
    assert result["passed"] is passed
    assert stopped == [True]
    assert (tmp_path / "solution.py").read_text() == "original"


@pytest.mark.parametrize("kind", ["missing", "symlink", "directory", "oversized"])
def test_unsafe_artifacts_never_start_grader(tmp_path, monkeypatch, kind):
    path = tmp_path / "solution.py"
    if kind == "symlink":
        path.symlink_to(tmp_path / "elsewhere")
    elif kind == "directory":
        path.mkdir()
    elif kind == "oversized":
        path.write_bytes(b"x" * 1_000_001)
    monkeypatch.setattr("repoagent.evolver.behavior_grading.PersistentDockerSandboxAdapter",
                        lambda *args, **kwargs: pytest.fail("unsafe artifact reached Docker"))
    result = grade_behavior(tmp_path, ("solution.py",), (BehaviorCheck("probe", "print(1)", "1"),),
                            executable="docker", image="pinned")
    assert result["passed"] is False


@pytest.mark.skipif(not os.environ.get("REPOAGENT_TEST_DOCKER"), reason="explicit Docker integration")
@pytest.mark.parametrize("implementation,expected", [
    ("def add(a, b):\n    return a + b\n", True),
    ("def add(a, b):\n    return sum((a, b))\n", True),
    ("def add(a, b):\n    return a - b\n", False),
])
def test_real_behavior_grader_accepts_equivalent_code(tmp_path, implementation, expected):
    (tmp_path / "solution.py").write_text(implementation)
    result = grade_behavior(
        tmp_path, ("solution.py",), (BehaviorCheck(
            "addition", "import sys,json;sys.path.insert(0,'.');from solution import add;print(json.dumps([add(2,3),add(-2,5)]))",
            "[5,3]",
        ),), executable=os.environ["REPOAGENT_TEST_DOCKER"], image="python:3.12-slim",
    )
    assert result["status"] == "completed"
    assert result["passed"] is expected


@pytest.mark.skipif(not os.environ.get("REPOAGENT_TEST_DOCKER"), reason="explicit Docker integration")
def test_snapshot_runtime_reaches_behavior_grader(source_repository):
    from repoagent.evolver import ScriptedAgentSnapshotEvaluator

    root, proposal, evolver = source_repository
    identity = evolver.materialize_candidate(root, proposal)
    task = _task(
        expected_files={}, behavior_files=("solution.py",),
        responses=(
            '<tool name="write_file" path="solution.py"><content>def add(a,b):\n return sum((a,b))\n</content></tool>',
            '<final>Done.</final>',
        ),
        behavior_checks=(BehaviorCheck(
            "addition", "import sys,json;sys.path.insert(0,'.');from solution import add;print(json.dumps([add(2,3),add(-2,5)]))", "[5,3]",
        ),),
    )
    evaluator = ScriptedAgentSnapshotEvaluator([task], executable=os.environ["REPOAGENT_TEST_DOCKER"])
    result = evaluator.run_trial(root, identity, task.check(), 0, evaluator.descriptor(root), cost_limit_usd=0)
    assert result["status"] == "completed", result
    assert result["passed"] is True, result
    assert result["raw"]["behavior"]["passed"] is True
    assert not (root / "solution.py").exists()
