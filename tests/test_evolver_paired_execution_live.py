"""Live binary paired checks, not model-quality or coding-benchmark claims."""

import json
import os

import pytest

from repoagent.evaluation.container import wsl_windows_path
from repoagent.evolver import (
    CandidateCheck,
    DockerPairedCheckEvaluator,
    EvolutionRunBudget,
    PairedPromotionGate,
)
from test_evolver_materialization import repository as _repository
from test_evolver_contracts import _git

repository = _repository

pytestmark = pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"),
    reason="explicit real Docker acceptance required",
)


@pytest.mark.parametrize(
    "command,passed,reported",
    [
        (
            "python -I -c \"from pathlib import Path; assert Path('repoagent/prompt_prefix.py').read_text() == 'new prompt\\n'\"",
            True,
            2,
        ),
        ("true", False, 2),
        ("command-deliberately-unavailable-in-image", False, 1),
    ],
)
def test_real_paired_check_execution_and_replay(repository, command, passed, reported):
    root, proposal, evolver = repository
    backend = DockerPairedCheckEvaluator(
        executable=os.environ["REPOAGENT_TEST_DOCKER"],
        path_converter=wsl_windows_path
        if os.environ.get("REPOAGENT_TEST_DOCKER_WSL") == "1"
        else None,
    )
    assert evolver.evaluate_candidate(
        root, proposal, [CandidateCheck("sanity", "true")], backend
    ).passed
    checks = [CandidateCheck("quality", command, timeout_seconds=15)]
    options = dict(
        gate=PairedPromotionGate(min_unique_tasks=1),
        run_budget=EvolutionRunBudget(max_pairs=1, max_estimated_cost_usd=0),
    )
    result = evolver.evaluate_paired_checks(root, proposal, checks, backend, **options)
    assert result.passed is passed
    assert result.metrics["planned_run_n"] == 2
    assert result.metrics["reported_run_n"] == reported
    assert result.metrics["known_estimated_cost_usd"] == 0
    receipts = [
        json.loads(path.read_text())
        for path in sorted(
            (evolver.ledger.path.parent / "evidence").glob("*-paired-*.json")
        )
    ]
    assert (
        receipts[0]["request"]["identity"]["commit_sha"]
        == proposal.manifest.base_commit
    )
    assert receipts[0]["result"]["raw"]["results"][0]["check_id"] == "quality"
    if passed:
        assert [row["result"]["passed"] for row in receipts] == [False, True]
        assert (
            receipts[0]["request"]["identity"]["commit_sha"]
            != receipts[1]["request"]["identity"]["commit_sha"]
        )
    assert (
        evolver.evaluate_paired_checks(
            root, proposal, checks, backend, **options
        ).to_dict()
        == result.to_dict()
    )
    assert _git(root, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert (root / "repoagent/prompt_prefix.py").read_bytes() == b"old prompt\n"
    assert not any(
        event["event_type"].startswith(("approval.", "activation."))
        for event in evolver.ledger.events()
    )
