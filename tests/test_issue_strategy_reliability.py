import pytest

from repoagent.issue_agent.strategy_reliability import summarize_reliability


def row(arm, case_status="candidate_ready", **changes):
    result = {"status": "completed", "passed": case_status == "candidate_ready",
              "raw": {"case_status": case_status, "calls": 8}, "estimated_cost_usd": 0.02}
    if case_status in {"repair_incomplete", "budget_exhausted"}:
        result.update(status="inconclusive", passed=None)
    result.update(changes)
    return {"task_id": "task", "repetition": 0, "arm": arm, "result": result}


@pytest.mark.parametrize("failure", ["verification_failed", "budget_exhausted", "repair_incomplete"])
def test_bounded_completion_retains_unfinished_but_does_not_call_it_wrong_patch(failure):
    result = summarize_reliability([row("control", failure), row("treatment")],
        task_ids=["task"], repetitions=1, minimum_candidate_passes=1)
    assert result["eligible_for_heldout_pilot"]
    assert result["totals"]["control"]["verified_failure"] == int(failure == "verification_failed")
    assert result["totals"]["control"]["calls"] == 8
    assert not result["statistically_proven"] and not result["automatic_activation"]


@pytest.mark.parametrize("change", [{"status": "infrastructure_error"},
    {"estimated_cost_usd": None}, {"estimated_cost_usd": float("nan")}, {"passed": None}])
def test_invalid_or_missing_measurement_cannot_help_candidate_qualify(change):
    result = summarize_reliability([row("control", "verification_failed", **change), row("treatment")],
        task_ids=["task"], repetitions=1, minimum_candidate_passes=1)
    assert not result["comparison_valid"] and not result["eligible_for_heldout_pilot"]


def test_tie_missing_and_duplicate_trials_cannot_qualify():
    values = [row("control"), row("treatment")]
    kwargs = dict(task_ids=["task"], repetitions=1, minimum_candidate_passes=1)
    assert not summarize_reliability(values, **kwargs)["eligible_for_heldout_pilot"]
    assert not summarize_reliability(values[:1], **kwargs)["matrix_complete"]
    with pytest.raises(ValueError):
        summarize_reliability(values + values, **kwargs)


def test_one_task_regression_blocks_aggregate_gain():
    values = []
    for task, control, treatment in (("a", True, False), ("b", False, True), ("c", False, True)):
        for arm, passed in (("control", control), ("treatment", treatment)):
            value = row(arm, "candidate_ready" if passed else "verification_failed")
            value["task_id"] = task
            values.append(value)
    result = summarize_reliability(values, task_ids=["a", "b", "c"], repetitions=1, minimum_candidate_passes=2)
    assert result["accepted_count_difference"] == 1
    assert not result["eligible_for_heldout_pilot"]
