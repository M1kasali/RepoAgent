from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import subprocess

import pytest

from repoagent.evolver.contracts import CandidateProposal
from repoagent.evolver.ledger import EvolutionLedger
from repoagent.evolver.orchestrator import ControlledEvolver
from repoagent.evolver.workspace import CandidateWorkspaceError, GitCandidateWorkspace, candidate_ref
from test_evolver_contracts import _git, _proposal


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    (root / "repoagent").mkdir()
    (root / "repoagent/prompt_prefix.py").write_bytes(b"old prompt\n")
    (root / "protected.txt").write_text("unchanged")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    proposal = _proposal(_git(root, "rev-parse", "HEAD"))
    evolver = ControlledEvolver(EvolutionLedger(tmp_path / "state/ledger.jsonl"))
    return root, proposal, evolver


def test_materialization_is_idempotent_pinned_and_does_not_touch_user_changes(repository):
    root, proposal, evolver = repository
    (root / "protected.txt").write_text("user edit")
    identity = evolver.materialize_candidate(root, proposal)
    assert evolver.materialize_candidate(root, proposal) == identity
    assert [event["event_type"] for event in evolver.ledger.events()] == [
        "candidate.created", "candidate.materialized"
    ]
    _git(root, "reflog", "expire", "--expire=now", "--all")
    _git(root, "gc", "--prune=now")
    assert _git(root, "rev-parse", candidate_ref(proposal.manifest.candidate_id)) == identity["commit_sha"]
    assert _git(root, "cat-file", "-t", identity["commit_sha"]) == "commit"
    assert _git(root, "rev-parse", "HEAD") == proposal.manifest.base_commit
    assert (root / "protected.txt").read_text() == "user edit"
    assert (root / "repoagent/prompt_prefix.py").read_bytes() == b"old prompt\n"


def test_resume_after_commit_pin_before_ledger_write(repository, monkeypatch):
    root, proposal, evolver = repository
    original = evolver.record_materialized

    def crash(*args, **kwargs):
        raise OSError("simulated journal interruption")

    monkeypatch.setattr(evolver, "record_materialized", crash)
    with pytest.raises(OSError, match="interruption"):
        evolver.materialize_candidate(root, proposal)
    pinned = _git(root, "rev-parse", candidate_ref(proposal.manifest.candidate_id))
    monkeypatch.setattr(evolver, "record_materialized", original)
    identity = evolver.materialize_candidate(root, proposal)
    assert identity["commit_sha"] == pinned
    assert len(evolver.ledger.events()) == 2


@pytest.mark.parametrize("staged", [False, True])
def test_undeclared_worktree_changes_cannot_be_materialized(repository, staged):
    root, proposal, _ = repository
    with GitCandidateWorkspace(root, proposal) as workspace:
        (workspace.root / "protected.txt").write_text("tampered")
        if staged:
            _git(workspace.root, "add", "protected.txt")
        with pytest.raises(CandidateWorkspaceError, match="undeclared"):
            workspace.finalize()


def test_candidate_content_changed_after_apply_is_rejected(repository):
    root, proposal, _ = repository
    with GitCandidateWorkspace(root, proposal) as workspace:
        (workspace.root / "repoagent/prompt_prefix.py").write_text("different candidate")
        with pytest.raises(CandidateWorkspaceError, match="content differs"):
            workspace.finalize()


def test_executable_mode_change_is_not_hidden_in_candidate(repository):
    root, proposal, _ = repository
    with GitCandidateWorkspace(root, proposal) as workspace:
        _git(workspace.root, "add", "repoagent/prompt_prefix.py")
        _git(workspace.root, "update-index", "--chmod=+x", "repoagent/prompt_prefix.py")
        # Preserve the staged mode even when Git ignores executable worktree bits.
        _git(workspace.root, "-c", "core.filemode=false", "status", "--short")
        (workspace.root / "repoagent/prompt_prefix.py").chmod(0o755)
        with pytest.raises(CandidateWorkspaceError, match="file type or mode"):
            workspace.finalize()


@pytest.mark.parametrize("delete", [False, True])
def test_changed_or_deleted_pinned_reference_fails_closed(repository, delete):
    root, proposal, evolver = repository
    evolver.materialize_candidate(root, proposal)
    ref = candidate_ref(proposal.manifest.candidate_id)
    if delete:
        _git(root, "update-ref", "-d", ref)
    else:
        _git(root, "update-ref", ref, proposal.manifest.base_commit)
    with pytest.raises(CandidateWorkspaceError, match="reference is missing or changed"):
        evolver.materialize_candidate(root, proposal)
    assert len(evolver.ledger.events()) == 2


def test_candidate_id_cannot_be_reused_for_another_manifest(repository):
    root, proposal, evolver = repository
    evolver.materialize_candidate(root, proposal)
    other = _proposal(proposal.manifest.base_commit, content=b"another prompt\n")
    changed = CandidateProposal(replace(other.manifest, candidate_id=proposal.manifest.candidate_id), other.content)
    with pytest.raises(CandidateWorkspaceError, match="another manifest"):
        evolver.materialize_candidate(root, changed)


def test_existing_ref_is_never_overwritten_by_finalize(repository):
    root, proposal, _ = repository
    ref = candidate_ref(proposal.manifest.candidate_id)
    _git(root, "update-ref", ref, proposal.manifest.base_commit)
    with GitCandidateWorkspace(root, proposal) as workspace:
        with pytest.raises(CandidateWorkspaceError):
            workspace.finalize()
    assert _git(root, "rev-parse", ref) == proposal.manifest.base_commit


def test_finalized_worktree_head_matches_immutable_identity(repository):
    root, proposal, _ = repository
    with GitCandidateWorkspace(root, proposal) as workspace:
        identity = workspace.finalize()
        assert _git(workspace.root, "rev-parse", "HEAD") == identity["commit_sha"]
        assert workspace.finalize() == identity
    assert _git(root, "rev-parse", "HEAD") == proposal.manifest.base_commit


def test_commit_hooks_are_not_executed(repository):
    root, proposal, evolver = repository
    hook = root / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 99\n")
    hook.chmod(0o755)
    identity = evolver.materialize_candidate(root, proposal)
    assert identity["commit_sha"]
    worktrees = subprocess.run(
        ["git", "worktree", "list", "--porcelain"], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    assert worktrees.count("worktree ") == 1


def test_concurrent_materializers_share_one_candidate_identity(repository):
    root, proposal, evolver = repository

    def run(_):
        other = ControlledEvolver(EvolutionLedger(evolver.ledger.path))
        return other.materialize_candidate(root, proposal)

    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = list(pool.map(run, range(4)))
    assert all(identity == identities[0] for identity in identities)
    assert len(evolver.ledger.events()) == 2
