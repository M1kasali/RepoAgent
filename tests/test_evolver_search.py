import json

import pytest

from repoagent.evolver import CandidateCheck, EvolutionRunBudget, PairedPromotionGate
from repoagent.evolver.search import SearchLimits
from test_evolver_materialization import repository as repository
from test_evolver_contracts import _proposal, _git
from test_evolver_evaluation import FixtureEvaluator
from test_evolver_paired_execution import TrialBackend


def run(repository, **changes):
    root, original, evolver = repository
    options = dict(
        run_id="search-one",
        base_commit=original.manifest.base_commit,
        propose=lambda ctx: _proposal(
            ctx["base_commit"], content=f"prompt {ctx['round']}\n".encode()
        ),
        deterministic_checks=[CandidateCheck("sanity", "true")],
        deterministic_evaluator=FixtureEvaluator(),
        paired_checks=[CandidateCheck("quality", "test")],
        paired_evaluator=TrialBackend(),
        gate=PairedPromotionGate(min_unique_tasks=1),
        run_budget=EvolutionRunBudget(max_pairs=4, max_estimated_cost_usd=2),
        max_trial_cost_usd=0.25,
        limits=SearchLimits(max_rounds=2),
    )
    options.update(changes)
    return evolver.search(root, **options)


def test_search_runs_two_rounds_without_activation_and_never_replays(repository):
    root, original, evolver = repository
    backend = TrialBackend()
    result = run(repository, paired_evaluator=backend)
    assert result["status"] == "finished"
    assert result["stop_reason"] == "max_rounds"
    assert len(result["qualified_candidates"]) == 2
    assert len(backend.trials) == 4
    assert _git(root, "rev-parse", "HEAD") == original.manifest.base_commit
    assert not any(
        event["event_type"].startswith(("approval.", "activation."))
        for event in evolver.ledger.events()
    )
    with pytest.raises(FileExistsError):
        run(repository, paired_evaluator=backend)
    assert len(backend.trials) == 4


def test_deterministic_rejections_skip_models_and_stop_on_patience(repository):
    backend = TrialBackend()
    result = run(
        repository,
        deterministic_evaluator=FixtureEvaluator(fail=True),
        paired_evaluator=backend,
        limits=SearchLimits(max_rounds=5, patience=2),
    )
    assert result["stop_reason"] == "patience_exhausted"
    assert len(result["rounds"]) == 2
    assert not backend.trials


def test_generation_error_limit_and_detached_history(repository):
    def bad(ctx):
        ctx["history"].append({"tampered": True})
        raise ValueError("private details")

    result = run(repository, propose=bad, limits=SearchLimits(max_generation_errors=2))
    assert result["stop_reason"] == "errors_exhausted"
    assert "private details" not in json.dumps(result)
    assert "tampered" not in json.dumps(result)


def test_uncertain_trial_stops_search_and_records_blocked_state(repository):
    backend = TrialBackend(explode=True)
    with pytest.raises(Exception, match="requires review"):
        run(repository, paired_evaluator=backend)
    _, _, evolver = repository
    state = json.loads(
        (evolver.ledger.path.parent / "searches/search-one/state.json").read_text()
    )
    assert state["status"] == "blocked"
    assert len(state["rounds"]) == 1
    assert len(backend.trials) == 1


@pytest.mark.parametrize("value", [True, 0, -1, 1.2])
def test_strict_limits(value):
    with pytest.raises(ValueError):
        SearchLimits(max_rounds=value)


def test_explicit_finished_resume_makes_no_calls(repository):
    backend = TrialBackend()
    result = run(repository, paired_evaluator=backend)
    assert run(repository, paired_evaluator=backend, resume=True) == result
    assert len(backend.trials) == 4


def test_resume_completed_round_does_not_repeat_generation_or_model_calls(repository, monkeypatch):
    backend = TrialBackend()
    evolver = repository[2]
    append = evolver.ledger.append
    stopped = False
    def interrupt(kind, **kwargs):
        nonlocal stopped
        result = append(kind, **kwargs)
        if kind == "search.round_completed" and not stopped:
            stopped = True
            raise KeyboardInterrupt()
        return result
    monkeypatch.setattr(evolver.ledger, "append", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run(repository, paired_evaluator=backend)
    monkeypatch.setattr(evolver.ledger, "append", append)
    result = run(repository, paired_evaluator=backend, resume=True)
    assert len(result["qualified_candidates"]) == 2
    assert len(backend.trials) == 4


def test_resume_never_replays_uncertain_execution(repository):
    backend = TrialBackend(explode=True)
    with pytest.raises(Exception):
        run(repository, paired_evaluator=backend)
    with pytest.raises(Exception, match="uncertain round"):
        run(repository, paired_evaluator=backend, resume=True)
    assert len(backend.trials) == 1
