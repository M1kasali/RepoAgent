import pytest

from repoagent.evolver.contracts import CandidateBudget
from repoagent.evolver.gates import (
    DeterministicGatePipeline,
    PairedPromotionGate,
    TerminationTracker,
)

from test_evolver_contracts import _proposal


def test_deterministic_gate_recomputes_candidate_and_contains_check_errors(tmp_path):
    proposal = _proposal()
    target = tmp_path / "repoagent" / "prompt_prefix.py"
    target.parent.mkdir()
    target.write_bytes(proposal.content["repoagent/prompt_prefix.py"])

    passed = DeterministicGatePipeline(checks=[lambda *_: (True, "tests passed")]).run(
        proposal,
        tmp_path,
        observed_base_commit=proposal.manifest.base_commit,
    )
    assert passed.passed is True

    def broken(*_):
        raise RuntimeError("check crashed")

    failed = DeterministicGatePipeline(checks=[broken]).run(
        proposal,
        tmp_path,
        observed_base_commit="wrong",
    )
    assert failed.passed is False
    assert {item.status for item in failed.observations} == {"pass", "fail", "error"}


def _paired_rows(task_count=10, repetitions=2, delta=0.2):
    return [
        {
            "task_id": f"task-{task}",
            "repetition": repetition,
            "control_score": 0.5,
            "treatment_score": 0.5 + delta,
            "control_passed": True,
            "treatment_passed": True,
        }
        for task in range(task_count)
        for repetition in range(repetitions)
    ]


def test_paired_gate_requires_sample_lift_quality_and_budget():
    budget = CandidateBudget(max_trials=25, max_estimated_cost_usd=1.0)
    gate = PairedPromotionGate(
        min_unique_tasks=10,
        min_repetitions=2,
        min_mean_lift=0.1,
        require_ci_lower=0.1,
    )
    decision = gate.run(_paired_rows(), candidate_budget=budget, estimated_cost_usd=0.5)

    assert decision.passed is True
    assert decision.metrics["effective_n"] == 10
    assert decision.metrics["win_tie_loss"]["wins"] == 20
    assert decision.metrics["paired_bootstrap_95"]["low"] == pytest.approx(0.2)

    regressed = gate.run(
        _paired_rows(delta=-0.1),
        candidate_budget=budget,
        estimated_cost_usd=2.0,
    )
    assert regressed.passed is False
    failed_ids = {
        item.gate_id for item in regressed.observations if item.status == "fail"
    }
    assert {"cost_budget", "paired_lift", "bootstrap_lower_bound"} <= failed_ids


def test_paired_gate_rejects_duplicate_pair_identity():
    row = _paired_rows(task_count=1, repetitions=1)[0]
    with pytest.raises(ValueError, match="unique"):
        PairedPromotionGate(min_unique_tasks=1).run(
            [row, row], candidate_budget=CandidateBudget()
        )
    invalid = dict(row, control_passed="false")
    with pytest.raises(TypeError, match="must be bool"):
        PairedPromotionGate(min_unique_tasks=1).run(
            [invalid], candidate_budget=CandidateBudget()
        )


def test_termination_tracker_stops_on_patience_errors_or_round_budget():
    tracker = TerminationTracker(patience=2, max_rounds=5, max_consecutive_errors=2)
    tracker.record(promoted=False)
    assert tracker.decision() == (False, None)
    tracker.record(promoted=False)
    assert tracker.decision() == (True, "patience_exhausted")

    errors = TerminationTracker(patience=5, max_rounds=5, max_consecutive_errors=2)
    errors.record(promoted=False, errored=True)
    errors.record(promoted=False, errored=True)
    assert errors.decision() == (True, "errors_exhausted")

    rounds = TerminationTracker(patience=5, max_rounds=1, max_consecutive_errors=2)
    rounds.record(promoted=True)
    assert rounds.decision() == (True, "max_rounds")
