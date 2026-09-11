from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

import pytest

from repoagent.evolver import (
    CandidateCheck,
    CandidateEvaluationError,
    ControlledEvolver,
    EvolutionLedger,
    EvolutionRunBudget,
    PairedPromotionGate,
)
from test_evolver_evaluation import FixtureEvaluator
from test_evolver_materialization import repository as _repository
from test_evolver_contracts import _proposal, _git
from repoagent.evolver.workspace import candidate_ref

repository = _repository


class TrialBackend(FixtureEvaluator):
    def __init__(self, *, cost=0.1, status="completed", explode=False):
        super().__init__()
        self.trials = []
        self.cost, self.status, self.explode = cost, status, explode

    def run_trial(
        self, root, identity, check, repetition, descriptor, *, cost_limit_usd
    ):
        self.trials.append((dict(identity), check, repetition, cost_limit_usd))
        if self.explode:
            raise RuntimeError("trial interrupted")
        passed = "candidate_id" in identity
        return {
            "status": self.status,
            "score": float(passed) if self.status == "completed" else None,
            "passed": passed if self.status == "completed" else None,
            "estimated_cost_usd": self.cost,
            "raw": {"output": "fixture"},
        }


def _prepare(repository, backend=None):
    root, proposal, evolver = repository
    backend = backend or TrialBackend()
    evolver.evaluate_candidate(
        root, proposal, [CandidateCheck("sanity", "true")], backend
    )
    return backend


def _run(repository, backend, **kwargs):
    root, proposal, evolver = repository
    options = dict(
        repetitions=1,
        gate=PairedPromotionGate(min_unique_tasks=1),
        run_budget=EvolutionRunBudget(max_pairs=4, max_estimated_cost_usd=2),
        max_trial_cost_usd=0.25,
    )
    options.update(kwargs)
    return evolver.evaluate_paired_checks(
        root, proposal, [CandidateCheck("quality", "test")], backend, **options
    )


def test_paired_execution_binds_commits_config_receipts_and_replays(repository):
    backend = _prepare(repository)
    decision = _run(repository, backend)
    assert decision.passed
    root, proposal, evolver = repository
    assert backend.trials[0][0]["commit_sha"] == proposal.manifest.base_commit
    assert backend.trials[1][0]["commit_sha"] != proposal.manifest.base_commit
    assert [item[3] for item in backend.trials] == [0.25, 0.25]
    restored = ControlledEvolver(EvolutionLedger(evolver.ledger.path))
    assert _run((root, proposal, restored), backend).to_dict() == decision.to_dict()
    assert len(backend.trials) == 2
    events = evolver.ledger.events()
    assert (
        len([event for event in events if event["event_type"] == "paired.reserved"])
        == 1
    )
    assert not any(
        event["event_type"].startswith(("approval.", "activation.")) for event in events
    )
    receipt = json.loads(
        next(
            (evolver.ledger.path.parent / "evidence").glob("*-paired-0.json")
        ).read_text()
    )
    assert receipt["result"]["raw"] == {"output": "fixture"}


def test_no_real_deterministic_receipt_means_no_paired_execution(repository):
    backend = TrialBackend()
    with pytest.raises(CandidateEvaluationError, match="deterministic evidence"):
        _run(repository, backend)
    assert not backend.trials


@pytest.mark.parametrize("mode", ["candidate", "run"])
def test_budget_admission_precedes_backend_execution(repository, mode):
    backend = _prepare(repository)
    kwargs = (
        {"repetitions": 51}
        if mode == "candidate"
        else {"run_budget": EvolutionRunBudget(max_pairs=1, max_estimated_cost_usd=0.1)}
    )
    with pytest.raises(CandidateEvaluationError, match="budget before execution"):
        _run(repository, backend, **kwargs)
    assert not backend.trials
    assert not any(
        event["event_type"] == "paired.reserved"
        for event in repository[2].ledger.events()
    )


@pytest.mark.parametrize("field", ["pairs", "cost"])
def test_cumulative_reservations_survive_restart_and_cover_other_candidates(
    repository, field
):
    root, proposal, evolver = repository
    backend = _prepare(repository)
    budget = EvolutionRunBudget(
        max_pairs=1 if field == "pairs" else 4,
        max_estimated_cost_usd=0.5 if field == "cost" else 2,
    )
    assert _run(repository, backend, run_budget=budget).passed
    other = _proposal(proposal.manifest.base_commit, content=b"another prompt\n")
    resumed = ControlledEvolver(EvolutionLedger(evolver.ledger.path))
    second = (root, other, resumed)
    _prepare(second, backend)
    with pytest.raises(CandidateEvaluationError, match="cumulative"):
        _run(second, backend, run_budget=budget)
    assert len(backend.trials) == 2


