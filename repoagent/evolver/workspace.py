"""Detached Git worktrees for candidate application and commit identity."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import re

from .contracts import CandidateProposal, sha256_bytes


class CandidateWorkspaceError(RuntimeError):
    pass


def _git(root, *args, check=True):
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise CandidateWorkspaceError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def _git_bytes(root, *args):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True)
    if result.returncode:
        raise CandidateWorkspaceError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def candidate_ref(candidate_id):
    if not re.fullmatch(r"candidate_[A-Za-z0-9_-]+", candidate_id):
        raise CandidateWorkspaceError("unsafe candidate id for persistent reference")
    return f"refs/repoagent/candidates/{candidate_id}"


def verify_benchmark_target(repo_root, target):
    """Verify scope against immutable baseline objects, without loading the grader."""
    if _git(repo_root, "rev-parse", target.base_commit + "^{commit}") != target.base_commit:
        raise CandidateWorkspaceError("benchmark target base is not an exact commit")
    for path in (*target.mutable_paths, *target.protected_paths):
        entry = _git_bytes(repo_root, "ls-tree", "-z", target.base_commit, "--", path)
        if entry.split(b" ", 1)[0] not in {b"100644", b"100755"}:
            raise CandidateWorkspaceError("benchmark target paths must be tracked regular files")


def verify_candidate_commit(repo_root, proposal, commit_sha):
    """Check immutable Git objects without importing or executing candidate code."""
    if not isinstance(proposal, CandidateProposal):
        raise TypeError("candidate verification requires CandidateProposal")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit_sha):
        raise CandidateWorkspaceError("candidate requires an exact commit SHA")
    if _git(repo_root, "rev-parse", f"{commit_sha}^{{commit}}") != commit_sha:
        raise CandidateWorkspaceError("candidate identity is not a commit")
    base = proposal.manifest.base_commit
    if proposal.manifest.benchmark_target is not None:
        verify_benchmark_target(repo_root, proposal.manifest.benchmark_target)
    if _git(repo_root, "show", "-s", "--format=%P", commit_sha).split() != [base]:
        raise CandidateWorkspaceError("candidate commit has an unexpected parent")
    paths = set(_git_bytes(
        repo_root, "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z", base, commit_sha, "--"
    ).decode("utf-8").split("\0")) - {""}
    if paths != set(proposal.content):
        raise CandidateWorkspaceError("candidate commit changed undeclared paths")
    for mutation in proposal.manifest.mutations:
        entry = _git_bytes(repo_root, "ls-tree", "-z", commit_sha, "--", mutation.path)
        mode = entry.split(b" ", 1)[0]
        before_entry = _git_bytes(repo_root, "ls-tree", "-z", base, "--", mutation.path)
        expected_mode = before_entry.split(b" ", 1)[0] if before_entry else b"100644"
        if mode not in {b"100644", b"100755"} or mode != expected_mode:
            raise CandidateWorkspaceError("candidate changed file type or mode")
        after = _git_bytes(repo_root, "cat-file", "blob", f"{commit_sha}:{mutation.path}")
        before = _git_bytes(repo_root, "cat-file", "blob", f"{base}:{mutation.path}") if before_entry else None
        if sha256_bytes(after) != mutation.after_sha256 or (
            sha256_bytes(before) if before is not None else None
        ) != mutation.before_sha256:
            raise CandidateWorkspaceError("candidate commit content differs from manifest")
    return {
        "candidate_id": proposal.manifest.candidate_id, "base_commit": base,
        "commit_sha": commit_sha, "tree_sha": _git(repo_root, "rev-parse", f"{commit_sha}^{{tree}}"),
        "patch_digest": proposal.manifest.patch_digest,
    }


class GitCandidateWorkspace:
    def __init__(self, repo_root, proposal):
        if not isinstance(proposal, CandidateProposal):
            raise TypeError("candidate workspace requires CandidateProposal")
        self.repo_root = Path(repo_root).resolve()
        self.proposal = proposal
        self._temporary = None
        self.root = None
        self.commit_sha = ""

    def __enter__(self):
        if self.proposal.manifest.benchmark_target is not None:
            verify_benchmark_target(self.repo_root, self.proposal.manifest.benchmark_target)
        resolved = _git(self.repo_root, "rev-parse", self.proposal.manifest.base_commit)
        if resolved != self.proposal.manifest.base_commit:
            raise CandidateWorkspaceError("candidate base commit must be an exact commit SHA")
        self._temporary = tempfile.TemporaryDirectory(prefix="repoagent-candidate-")
        self.root = Path(self._temporary.name) / "worktree"
        _git(
            self.repo_root,
            "-c",
            "core.autocrlf=false",
            "worktree",
            "add",
            "--detach",
            str(self.root),
            self.proposal.manifest.base_commit,
        )
        try:
            self._apply()
        except BaseException:
            self.close()
            raise
        return self

    def _apply(self):
        for mutation in self.proposal.manifest.mutations:
            target = self.root / mutation.path
            current = self.root
            for part in mutation.path.split("/"):
                current = current / part
                if current.is_symlink():
                    raise CandidateWorkspaceError(
                        f"candidate mutation path contains symlink: {mutation.path}"
                    )
            root_path = os.path.normcase(str(self.root.resolve()))
            target_path = os.path.normcase(str(target.resolve(strict=False)))
            try:
                contained = os.path.commonpath((root_path, target_path)) == root_path
            except ValueError:
                contained = False
            if not contained or target_path == root_path:
                raise CandidateWorkspaceError(
                    f"candidate mutation escapes worktree: {mutation.path}"
                )
            before = target.read_bytes() if target.is_file() else None
            observed = sha256_bytes(before) if before is not None else None
            if observed != mutation.before_sha256:
                raise CandidateWorkspaceError(
                    f"candidate baseline digest mismatch: {mutation.path}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(self.proposal.content[mutation.path])
            if sha256_bytes(target.read_bytes()) != mutation.after_sha256:
                raise CandidateWorkspaceError(
                    f"candidate output digest mismatch: {mutation.path}"
                )

    def finalize(self):
        if self.commit_sha:
            return verify_candidate_commit(self.repo_root, self.proposal, self.commit_sha)
        base = self.proposal.manifest.base_commit
        if _git(self.root, "rev-parse", "HEAD") != base:
            raise CandidateWorkspaceError("candidate worktree HEAD moved before finalization")
        changed = set(_git_bytes(
            self.root, "diff", "--no-ext-diff", "--name-only", "-z", base, "--"
        ).decode("utf-8").split("\0")) - {""}
        untracked = set(_git_bytes(
            self.root, "ls-files", "--others", "--exclude-standard", "-z"
        ).decode("utf-8").split("\0")) - {""}
        if (changed | untracked) - set(self.proposal.content):
            raise CandidateWorkspaceError("candidate worktree contains undeclared changes")
        _git(self.root, "add", "--", *[item.path for item in self.proposal.manifest.mutations])
        tree = _git(self.root, "write-tree")
        commit_sha = _git(
            self.root,
            "-c",
            "user.name=RepoAgent Evolver",
            "-c",
            "user.email=evolver@repoagent.invalid",
            "-c", "commit.gpgsign=false",
            "commit-tree", tree, "-p", base,
            "-m",
            f"candidate {self.proposal.manifest.candidate_id}",
        )
        identity = verify_candidate_commit(self.repo_root, self.proposal, commit_sha)
        ref = candidate_ref(self.proposal.manifest.candidate_id)
        _git(self.repo_root, "update-ref", ref, commit_sha, "0" * len(commit_sha))
        _git(self.root, "update-ref", "--no-deref", "HEAD", commit_sha, base)
        self.commit_sha = commit_sha
        return identity

    @property
    def workspace_digest(self):
        digest = hashlib.sha256()
        for path in sorted(item for item in self.root.rglob("*") if item.is_file()):
            if ".git" in path.parts:
                continue
            relative = path.relative_to(self.root).as_posix().encode("utf-8")
            digest.update(relative)
            digest.update(path.read_bytes())
        return "sha256:" + digest.hexdigest()

    def close(self):
        if self.root is not None and self.root.exists():
            _git(self.repo_root, "worktree", "remove", "--force", str(self.root), check=False)
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

    def __exit__(self, exc_type, exc, traceback):
        self.close()


__all__ = ["CandidateWorkspaceError", "GitCandidateWorkspace"]
