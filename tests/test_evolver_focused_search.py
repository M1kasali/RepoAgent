from dataclasses import replace
import json

import pytest

from repoagent.evolver import (ControlledEvolver, EvolutionLedger, RepairTask, SearchLimits,
    FocusedFisherGate, TaskTrialSummary, EvaluationModelLimits, CandidateEvaluationError)
from test_evolver_benchmark_target import benchmark_repo, TARGET
from test_evolver_model_budget import LeafClient, _client


class Backend:
    def __init__(self):
        self.calls = []
        self.failure_requests = []
        self.version = 1
        self.invalid = False
        self.reject = False

    def descriptor(self, root):
        return {"kind": "scripted-scorer", "version": self.version}

    def score(self, root, identity, task_ids, k, phase):
        self.calls.append((identity["commit_sha"], tuple(task_ids), k, phase))
        if self.invalid:
            return {}
        level = 0 if phase == "cold_start" else 1 if phase.startswith("r0000") else 2
        if self.reject:
            level = 0
        return {tid: TaskTrialSummary(tid, k if tid == "a" and level >= 1 or tid == "b" and level >= 2 else 0, k)
                for tid in task_ids}

    def failure_cases(self, root, identity, task_ids, k, phase):
        self.failure_requests.append((identity["commit_sha"], tuple(task_ids), phase))
        return {tid: [{"fn": "solve", "args": [tid], "actual": 0, "expect": 1}] for tid in task_ids}


@pytest.fixture
def setup(benchmark_repo, tmp_path):
    root, target = benchmark_repo
    evolver = ControlledEvolver(EvolutionLedger(tmp_path / "ledger.jsonl"))
    backend = Backend()
    leaf = LeafClient(outputs=["```python\ndef solve(x): return 1\n```", "```python\ndef solve(x): return 2\n```"])
    client = _client(leaf, limits=EvaluationModelLimits(max_calls=9, max_input_tokens=100, max_output_tokens=20))
    options = dict(run_id="focused", target=target, backend=backend, client=client,
        tasks=[RepairTask("a", "a_why", "first case", "first rule"), RepairTask("b", "b_why", "second case", "second rule")],
        public_names=["solve"], limits=SearchLimits(max_rounds=3), gate=FocusedFisherGate(k=2))
    return root, evolver, options, backend, leaf


def run(setup, **changes):
    root, evolver, options, _, _ = setup
    return evolver.search_focused(root, **(options | changes))


def test_multi_round_promotes_parent_reuses_confirmation_and_stops_when_solved(setup):
    root, evolver, options, backend, leaf = setup
    state = run(setup)
    assert state["stop_reason"] == "no_training_failures"
    assert [row["why"] for row in state["rounds"]] == ["a_why", "b_why"]
    assert all(row["status"] == "qualified" for row in state["rounds"])
    assert [call[3] for call in backend.calls] == ["cold_start", "r0000_focused", "r0000_confirm", "r0001_focused", "r0001_confirm"]
    assert state["rounds"][1]["parent_commit"] == backend.calls[2][0]
    assert "return 1" in leaf.requests[1].messages[1].content
    assert backend.failure_requests[1][2] == "r0000_confirm"
    assert state["reserved_model_calls"] == 6
    assert state["reserved_scoring_trials"] == 16
    assert (root / TARGET).read_text() == "before\n"
    assert not any(e["event_type"].startswith(("sealed.", "activation.")) for e in evolver.ledger.events())


def test_finished_resume_never_calls_model_or_scorer(setup):
    state = run(setup)
    assert run(setup, resume=True) == state
    assert len(setup[3].calls) == 5 and len(setup[4].requests) == 2
    setup[3].version += 1
    with pytest.raises(CandidateEvaluationError, match="configuration"):
        run(setup, resume=True)


def test_rejection_keeps_parent_and_stops_patience(setup):
    setup[3].reject = True
    state = run(setup, limits=SearchLimits(max_rounds=5, patience=2))
    assert state["stop_reason"] == "patience_exhausted"
    assert state["parent_commit"] == setup[2]["target"].base_commit
    assert len(state["rounds"]) == 2
    assert all(row["why"] == "a_why" for row in state["rounds"])


def test_scoring_budget_stops_before_generation(setup):
    state = run(setup, max_scoring_trials=4)
    assert state["stop_reason"] == "scoring_budget_exhausted"
    assert len(setup[3].calls) == 1 and not setup[4].requests


def test_model_budget_reserves_parse_retries_before_generation(setup):
    setup[2]["client"] = _client(setup[4], limits=EvaluationModelLimits(max_calls=2, max_input_tokens=100, max_output_tokens=20))
    state = run(setup)
    assert state["stop_reason"] == "generation_budget_exhausted"
    assert not setup[4].requests


