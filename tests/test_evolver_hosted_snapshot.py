import os

import pytest

from repoagent.evolver.hosted_snapshot import HostedAgentSnapshotEvaluator
from repoagent.evolver.model_budget import EvaluationModelLimits
from repoagent.evolver.workspace import _git
from test_evolver_agent_snapshot_live import (
    source_repository as source_repository,
    _task,
    _options,
)
from test_evolver_model_budget import _client, LeafClient


def factory():
    return _client(
        LeafClient(
            outputs=[
                '<tool name="write_file" path="result.txt"><content>done</content></tool>',
                "<final>Done.</final>",
            ]
        ),
        limits=EvaluationModelLimits(
            max_calls=4,
            max_input_tokens=20000,
            max_output_tokens=512,
            timeout_seconds=30,
        ),
    )


def test_hosted_task_input_has_no_script_or_grader(tmp_path):
    task = _task(model_mode="host", responses=())
    backend = HostedAgentSnapshotEvaluator(
        [task],
        client_factory=factory,
        model_descriptor=factory().descriptor(),
        journal_root=tmp_path / "journal",
    )
    data = backend._worker_input(task)
    assert "responses" not in data
    assert "expected_files" not in data
    assert data["model"] == "fixture-model"
    with pytest.raises(ValueError):
        _task(model_mode="host")


@pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"), reason="opt-in Docker functional test"
)
@pytest.mark.parametrize("cap,success", [(1.0, True), (0.5, False)])
def test_pinned_snapshot_calls_host_gateway(source_repository, tmp_path, cap, success):
    from repoagent.evolver.evaluation import CandidateEvaluationError

    root, _, _ = source_repository
    task = _task(model_mode="host", responses=())
    backend = HostedAgentSnapshotEvaluator(
        [task],
        client_factory=factory,
        model_descriptor=factory().descriptor(),
        journal_root=tmp_path / "journal",
        **_options(),
    )
    identity = {
        "commit_sha": _git(root, "rev-parse", "HEAD"),
        "tree_sha": _git(root, "rev-parse", "HEAD^{tree}"),
    }

    def run():
        return backend.run_trial(
            root,
            identity,
            task.check(),
            0,
            backend.descriptor(root),
            cost_limit_usd=cap,
        )

    if not success:
        with pytest.raises(CandidateEvaluationError, match="limits"):
            run()
        assert not list((tmp_path / "journal").iterdir())
        return
    result = run()
    assert result["passed"], result
    assert result["estimated_cost_usd"] > 0
    assert result["raw"]["model_evidence"]["calls_reserved"] == 2
    assert result["raw"]["model_journal"]["events"] == 7
    assert not (root / "result.txt").exists()


@pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"), reason="opt-in Docker functional test"
)
def test_hosted_pair_binds_costs_and_resumes_without_calls(source_repository, tmp_path):
    import json
    from repoagent.evolver import (
        CandidateCheck,
        DockerCandidateEvaluator,
        EvolutionRunBudget,
        PairedPromotionGate,
    )

    root, proposal, evolver = source_repository
    assert evolver.evaluate_candidate(
        root,
        proposal,
        [CandidateCheck("syntax", "python -c 'pass'")],
        DockerCandidateEvaluator(**_options()),
    ).passed
    clients = []

    def make_client():
        client = factory()
        clients.append(client)
        return client

    backend = HostedAgentSnapshotEvaluator(
        [_task(model_mode="host", responses=())],
        client_factory=make_client,
        model_descriptor=factory().descriptor(),
        journal_root=tmp_path / "journal",
        **_options(),
    )
    options = dict(
        gate=PairedPromotionGate(min_unique_tasks=1),
        run_budget=EvolutionRunBudget(max_pairs=1, max_estimated_cost_usd=2),
        max_trial_cost_usd=1,
    )
    result = evolver.evaluate_paired_checks(
        root, proposal, backend.checks(), backend, **options
    )
    assert result.metrics["control_passes"] == result.metrics["treatment_passes"] == 1
    assert not result.passed
    receipts = [
        json.loads(path.read_text())
        for path in (evolver.ledger.path.parent / "evidence").glob("*-paired-*.json")
    ]
    assert len(receipts) == 2
    assert all(row["result"]["estimated_cost_usd"] > 0 for row in receipts)
    assert len({row["result"]["raw"]["model_journal"]["path"] for row in receipts}) == 2
    assert (
        evolver.evaluate_paired_checks(
            root, proposal, backend.checks(), backend, **options
        ).to_dict()
        == result.to_dict()
    )
    assert len(clients) == 2
