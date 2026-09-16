from copy import deepcopy

import pytest

from repoagent.issue_agent.feedback import diagnose


def failure():
    return {
        "case_id": "test",
        "status": "verification_failed",
        "runs": [{"phase": "fix", "status": "completed", "verification": {
            "baseline": {"reproduced": True},
            "candidate": {"reproduced": True, "passed": False,
                          "status": "completed", "exit_code": 1,
                          "output_truncated": False},
        }}],
    }


def test_known_failure_is_reviewable_not_automatically_evolved():
    state = failure()
    before = deepcopy(state)
    result = diagnose(state)
    assert result["category"] == "known_failure_persists"
    assert result["eligible_for_manual_training_review"]
    assert not result["automatic_evolution"]
    assert result["root_cause"] is None
    assert state == before


@pytest.mark.parametrize("key,value", [
    ("status", "timeout"), ("exit_code", 2), ("output_truncated", True),
    ("reproduced", False), ("passed", True),
])
def test_incomplete_or_conflicting_evidence_is_not_training_input(key, value):
    state = failure()
    state["runs"][0]["verification"]["candidate"][key] = value
    assert diagnose(state)["category"] == "unknown"
    assert not diagnose(state)["eligible_for_manual_training_review"]


def test_environment_failure_does_not_trigger_strategy_search():
    result = diagnose({"case_id": "test", "status": "environment_blocked"})
    assert result["category"] == "baseline_blocked"
    assert not result["eligible_for_manual_training_review"]


def test_budget_exhaustion_is_not_a_failed_patch():
    result = diagnose({"case_id": "test", "status": "budget_exhausted"})
    assert result["category"] == "budget_exhausted"
    assert not result["eligible_for_manual_training_review"]
