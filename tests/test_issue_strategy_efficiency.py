from copy import deepcopy

import pytest

from repoagent.issue_agent.strategy_efficiency import summarize_efficiency


def rows():
    return [{"task_id": "a", "repetition": 0, "arm": arm,
             "result": {"status": "completed", "passed": True,
                        "estimated_cost_usd": cost, "raw": {"calls": calls}}}
            for arm, calls, cost in (("control", 10, 0.1), ("treatment", 8, 0.09))]


def test_lower_calls_with_full_acceptance_is_exploratory_not_activation():
    result = summarize_efficiency(rows(), task_ids=["a"], repetitions=1)
    assert result["eligible_for_heldout_pilot"]
    assert result["reductions"]["calls"] == pytest.approx(0.2)
    assert not result["statistically_proven"] and not result["automatic_activation"]


@pytest.mark.parametrize("change", [{"passed": False}, {"status": "inconclusive"},
                                   {"estimated_cost_usd": None}, {"raw": {"calls": True}},
                                   {"estimated_cost_usd": float("nan")}])
def test_bad_quality_or_measurement_cannot_be_presented_as_savings(change):
    values = deepcopy(rows())
    values[1]["result"].update(change)
    result = summarize_efficiency(values, task_ids=["a"], repetitions=1)
    assert not result["eligible_for_heldout_pilot"]
    assert result["reductions"]["calls"] is None


def test_missing_failure_not_silently_dropped_and_duplicates_rejected():
    assert not summarize_efficiency(rows()[:1], task_ids=["a"], repetitions=1)["measurement_complete"]
    with pytest.raises(ValueError):
        summarize_efficiency(rows() + rows(), task_ids=["a"], repetitions=1)


def test_cost_saving_alone_does_not_switch_primary_metric():
    values = rows()
    values[1]["result"]["raw"]["calls"] = 11
    result = summarize_efficiency(values, task_ids=["a"], repetitions=1)
    assert not result["eligible_for_heldout_pilot"]


def test_inconclusive_run_keeps_known_calls_and_cost_without_qualifying():
    values = rows()
    values[0]["result"].update(status="inconclusive", passed=None)
    result = summarize_efficiency(values, task_ids=["a"], repetitions=1)
    assert not result["eligible_for_heldout_pilot"]
    assert result["totals"]["control"]["calls"] == 10
    assert result["totals"]["control"]["estimated_cost_usd"] == 0.1
    assert result["totals"]["control"]["passes"] == 0


def test_exact_call_reduction_threshold_qualifies():
    values = rows()
    values[1]["result"]["raw"]["calls"] = 9
    assert summarize_efficiency(values, task_ids=["a"], repetitions=1)["eligible_for_heldout_pilot"]


@pytest.mark.parametrize("rep", [False, 0.0])
def test_repetition_requires_integer(rep):
    values = rows()
    values[0]["repetition"] = rep
    with pytest.raises(ValueError):
        summarize_efficiency(values, task_ids=["a"], repetitions=1)