def test_candidate_trial_budget_is_not_overridden_by_run_budget(setup):
    from repoagent.evolver import CandidateBudget
    state = run(setup, candidate_budget=CandidateBudget(max_trials=1))
    assert state["stop_reason"] == "candidate_budget_exhausted"
    assert len(setup[3].calls) == 1 and not setup[4].requests


def test_invalid_cold_start_and_uncertain_resume_fail_closed(setup):
    setup[3].invalid = True
    with pytest.raises(CandidateEvaluationError, match="cold start"):
        run(setup)
    setup[3].invalid = False
    with pytest.raises(CandidateEvaluationError, match="uncertain"):
        run(setup, resume=True)
    assert not setup[4].requests and len(setup[3].calls) == 1


def test_checkpoint_resume_preserves_reservations_with_new_gateway(setup, monkeypatch):
    evolver = setup[1]
    append = evolver.ledger.append
    interrupted = False
    def stop(event, **kwargs):
        nonlocal interrupted
        result = append(event, **kwargs)
        if event == "focused_search.round_completed" and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()
        return result
    monkeypatch.setattr(evolver.ledger, "append", stop)
    with pytest.raises(KeyboardInterrupt):
        run(setup)
    monkeypatch.setattr(evolver.ledger, "append", append)
    # A new proxy has no in-memory reservations; durable run reservations remain.
    leaf = LeafClient(outputs=["```python\ndef solve(x): return 2\n```"])
    setup[2]["client"] = _client(leaf, limits=setup[2]["client"].limits)
    state = run(setup, resume=True)
    assert state["reserved_model_calls"] == 6
    assert state["reserved_scoring_trials"] == 16
    assert len(leaf.requests) == 1 and len(setup[3].calls) == 5


def test_uncertain_model_send_is_not_replayed(setup, monkeypatch):
    def interrupted(request):
        raise KeyboardInterrupt()
    monkeypatch.setattr(setup[4], "generate", interrupted)
    with pytest.raises(KeyboardInterrupt):
        run(setup)
    with pytest.raises(CandidateEvaluationError, match="uncertain"):
        run(setup, resume=True)


def test_failure_cases_cannot_smuggle_other_task_ids(setup, monkeypatch):
    monkeypatch.setattr(setup[3], "failure_cases", lambda *args: {"sealed-task": []})
    with pytest.raises(CandidateEvaluationError, match="nontraining"):
        run(setup)
    assert not setup[4].requests


def test_generation_exhaustion_is_bounded(setup):
    setup[4].outputs = ["malformed"]
    state = run(setup, limits=SearchLimits(max_rounds=5, max_generation_errors=2))
    assert state["stop_reason"] == "errors_exhausted"
    assert len(setup[4].requests) == 6
    assert len(setup[3].calls) == 1


def test_generation_cost_is_reserved_before_sending(setup):
    old = setup[2]["client"].limits
    setup[2]["client"] = _client(setup[4], limits=replace(old, max_estimated_cost_usd=.0002))
    state = run(setup)
    assert state["stop_reason"] == "generation_budget_exhausted"
    assert not setup[4].requests


def test_evaluation_interruption_cannot_be_silently_replayed(setup, monkeypatch):
    original = setup[3].score
    def stop(root, identity, task_ids, k, phase):
        if phase.endswith("focused"):
            raise KeyboardInterrupt()
        return original(root, identity, task_ids, k, phase)
    monkeypatch.setattr(setup[3], "score", stop)
    with pytest.raises(KeyboardInterrupt):
        run(setup)
    with pytest.raises(CandidateEvaluationError, match="uncertain"):
        run(setup, resume=True)
    assert len(setup[4].requests) == 1


def test_conflicting_why_metadata_fails_before_cold_start(setup):
    tasks = [RepairTask("a", "why", "first", "one"), RepairTask("b", "why", "second", "two")]
    with pytest.raises(ValueError, match="conflicting"):
        run(setup, tasks=tasks)
    assert not setup[3].calls


def test_sentinels_are_stratified_and_candidate_rotated():
    from repoagent.evolver.focused_search import select_sentinels
    baseline = {str(i): TaskTrialSummary(str(i), 2 if i < 10 else 1, 2) for i in range(20)}
    first = select_sentinels(baseline, "candidate_a", 6)
    second = select_sentinels(baseline, "candidate_b", 6)
    assert len(first) == 6 and len(set(first)) == 6
    assert sum(int(tid) < 10 for tid in first) == 3
    assert first == select_sentinels(baseline, "candidate_a", 6)
    assert first != second
