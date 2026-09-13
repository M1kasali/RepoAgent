from dataclasses import replace
import json
import os

import pytest

from repoagent.evolver import ScriptedAgentSnapshotEvaluator
from test_evolver_agent_snapshot_live import (
    _task,
    source_repository as source_repository,
)


def tool(name, **args):
    return "<tool>" + json.dumps({"name": name, "args": args}) + "</tool>"


def test_test_tool_is_explicit_and_part_of_task_identity():
    task = _task()
    assert "enable_tests" not in task.worker_input()
    enabled = replace(task, enable_tests=True)
    assert enabled.worker_input()["enable_tests"] is True
    assert enabled.check() != task.check()
    with pytest.raises(ValueError, match="enable_tests"):
        replace(task, enable_tests=1)


@pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"), reason="opt-in Docker tests"
)
@pytest.mark.parametrize("mode", ["repair", "failed", "stale"])
def test_snapshot_retains_real_test_results_and_freshness(source_repository, mode):
    root, proposal, evolver = source_repository
    identity = evolver.materialize_candidate(root, proposal)
    good = "def solve(): return 42\n"
    bad = "def solve(): return 0\n"
    visible = "import unittest\nfrom solution import solve\nclass Test(unittest.TestCase):\n    def test_answer(self): self.assertEqual(solve(),42)\n"
    run = tool("run_tests", start=".", pattern="test_visible.py", timeout=20)
    if mode == "repair":
        responses = (
            run,
            tool("write_file", path="solution.py", content=good),
            run,
            "<final>Done.</final>",
        )
    elif mode == "failed":
        responses = (run, "<final>Done.</final>")
    else:
        responses = (
            tool("write_file", path="solution.py", content=good),
            run,
            tool("write_file", path="solution.py", content=bad),
            "<final>Done.</final>",
        )
    task = _task(
        enable_tests=True,
        files={"solution.py": bad, "test_visible.py": visible},
        expected_files={"solution.py": good, "test_visible.py": visible},
        responses=responses,
    )
    backend = ScriptedAgentSnapshotEvaluator(
        [task], executable=os.environ["REPOAGENT_TEST_DOCKER"]
    )
    result = backend.run_trial(
        root, identity, task.check(), 0, backend.descriptor(root), cost_limit_usd=0
    )
    assert result["status"] == "completed", result
    assert result["passed"] is (mode == "repair")
    worker = result["raw"]["worker"]
    assert worker["tool_names_truncated"] is False
    reports = worker["test_verifications"]
    assert all(r["report"]["tests"] == 1 for r in reports)
    if mode == "repair":
        assert [r["verdict"] for r in reports] == ["failed", "passed"]
        assert [r["freshness"] for r in reports] == ["stale", "current"]
    elif mode == "failed":
        assert reports[0]["verdict"] == "failed"
    else:
        assert reports[0]["verdict"] == "passed"
        assert reports[0]["freshness"] == "stale"
    assert not (root / "solution.py").exists()