@pytest.mark.parametrize(
    "status", ["provider_error", "infrastructure_error", "inconclusive"]
)
def test_invalid_trial_stops_remaining_arms_and_replays_failed_gate(repository, status):
    backend = _prepare(repository, TrialBackend(status=status))
    first = _run(repository, backend)
    assert not first.passed
    assert first.metrics["reported_run_n"] == 1
    assert first.metrics["planned_run_n"] == 2
    assert _run(repository, backend).to_dict() == first.to_dict()
    assert len(backend.trials) == 1


@pytest.mark.parametrize("explode", [True, False])
def test_exception_or_unknown_cost_is_not_a_zero_score_or_free_trial(
    repository, explode
):
    backend = _prepare(repository, TrialBackend(explode=explode, cost=None))
    result = _run(repository, backend)
    assert not result.passed
    assert result.metrics["unpriced"]
    assert "win_tie_loss" not in result.metrics
    assert _run(repository, backend).to_dict() == result.to_dict()
    assert len(backend.trials) == 1


@pytest.mark.parametrize(
    "point", ["paired.trial_started", "paired.trial_completed", "gate"]
)
def test_interrupted_journal_never_repeats_completed_or_uncertain_trials(
    repository, monkeypatch, point
):
    backend = _prepare(repository)
    evolver = repository[2]
    original = evolver.ledger._append_protocol

    def append(event_type, **kwargs):
        payload = kwargs["payload"]
        if (
            event_type == point
            and (point != "paired.trial_started" or payload["index"] == 1)
            or point == "gate"
            and event_type == "gate.evaluated"
            and payload["stage"] == "paired"
        ):
            raise OSError("journal unavailable")
        return original(event_type, **kwargs)

    monkeypatch.setattr(evolver.ledger, "_append_protocol", append)
    with pytest.raises(OSError):
        _run(repository, backend)
    monkeypatch.setattr(evolver.ledger, "_append_protocol", original)
    if point == "paired.trial_completed":
        with pytest.raises(CandidateEvaluationError, match="interrupted"):
            _run(repository, backend)
        assert len(backend.trials) == 1
    else:
        assert _run(repository, backend).passed
        assert len(backend.trials) == 2


def test_killed_trial_is_not_automatically_retried(repository):
    class Killed(TrialBackend):
        def run_trial(self, *args, **kwargs):
            super().run_trial(*args, **kwargs)
            raise KeyboardInterrupt()

    backend = _prepare(repository, Killed())
    with pytest.raises(KeyboardInterrupt):
        _run(repository, backend)
    with pytest.raises(CandidateEvaluationError, match="interrupted"):
        _run(repository, backend)
    assert len(backend.trials) == 1


@pytest.mark.parametrize("kind", ["trial", "prerequisite"])
def test_changed_receipt_blocks_replay(repository, kind):
    backend = _prepare(repository)
    _run(repository, backend)
    pattern = "*-paired-0.json" if kind == "trial" else "*-deterministic.json"
    path = next((repository[2].ledger.path.parent / "evidence").glob(pattern))
    path.write_text("{}")
    with pytest.raises(CandidateEvaluationError, match="evidence changed/missing"):
        _run(repository, backend)
    assert len(backend.trials) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"repetitions": 2},
        {"fired_tasks": []},
        {"max_trial_cost_usd": 0.2},
        {"gate": PairedPromotionGate(min_unique_tasks=2)},
        {"run_budget": EvolutionRunBudget(max_pairs=99, max_estimated_cost_usd=2)},
    ],
)
def test_plan_and_budget_cannot_be_changed_on_resume(repository, change):
    backend = _prepare(repository)
    _run(repository, backend)
    with pytest.raises(CandidateEvaluationError, match="changed|frozen"):
        _run(repository, backend, **change)
    assert len(backend.trials) == 2


def test_exceeded_trial_reservation_latches_run_and_preserves_receipt(repository):
    backend = _prepare(repository, TrialBackend(cost=0.3))
    with pytest.raises(CandidateEvaluationError, match="exceeded reserved"):
        _run(repository, backend)
    assert len(backend.trials) == 1
    with pytest.raises(CandidateEvaluationError, match="exceeded reserved"):
        _run(repository, backend)
    root, proposal, evolver = repository
    other = _proposal(proposal.manifest.base_commit, content=b"other\n")
    _prepare((root, other, evolver), backend)
    with pytest.raises(CandidateEvaluationError, match="operator reconciliation"):
        _run((root, other, evolver), backend)
    assert len(backend.trials) == 1


