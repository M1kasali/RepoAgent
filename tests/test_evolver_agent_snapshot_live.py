"""Pinned Runtime execution with free scripted responses in real Docker."""

import io
import json
import os
from pathlib import Path
import tarfile

import pytest

from repoagent.evaluation.container import wsl_windows_path
from repoagent.evolver import (
    AgentSnapshotTask,
    CandidateCheck,
    CandidateGenerator,
    ControlledEvolver,
    DockerCandidateEvaluator,
    EvolutionLedger,
    EvolutionRunBudget,
    PairedPromotionGate,
    ScriptedAgentSnapshotEvaluator,
)
from repoagent.evolver.workspace import _git_bytes
from test_evolver_contracts import _git, _evidence


pytestmark = pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"),
    reason="explicit real Docker acceptance required",
)


@pytest.fixture
def source_repository(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    project = Path(__file__).resolve().parents[1]
    archive = _git_bytes(project, "archive", "HEAD", "repoagent")
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        stream.extractall(root, filter="data")
    _git(root, "init")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "snapshot fixture")
    before = (root / "repoagent/prompt_prefix.py").read_bytes()
    after = before.replace(
        b"You are repoagent", b"SNAPSHOT_CANDIDATE_MARKER You are repoagent"
    )
    assert after != before
    proposal = CandidateGenerator(
        {"prompt": lambda _: {"repoagent/prompt_prefix.py": after}}
    ).generate(
        label="prompt",
        base_commit=_git(root, "rev-parse", "HEAD"),
        evidence=[_evidence()],
        repository_reader=lambda name: (root / name).read_bytes(),
    )
    evolver = ControlledEvolver(EvolutionLedger(tmp_path / "state/ledger.jsonl"))
    return root, proposal, evolver


def _options():
    return dict(
        executable=os.environ["REPOAGENT_TEST_DOCKER"],
        path_converter=wsl_windows_path
        if os.environ.get("REPOAGENT_TEST_DOCKER_WSL") == "1"
        else None,
    )


def _task(**kwargs):
    values = dict(
        task_id="write",
        prompt="Write done to result.txt",
        files={"README.md": "fixture"},
        responses=(
            '<tool name="write_file" path="result.txt"><content>done</content></tool>',
            "<final>Done.</final>",
        ),
        expected_files={"result.txt": "done"},
    )
    values.update(kwargs)
    return AgentSnapshotTask(**values)


def test_actual_snapshot_agent_runs_both_commits_and_replays(source_repository):
    root, proposal, evolver = source_repository
    sanity = DockerCandidateEvaluator(**_options())
    assert evolver.evaluate_candidate(
        root,
        proposal,
        [
            CandidateCheck(
                "syntax",
                "python -B -c 'import ast; from pathlib import Path; ast.parse(Path(\"repoagent/prompt_prefix.py\").read_text())'",
            )
        ],
        sanity,
    ).passed
    backend = ScriptedAgentSnapshotEvaluator([_task()], **_options())
    options = dict(
        gate=PairedPromotionGate(min_unique_tasks=1),
        run_budget=EvolutionRunBudget(max_pairs=1, max_estimated_cost_usd=0),
    )
    result = evolver.evaluate_paired_checks(
        root, proposal, backend.checks(), backend, **options
    )
    receipts = [
        json.loads(path.read_text())
        for path in sorted(
            (evolver.ledger.path.parent / "evidence").glob("*-paired-*.json")
        )
    ]
    assert len(receipts) == 2, receipts
    assert [row["result"]["passed"] for row in receipts] == [True, True], receipts
    control, treatment = [row["result"]["raw"]["worker"] for row in receipts]
    assert "SNAPSHOT_CANDIDATE_MARKER" not in control["prefix_excerpt"]
    assert "SNAPSHOT_CANDIDATE_MARKER" in treatment["prefix_excerpt"]
    assert (
        control["modules"]["repoagent.prompt_prefix"]["sha256"]
        != treatment["modules"]["repoagent.prompt_prefix"]["sha256"]
    )
    assert len(control["calls"]) == len(treatment["calls"]) == 2
    assert not result.passed  # Both tasks passed; a tie is not an improvement.
    assert result.metrics["control_passes"] == result.metrics["treatment_passes"] == 1
    assert (
        evolver.evaluate_paired_checks(
            root, proposal, backend.checks(), backend, **options
        ).to_dict()
        == result.to_dict()
    )
    assert _git(root, "worktree", "list", "--porcelain").count("worktree ") == 1
    assert not (root / "result.txt").exists()


@pytest.mark.parametrize(
    "task,valid,passed",
    [
        (_task(max_calls=1), True, False),
        (_task(expected_files={"result.txt": "hidden-host-answer"}), True, False),
        (_task(responses=("<tool>invalid</tool>",)), False, None),
    ],
)
def test_budget_grading_and_worker_failure_are_distinct(
    source_repository, task, valid, passed
):
    root, proposal, evolver = source_repository
    identity = evolver.materialize_candidate(root, proposal)
    backend = ScriptedAgentSnapshotEvaluator([task], **_options())
    result = backend.run_trial(
        root, identity, task.check(), 0, backend.descriptor(root), cost_limit_usd=0
    )
    assert (result["status"] == "completed") is valid, result
    assert result["passed"] is passed, result
    assert result["estimated_cost_usd"] == 0
    if valid:
        assert len(result["raw"]["worker"]["calls"]) <= task.max_calls
    assert _git(root, "worktree", "list", "--porcelain").count("worktree ") == 1
