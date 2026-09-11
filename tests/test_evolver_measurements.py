from dataclasses import replace

import pytest

from repoagent.evolver import CandidateBudget, PairedMeasurement, PairedPromotionGate


def _measurements():
    return [
        PairedMeasurement(
            task,
            repetition,
            arm,
            "completed",
            "sha256:" + "a" * 64,
            score=0.8 if arm == "treatment" else 0.2,
            passed=arm == "treatment",
            estimated_cost_usd=0.1,
        )
        for task in ("a", "b")
        for repetition in range(2)
        for arm in ("control", "treatment")
    ]


def _run(rows=None, **kwargs):
    options = {
        "task_ids": ["a", "b"],
        "repetitions": 2,
        "candidate_budget": CandidateBudget(),
    }
    options.update(kwargs)
    return PairedPromotionGate(min_unique_tasks=1).run_measurements(
        _measurements() if rows is None else rows, **options
    )


def test_complete_measurements_pass_and_bind_raw_receipts_and_policy():
    rows = _measurements()
    result = _run(rows)
    assert result.passed
    assert result.metrics["planned_run_n"] == result.metrics["run_n"] == 8
    assert result.metrics["estimated_cost_usd"] == pytest.approx(0.8)
    assert _run(list(reversed(rows))).evidence_digest == result.evidence_digest
    rows[0] = replace(rows[0], receipt_digest="sha256:" + "b" * 64)
    assert _run(rows).evidence_digest != result.evidence_digest
    stricter = PairedPromotionGate(min_unique_tasks=2).run_measurements(
        _measurements(),
        task_ids=["a", "b"],
        repetitions=2,
        candidate_budget=CandidateBudget(),
    )
    assert stricter.passed
    assert stricter.evidence_digest != result.evidence_digest


@pytest.mark.parametrize(
    "status", ["provider_error", "infrastructure_error", "inconclusive"]
)
@pytest.mark.parametrize("arm", ["control", "treatment"])
def test_invalid_measurement_blocks_before_attribution_or_statistics(status, arm):
    rows = _measurements()
    index = next(
        i for i, row in enumerate(rows) if row.task_id == "b" and row.arm == arm
    )
    rows[index] = replace(rows[index], status=status, score=None, passed=None)
    result = _run(rows, fired_tasks={"a"})
    assert not result.passed
    assert result.metrics["attribution_applied"] is False
    assert len(result.metrics["invalid"]) == 1
    assert [item.gate_id for item in result.observations] == ["measurement_validity"]
    assert "win_tie_loss" not in result.metrics


@pytest.mark.parametrize("rows", [[], _measurements()[:-1]])
def test_missing_arms_are_not_silently_removed_from_denominator(rows):
    result = _run(rows)
    assert not result.passed
    assert result.metrics["planned_run_n"] == 8
    assert result.metrics["reported_run_n"] == len(rows)
    assert len(result.metrics["missing"]) == 8 - len(rows)


def test_unknown_cost_is_not_free_but_explicit_zero_is_valid():
    rows = _measurements()
    rows[0] = replace(rows[0], estimated_cost_usd=None)
    assert not _run(rows).passed
    rows[0] = replace(rows[0], estimated_cost_usd=0)
    assert _run(rows).passed


def test_normal_failed_test_is_valid_quality_evidence():
    result = _run([replace(row, score=0, passed=False) for row in _measurements()])
    assert not result.passed
    assert result.observations[0].status == "pass"
    assert result.metrics["win_tie_loss"]["ties"] == 4


def test_attribution_cannot_hide_full_matrix_trial_or_cost_budget():
    result = _run(
        fired_tasks={"a"},
        candidate_budget=CandidateBudget(max_trials=2, max_estimated_cost_usd=0.5),
    )
    assert not result.passed
    assert result.metrics["pair_n"] == 2
    assert result.metrics["planned_pair_n"] == 4
    assert result.metrics["estimated_cost_usd"] == pytest.approx(0.8)
    failed = {item.gate_id for item in result.observations if item.status == "fail"}
    assert {"measured_trial_budget", "cost_budget"} <= failed


def test_no_fired_tasks_rejects_without_statistics():
    result = _run(fired_tasks=set())
    assert not result.passed
    assert result.metrics["unfired_excluded"] == ["a", "b"]
    assert "win_tie_loss" not in result.metrics


@pytest.mark.parametrize(
    "change",
    [
        {"repetition": True},
        {"repetition": -1},
        {"repetition": 1.5},
        {"task_id": " "},
        {"arm": "candidate"},
        {"status": "skipped"},
        {"receipt_digest": "missing"},
        {"score": float("nan")},
        {"score": True},
        {"score": 1.1},
        {"passed": 1},
        {"estimated_cost_usd": True},
        {"estimated_cost_usd": -1},
        {"estimated_cost_usd": float("inf")},
        {"status": "provider_error"},
    ],
)
def test_measurement_contract_rejects_ambiguous_values(change):
    with pytest.raises(ValueError):
        replace(_measurements()[0], **change)


@pytest.mark.parametrize(
    "options",
    [
        {"task_ids": []},
        {"task_ids": ["a", "a"]},
        {"repetitions": True},
        {"repetitions": 0},
        {"fired_tasks": {"not-planned"}},
    ],
)
def test_invalid_plan_is_rejected(options):
    with pytest.raises(ValueError):
        _run(**options)


def test_duplicate_and_unplanned_measurements_are_rejected():
    rows = _measurements()
    with pytest.raises(ValueError, match="duplicate"):
        _run(rows + rows[:1])
    rows[0] = replace(rows[0], repetition=2)
    with pytest.raises(ValueError, match="unexpected"):
        _run(rows)


def test_attribution_is_explicit_and_omitting_it_keeps_all_tasks():
    attributed = _run(fired_tasks={"a"})
    assert attributed.passed
    assert attributed.metrics["eligible_tasks"] == ["a"]
    assert attributed.metrics["run_n"] == 4
    full = _run()
    assert full.metrics["eligible_tasks"] == ["a", "b"]
    assert not full.metrics["attribution_applied"]


def test_budget_changes_are_bound_to_evidence_digest():
    assert (
        _run().evidence_digest
        != _run(candidate_budget=CandidateBudget(max_trials=99)).evidence_digest
    )


def test_mixed_magnitude_costs_are_order_independent():
    costs = [1.0, 1e-16, 1e-16, 0, 0, 0, 0, 0]
    rows = [
        replace(row, estimated_cost_usd=cost)
        for row, cost in zip(_measurements(), costs)
    ]
    assert _run(rows).evidence_digest == _run(list(reversed(rows))).evidence_digest


def test_aggregate_cost_overflow_is_rejected():
    rows = [replace(row, estimated_cost_usd=1e308) for row in _measurements()]
    with pytest.raises(ValueError, match="aggregate"):
        _run(rows)
