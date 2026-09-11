import pytest

from repoagent.evolver import SnapshotDeployment, HostedAgentSnapshotEvaluator
from repoagent.evolver.activation import ActivationError
from test_evolver_finalization import prepare, Backend
from test_evolver_materialization import repository as repository
from test_evolver_hosted_snapshot import factory
from test_evolver_agent_snapshot_live import _task
from test_evolver_contracts import _git


def setup(repository, tmp_path):
    root, proposal, evolver = repository
    candidate, vault = prepare(repository, tmp_path)
    evolver.finalize_search(
        root,
        run_id="search-one",
        candidate_id=candidate,
        vault=vault,
        backend=Backend(),
        max_estimated_cost_usd=0,
    )
    evaluator = HostedAgentSnapshotEvaluator(
        [_task(model_mode="host", responses=())],
        client_factory=factory,
        model_descriptor=factory().descriptor(),
        journal_root=tmp_path / "calls",
    )
    runtime = SnapshotDeployment(
        evolver,
        root,
        baseline_commit=proposal.manifest.base_commit,
        evaluator=evaluator,
    )
    return candidate, runtime


def test_wrong_candidate_token_is_not_consumed(repository, tmp_path):
    candidate, runtime = setup(repository, tmp_path)
    evolver = repository[2]
    token = evolver.approvals.request("candidate_other", "sha256:" + "a" * 64)
    with pytest.raises(ActivationError, match="another candidate"):
        runtime.approve(token, candidate_id=candidate, label="prompt", actor="human")
    assert (
        evolver.approvals.confirm(token, actor="human")["candidate_id"]
        == "candidate_other"
    )
    assert evolver.activations.resolve("prompt") is None


def test_drifted_candidate_cannot_be_approved(repository, tmp_path):
    from repoagent.evolver.workspace import candidate_ref

    candidate, runtime = setup(repository, tmp_path)
    root, proposal, evolver = repository
    token = evolver.request_finalist_approval(
        run_id="search-one", candidate_id=candidate
    )
    _git(root, "update-ref", candidate_ref(candidate), proposal.manifest.base_commit)
    with pytest.raises(ActivationError, match="identity"):
        runtime.approve(token, candidate_id=candidate, label="prompt", actor="human")
    assert evolver.activations.resolve("prompt") is None


def test_skill_route_rejects_disabled_worker(repository, tmp_path):
    candidate, runtime = setup(repository, tmp_path)
    with pytest.raises(ActivationError, match="skill-enabled"):
        runtime.approve("unused", candidate_id=candidate, label="skill", actor="human")


def test_workflow_split_mismatch_fails_before_generation(repository, tmp_path):
    from repoagent.evolver import CandidateCheck, SealedEvaluationVault
    from repoagent.evolver.evaluation import CandidateEvaluationError

    called = []
    vault = SealedEvaluationVault(
        tmp_path / "sealed",
        training_task_ids=["train"],
        sealed_task_ids=["hidden"],
        grader_digest="sha256:" + "a" * 64,
    )
    with pytest.raises(CandidateEvaluationError, match="split"):
        repository[2].prepare_evolution(
            repository[0],
            search_options={
                "paired_checks": [CandidateCheck("hidden", "test")],
                "propose": lambda _: called.append(1),
            },
            select_finalist=lambda _: "unused",
            vault=vault,
            sealed_backend=Backend(),
            sealed_cost_limit_usd=0,
        )
    assert not called


def test_confirmed_but_not_activated_can_recover_once(
    repository, tmp_path, monkeypatch
):
    candidate, runtime = setup(repository, tmp_path)
    evolver = repository[2]
    token = evolver.request_finalist_approval(
        run_id="search-one", candidate_id=candidate
    )
    activate = evolver.activations.activate

    def crash(*args, **kwargs):
        raise OSError("interrupted before activation")

    monkeypatch.setattr(evolver.activations, "activate", crash)
    with pytest.raises(OSError):
        runtime.approve(token, candidate_id=candidate, label="prompt", actor="human")
    monkeypatch.setattr(evolver.activations, "activate", activate)
    runtime.activate_confirmed(candidate_id=candidate, label="prompt", actor="operator")
    assert evolver.activations.resolve("prompt").candidate_id == candidate
    assert (
        len(
            [
                e
                for e in evolver.ledger.events()
                if e["event_type"] == "approval.confirmed"
            ]
        )
        == 1
    )
    runtime.rollback("prompt", actor="human")
    with pytest.raises(ActivationError, match="already activated"):
        runtime.activate_confirmed(
            candidate_id=candidate, label="prompt", actor="operator"
        )


def test_activation_recovery_cannot_invent_human_confirmation(repository, tmp_path):
    candidate, runtime = setup(repository, tmp_path)
    with pytest.raises(ActivationError, match="no durable"):
        runtime.activate_confirmed(
            candidate_id=candidate, label="prompt", actor="operator"
        )
