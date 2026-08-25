import hashlib
from pathlib import Path
import subprocess

import pytest

from repoagent.evolver.contracts import (
    CandidateBudget,
    CandidateProposal,
    EvolutionLabel,
    FailureEvidence,
)
from repoagent.evolver.generator import CandidateGenerator
from repoagent.evolver.sealed import SealedBoundaryError, SealedEvaluationVault
from repoagent.evolver.workspace import CandidateWorkspaceError, GitCandidateWorkspace


GRADER_DIGEST = "sha256:" + "a" * 64


def _evidence():
    return FailureEvidence(
        "failure-1",
        "task-1",
        "prompt-regression",
        "The candidate omitted a required constraint.",
        "sha256:" + hashlib.sha256(b"failure").hexdigest(),
    )


def _proposal(base_commit="a" * 40, content=b"new prompt\n"):
    generator = CandidateGenerator(
        {"prompt": lambda _evidence: {"repoagent/prompt_prefix.py": content}}
    )
    return generator.generate(
        label=EvolutionLabel.PROMPT,
        base_commit=base_commit,
        evidence=[_evidence()],
        repository_reader=lambda _path: b"old prompt\n",
    )


def _git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_candidate_generation_binds_failure_evidence_and_content():
    proposal = _proposal()

    assert proposal.manifest.evidence_ids == ("failure-1",)
    assert proposal.manifest.label is EvolutionLabel.PROMPT
    assert proposal.manifest.patch_digest.startswith("sha256:")
    assert proposal.content["repoagent/prompt_prefix.py"] == b"new prompt\n"
    with pytest.raises(ValueError, match="does not match"):
        CandidateProposal(proposal.manifest, {"repoagent/prompt_prefix.py": b"forged"})


def test_candidate_policy_blocks_wrong_label_sealed_paths_and_budget_overrun():
    with pytest.raises(ValueError, match="outside prompt policy"):
        CandidateGenerator(
            {"prompt": lambda _: {"repoagent/tools.py": b"mutated"}}
        ).generate(
            label="prompt",
            base_commit="a" * 40,
            evidence=[_evidence()],
            repository_reader=lambda _: b"before",
        )
    with pytest.raises(ValueError, match="protected"):
        CandidateGenerator({"skill": lambda _: {"sealed/grader.py": b"leak"}}).generate(
            label="skill",
            base_commit="a" * 40,
            evidence=[_evidence()],
            repository_reader=lambda _: None,
        )
    with pytest.raises(ValueError, match="byte budget"):
        CandidateGenerator(
            {"prompt": lambda _: {"repoagent/prompt_prefix.py": b"too large"}}
        ).generate(
            label="prompt",
            base_commit="a" * 40,
            evidence=[_evidence()],
            repository_reader=lambda _: b"old",
            budget=CandidateBudget(max_changed_bytes=2),
        )


def test_candidate_worktree_is_detached_and_parent_workspace_is_unchanged(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    target = root / "repoagent" / "prompt_prefix.py"
    target.parent.mkdir()
    target.write_bytes(b"old prompt\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    base_commit = _git(root, "rev-parse", "HEAD")

    proposal = _proposal(base_commit)
    with GitCandidateWorkspace(root, proposal) as workspace:
        assert (workspace.root / "repoagent/prompt_prefix.py").read_bytes() == b"new prompt\n"
        identity = workspace.finalize()
        assert identity["base_commit"] == base_commit
        assert identity["commit_sha"] != base_commit
        assert _git(workspace.root, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"

    assert target.read_bytes() == b"old prompt\n"
    assert _git(root, "rev-parse", "HEAD") == base_commit


def test_candidate_worktree_rejects_symlinked_mutation_target(tmp_path):
    root = tmp_path / "repository"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    (outside / "prompt_prefix.py").write_bytes(b"old prompt\n")
    (root / "repoagent").symlink_to(outside, target_is_directory=True)
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    base_commit = _git(root, "rev-parse", "HEAD")

    with pytest.raises(CandidateWorkspaceError, match="symlink"):
        with GitCandidateWorkspace(root, _proposal(base_commit)):
            pass
    assert (outside / "prompt_prefix.py").read_bytes() == b"old prompt\n"


class _Backend:
    is_isolated = True

    def evaluate(self, *, candidate_ref, task_ids, grader_digest):
        assert candidate_ref == "commit-ref"
        assert task_ids == ("sealed-1", "sealed-2")
        assert grader_digest == GRADER_DIGEST
        return {"passed": 2, "secret_detail": "not exposed by receipt"}


def test_sealed_evaluation_returns_blind_receipt_until_evolution_finishes(tmp_path):
    vault = SealedEvaluationVault(
        tmp_path / "sealed",
        training_task_ids=["train-1"],
        sealed_task_ids=["sealed-1", "sealed-2"],
        grader_digest=GRADER_DIGEST,
    )
    receipt = vault.score("candidate_x", "commit-ref", _Backend())

    assert receipt.task_count == 2
    assert not hasattr(receipt, "measurements")
    with pytest.raises(SealedBoundaryError, match="before evolution finishes"):
        vault.unseal("forged")
    results = vault.unseal(vault.finish_evolution())
    assert results[0]["measurements"]["passed"] == 2


def test_sealed_evaluation_rejects_split_leak_unisolated_backend_and_overlap(tmp_path):
    with pytest.raises(SealedBoundaryError, match="leaked"):
        SealedEvaluationVault(
            tmp_path / "vault",
            training_task_ids=["same"],
            sealed_task_ids=["same"],
            grader_digest=GRADER_DIGEST,
        )
    vault = SealedEvaluationVault(
        tmp_path / "vault",
        training_task_ids=["train"],
        sealed_task_ids=["sealed"],
        grader_digest=GRADER_DIGEST,
    )
    with pytest.raises(SealedBoundaryError, match="isolated"):
        vault.score("candidate_x", "ref", object())
    with pytest.raises(SealedBoundaryError, match="safe candidate id"):
        vault.score("../../candidate_escape", "ref", _Backend())
    with pytest.raises(SealedBoundaryError, match="overlaps"):
        vault.score(
            "candidate_x",
            "ref",
            _Backend(),
            candidate_workspace=Path(tmp_path),
        )
