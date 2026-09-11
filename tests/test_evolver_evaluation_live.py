"""Real candidate checks; no model, network, image pulls or package installs."""

import os
import json

import pytest

from repoagent.evolver.evaluation import CandidateCheck, DockerCandidateEvaluator, CandidateEvaluationError
from repoagent.evaluation.container import wsl_windows_path
from test_evolver_materialization import repository as _repository
from test_evolver_contracts import _git


repository = _repository


pytestmark = pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"), reason="explicit real Docker acceptance required"
)


def evaluator(image="python:3.12-slim"):
    return DockerCandidateEvaluator(
        executable=os.environ["REPOAGENT_TEST_DOCKER"], image=image,
        path_converter=wsl_windows_path if os.environ.get("REPOAGENT_TEST_DOCKER_WSL") == "1" else None,
    )


def test_real_candidate_check_uses_pinned_content_and_retains_output(repository):
    root, proposal, evolver = repository
    check = CandidateCheck("content", 'python -I -c "from pathlib import Path; assert Path(\'repoagent/prompt_prefix.py\').read_text() == \'new prompt\\n\'; print(\'candidate checked\')"')
    backend = evaluator()
    decision = evolver.evaluate_candidate(root, proposal, [check], backend)
    assert decision.passed
    artifact = next((evolver.ledger.path.parent / "evidence").glob("*.json"))
    receipt = json.loads(artifact.read_text())
    assert receipt["results"][0]["stdout"] == "candidate checked\n"
    assert receipt["plan"]["evaluator"]["image_id"].startswith("sha256:")
    assert (root / "repoagent/prompt_prefix.py").read_bytes() == b"old prompt\n"
    assert _git(root, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert evolver.evaluate_candidate(root, proposal, [check], backend).to_dict() == decision.to_dict()


@pytest.mark.parametrize("command,status", [
    ("python -I -c 'raise SystemExit(1)'", "fail"),
    ("sleep 10", "error"),
    ("printf tampered > repoagent/prompt_prefix.py", "error"),
    ("command-not-installed-in-image", "error"),
])
def test_real_failure_timeout_and_source_drift_are_not_passes(repository, command, status):
    root, proposal, evolver = repository
    decision = evolver.evaluate_candidate(
        root, proposal, [CandidateCheck("check", command, timeout_seconds=2 if command == "sleep 10" else 10)], evaluator()
    )
    assert not decision.passed
    assert decision.observations[0].status == status
    assert evolver.ledger.events()[-1]["event_type"] == "gate.evaluated"
    assert (root / "repoagent/prompt_prefix.py").read_bytes() == b"old prompt\n"


def test_real_missing_image_does_not_fall_back_to_host(repository):
    root, proposal, evolver = repository
    with pytest.raises(CandidateEvaluationError, match="not available locally"):
        evolver.evaluate_candidate(
            root, proposal, [CandidateCheck("check", "touch must-not-run")],
            evaluator(image="repoagent-deliberately-missing-eval-image:never-pull"),
        )
    assert not (root / "must-not-run").exists()
    assert not any(event["event_type"].startswith("evaluation.") for event in evolver.ledger.events())