def test_concurrent_coordinators_execute_each_arm_once(repository):
    backend = _prepare(repository)
    root, proposal, evolver = repository

    def run(_):
        return _run(
            (root, proposal, ControlledEvolver(EvolutionLedger(evolver.ledger.path))),
            backend,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        decisions = list(pool.map(run, range(3)))
    assert all(decision.passed for decision in decisions)
    assert len(backend.trials) == 2


def test_source_drift_during_trial_never_produces_paired_pass(repository):
    root, proposal, evolver = repository

    class Drift(TrialBackend):
        def run_trial(self, *args, **kwargs):
            result = super().run_trial(*args, **kwargs)
            _git(
                root,
                "update-ref",
                candidate_ref(proposal.manifest.candidate_id),
                proposal.manifest.base_commit,
            )
            return result

    backend = _prepare(repository, Drift())
    with pytest.raises(CandidateEvaluationError, match="reference changed"):
        _run(repository, backend)
    assert not any(
        event["event_type"] == "gate.evaluated"
        and event["payload"]["stage"] == "paired"
        for event in evolver.ledger.events()
    )


@pytest.mark.parametrize(
    "budget",
    [
        {"max_pairs": True},
        {"max_pairs": 0},
        {"max_estimated_cost_usd": -1},
        {"max_estimated_cost_usd": float("nan")},
        {"max_estimated_cost_usd": True},
    ],
)
def test_invalid_run_budgets(budget):
    with pytest.raises(ValueError):
        EvolutionRunBudget(**budget)


def test_invalid_candidate_budget_never_calls_descriptor(repository):
    root, proposal, evolver = repository
    proposal = replace(
        proposal,
        manifest=replace(
            proposal.manifest, budget=replace(proposal.manifest.budget, max_trials=1)
        ),
    )
    backend = TrialBackend()
    with pytest.raises(CandidateEvaluationError, match="candidate budget"):
        _run((root, proposal, evolver), backend, repetitions=2)
    assert not backend.trials
    assert not evolver.ledger.events()


def test_reservation_without_started_trial_resumes_without_double_reservation(
    repository, monkeypatch
):
    backend = _prepare(repository)
    evolver = repository[2]
    original = evolver.ledger._append_protocol

    def append(event_type, **kwargs):
        if event_type == "paired.trial_started":
            raise OSError("stop before any execution")
        return original(event_type, **kwargs)

    monkeypatch.setattr(evolver.ledger, "_append_protocol", append)
    with pytest.raises(OSError):
        _run(repository, backend)
    assert not backend.trials
    monkeypatch.setattr(evolver.ledger, "_append_protocol", original)
    assert _run(repository, backend).passed
    assert (
        len(
            [
                event
                for event in evolver.ledger.events()
                if event["event_type"] == "paired.reserved"
            ]
        )
        == 1
    )


def test_decimal_cost_admission_accepts_exact_boundary_across_candidates(repository):
    root, proposal, evolver = repository
    backend = _prepare(repository)
    options = dict(
        run_budget=EvolutionRunBudget(max_pairs=4, max_estimated_cost_usd=0.6),
        max_trial_cost_usd=0.15,
    )
    assert _run(repository, backend, **options).passed
    other = _proposal(proposal.manifest.base_commit, content=b"second\n")
    _prepare((root, other, evolver), backend)
    assert _run((root, other, evolver), backend, **options).passed
    assert len(backend.trials) == 4


def test_gate_cost_cap_is_an_execution_admission_limit(repository):
    backend = _prepare(repository)
    with pytest.raises(CandidateEvaluationError, match="gate cost budget"):
        _run(
            repository,
            backend,
            gate=PairedPromotionGate(min_unique_tasks=1, max_estimated_cost_usd=0.1),
        )
    assert not backend.trials


def test_changed_evaluator_descriptor_cannot_reuse_existing_receipts(
    repository, monkeypatch
):
    backend = _prepare(repository)
    _run(repository, backend)
    monkeypatch.setattr(backend, "descriptor", lambda root: {"kind": "different/v2"})
    with pytest.raises(CandidateEvaluationError, match="plan changed"):
        _run(repository, backend)
    assert len(backend.trials) == 2


def test_scores_without_raw_evidence_are_invalid(repository):
    class MissingEvidence(TrialBackend):
        def run_trial(self, *args, **kwargs):
            result = super().run_trial(*args, **kwargs)
            del result["raw"]
            return result

    backend = _prepare(repository, MissingEvidence())
    result = _run(repository, backend)
    assert not result.passed
    assert result.metrics["invalid"]
    assert len(backend.trials) == 1
