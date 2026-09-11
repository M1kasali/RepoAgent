import json

import pytest

from repoagent.evolver import CandidateEvaluationError, EvolutionRunBudget
from test_evolver_materialization import repository as repository
from test_evolver_paired_execution import _prepare, _run, TrialBackend
from test_evolver_contracts import _proposal


def test_verified_settlement_releases_unused_cost_once(repository):
    root, proposal, evolver = repository
    backend = _prepare(repository)
    budget = EvolutionRunBudget(max_pairs=4, max_estimated_cost_usd=0.7)
    _run(repository, backend, run_budget=budget)
    result = evolver.settle_paired_costs(proposal.manifest.candidate_id)
    assert result["actual_estimated_cost_usd"] == "0.2"
    assert result["released_cost_usd"] == "0.30"
    assert evolver.settle_paired_costs(proposal.manifest.candidate_id) == result
    other = _proposal(proposal.manifest.base_commit, content=b"next prompt\n")
    second = (root, other, evolver)
    _prepare(second, backend)
    assert _run(second, backend, run_budget=budget).passed
    assert (
        len([e for e in evolver.ledger.events() if e["event_type"] == "paired.settled"])
        == 1
    )


def test_unpriced_or_interrupted_trials_never_release_cost(repository):
    backend = _prepare(repository, TrialBackend(explode=True))
    _run(repository, backend)
    with pytest.raises(CandidateEvaluationError):
        repository[2].settle_paired_costs(repository[1].manifest.candidate_id)
    assert not any(
        e["event_type"] == "paired.settled" for e in repository[2].ledger.events()
    )


def test_changed_receipt_cannot_be_settled(repository):
    backend = _prepare(repository)
    _run(repository, backend)
    _, proposal, evolver = repository
    path = next((evolver.ledger.path.parent / "evidence").glob("*-paired-*.json"))
    receipt = json.loads(path.read_text())
    receipt["result"]["estimated_cost_usd"] = 0
    path.write_text(json.dumps(receipt))
    with pytest.raises(CandidateEvaluationError, match="changed"):
        evolver.settle_paired_costs(proposal.manifest.candidate_id)


@pytest.mark.parametrize("known", [True, False])
def test_operator_reconciliation_never_recovers_quality_or_retries(
    repository, tmp_path, known
):
    from repoagent.evolver.model_channel import ModelCallJournal
    from repoagent.evolver.evaluation import payload_digest

    class Interrupted(TrialBackend):
        def descriptor(self, root):
            return {
                "kind": "fixture-only",
                "model_mode": "host-budgeted",
                "model_gateway": {},
                "journal_root": str(tmp_path / "calls"),
                "tasks": [{"task_id": "quality"}],
            }

        def run_trial(
            self, root, identity, check, repetition, descriptor, *, cost_limit_usd
        ):
            (tmp_path / "calls").mkdir()
            self.journal = ModelCallJournal(
                tmp_path / "calls" / "trial", worker_root=root
            )
            self.journal(
                {
                    "status": "bound",
                    "source": identity,
                    "task": {"task_id": "quality"},
                    "repetition": repetition,
                    "outer_cost_limit_usd": cost_limit_usd,
                    "gateway_digest": payload_digest({}),
                }
            )
            self.journal(
                {
                    "status": "trial_finished",
                    "model_evidence": {
                        "cost_complete": known,
                        "known_estimated_cost_usd": 0.1,
                    },
                }
            )
            raise KeyboardInterrupt()

    backend = _prepare(repository, Interrupted())
    with pytest.raises(KeyboardInterrupt):
        _run(repository, backend)
    evolver, candidate = repository[2], repository[1].manifest.candidate_id
    if known:
        result = evolver.reconcile_trial_cost(
            candidate, 0, journal_path=backend.journal.path, actor="operator"
        )
        assert result["known_estimated_cost_usd"] == "0.1"
        assert result["quality_recovered"] is False
        assert result["released_cost_usd"] == "0"
    else:
        with pytest.raises(CandidateEvaluationError, match="unknown"):
            evolver.reconcile_trial_cost(
                candidate, 0, journal_path=backend.journal.path, actor="operator"
            )
    with pytest.raises(CandidateEvaluationError, match="interrupted"):
        _run(repository, backend)
    with pytest.raises(CandidateEvaluationError):
        evolver.settle_paired_costs(candidate)
