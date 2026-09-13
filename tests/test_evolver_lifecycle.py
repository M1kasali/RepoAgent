import os

import pytest

from repoagent.evolver import (
    CandidateCheck,
    DockerCandidateEvaluator,
    EvolutionRunBudget,
    PairedPromotionGate,
    SealedEvaluationVault,
)
from repoagent.evolver.deployment import SnapshotDeployment
from repoagent.evolver.hosted_snapshot import HostedAgentSnapshotEvaluator
from repoagent.evolver.sealed_snapshot import SnapshotSealedBackend
from repoagent.evolver.search import SearchLimits
from test_evolver_agent_snapshot_live import (
    source_repository as source_repository,
    _options,
    _task,
)
from test_evolver_hosted_snapshot import factory as gateway_factory


class MarkerLeaf:
    model = "fixture-model"

    def __init__(self):
        from test_evolver_model_budget import LeafClient

        self.leaf = LeafClient()
        self.profile = self.leaf.profile
        self.calls = 0

    def generate(self, request):
        from repoagent.providers.base import ModelResult

        self.calls += 1
        content = (
            "done" if "SNAPSHOT_CANDIDATE_MARKER" in request.prompt else "baseline"
        )
        text = (
            f'<tool name="write_file" path="result.txt"><content>{content}</content></tool>'
            if self.calls == 1
            else "<final>Done.</final>"
        )
        return ModelResult(
            text=text, model=self.model, usage=self.leaf.usage, provider="fixture"
        )


def model_factory():
    from test_evolver_model_budget import _client

    return _client(MarkerLeaf(), limits=gateway_factory().limits)


@pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"),
    reason="opt-in Docker lifecycle integration",
)
@pytest.mark.parametrize("paired_sealed", [False, True])
def test_search_sealed_approval_execute_and_rollback(
    source_repository, tmp_path, paired_sealed
):
    root, proposal, evolver = source_repository

    def evaluator(task):
        return HostedAgentSnapshotEvaluator(
            [task],
            client_factory=model_factory,
            model_descriptor=model_factory().descriptor(),
            journal_root=tmp_path / "calls",
            **_options(),
        )

    training = evaluator(_task(model_mode="host", responses=()))
    import json
    from dataclasses import replace
    from repoagent.evolver import ModelCandidateProposer
    from test_evolver_contracts import _evidence
    from test_evolver_model_budget import _client, LeafClient

    proposer = ModelCandidateProposer(
        root,
        base_commit=proposal.manifest.base_commit,
        label="prompt",
        paths=["repoagent/prompt_prefix.py"],
        evidence=[replace(_evidence(), task_id="write")],
        client=_client(
            LeafClient(
                outputs=[
                    json.dumps(
                        {
                            "files": {
                                path: data.decode()
                                for path, data in proposal.content.items()
                            }
                        }
                    )
                ]
            ),
            limits=gateway_factory().limits,
        ),
        journal_directory=tmp_path / "generation",
    )
    search_options = dict(
        run_id="lifecycle",
        base_commit=proposal.manifest.base_commit,
        propose=proposer,
        deterministic_checks=[CandidateCheck("syntax", "python -c 'pass'")],
        deterministic_evaluator=DockerCandidateEvaluator(**_options()),
        paired_checks=training.checks(),
        paired_evaluator=training,
        gate=PairedPromotionGate(min_unique_tasks=1),
        run_budget=EvolutionRunBudget(max_pairs=1, max_estimated_cost_usd=2),
        max_trial_cost_usd=1,
        limits=SearchLimits(max_rounds=1),
    )
    sealed = SnapshotSealedBackend(
        root,
        evaluator(_task(task_id="hidden", model_mode="host", responses=())),
        baseline_commit=proposal.manifest.base_commit if paired_sealed else None,
    )
    vault = SealedEvaluationVault(
        tmp_path / "sealed",
        training_task_ids=["write"],
        sealed_task_ids=["hidden"],
        grader_digest=sealed.grader_digest,
    )
    prepared = evolver.prepare_evolution(
        root,
        search_options=search_options,
        select_finalist=lambda state: state["qualified_candidates"][0],
        vault=vault,
        sealed_backend=sealed,
        sealed_cost_limit_usd=2 if paired_sealed else 1,
    )
    assert prepared["status"] == "awaiting_human_approval"
    assert prepared["sealed"]["passed"]
    if paired_sealed:
        assert prepared["sealed"]["comparison"] == {"wins": 1, "ties": 0, "losses": 0}
    candidate = prepared["search"]["qualified_candidates"][0]

    control_receipt = json.loads(
        (
            evolver.ledger.path.parent / "evidence" / f"{candidate}-paired-0.json"
        ).read_text()
    )
    journal_path = control_receipt["result"]["raw"]["model_journal"]["path"]
    accounting = evolver.reconcile_trial_cost(
        candidate, 0, journal_path=journal_path, actor="test-operator"
    )
    assert accounting["quality_recovered"] is False
    assert accounting["released_cost_usd"] == "0"
    assert (
        evolver.reconcile_trial_cost(
            candidate, 0, journal_path=journal_path, actor="test-operator"
        )
        == accounting
    )
    from repoagent.evolver.evaluation import CandidateEvaluationError

    with pytest.raises(CandidateEvaluationError, match="outside"):
        evolver.reconcile_trial_cost(
            candidate,
            0,
            journal_path=tmp_path / "unrelated.json",
            actor="test-operator",
        )
    runtime = SnapshotDeployment(
        evolver, root, baseline_commit=proposal.manifest.base_commit, evaluator=training
    )
    before = runtime.run_task("write", cost_limit_usd=1)
    assert before["plan"]["activation_event_id"] is None
    assert not before["result"]["passed"]
    token = prepared["approval_token"]
    runtime.approve(token, candidate_id=candidate, label="prompt", actor="test-human")
    active = runtime.run_task("write", cost_limit_usd=1)
    assert active["plan"]["source"]["candidate_id"] == candidate
    assert active["result"]["passed"]
    assert (
        "SNAPSHOT_CANDIDATE_MARKER"
        in active["result"]["raw"]["worker"]["prefix_excerpt"]
    )
    runtime.rollback("prompt", actor="test-human")
    restored = runtime.run_task("write", cost_limit_usd=1)
    assert restored["plan"]["source"]["commit_sha"] == proposal.manifest.base_commit
    assert not restored["result"]["passed"]
    assert (
        "SNAPSHOT_CANDIDATE_MARKER"
        not in restored["result"]["raw"]["worker"]["prefix_excerpt"]
    )
    assert not (root / "result.txt").exists()


@pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"),
    reason="opt-in skill snapshot integration",
)
def test_skill_files_are_loaded_from_pinned_source(source_repository):
    from test_skills import write_skill
    from test_evolver_contracts import _git
    from repoagent.evolver import ScriptedAgentSnapshotEvaluator

    root, _, _ = source_repository
    write_skill(
        root / "skills", "write", body="Write files using the verified workflow."
    )
    _git(root, "add", "skills")
    _git(root, "commit", "-m", "skill fixture")
    task = _task(enable_skills=True)
    backend = ScriptedAgentSnapshotEvaluator([task], **_options())
    identity = {
        "commit_sha": _git(root, "rev-parse", "HEAD"),
        "tree_sha": _git(root, "rev-parse", "HEAD^{tree}"),
    }
    result = backend.run_trial(
        root, identity, task.check(), 0, backend.descriptor(root), cost_limit_usd=0
    )
    assert result["passed"], result
    assert any("write" in skill for skill in result["raw"]["worker"]["active_skills"])
