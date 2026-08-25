"""Detached Git worktrees for candidate application and commit identity."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

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
        _git(self.root, "add", "--", *[item.path for item in self.proposal.manifest.mutations])
        _git(
            self.root,
            "-c",
            "user.name=RepoAgent Evolver",
            "-c",
            "user.email=evolver@repoagent.invalid",
            "commit",
            "-m",
            f"candidate {self.proposal.manifest.candidate_id}",
        )
        self.commit_sha = _git(self.root, "rev-parse", "HEAD")
        return {
            "candidate_id": self.proposal.manifest.candidate_id,
            "base_commit": self.proposal.manifest.base_commit,
            "commit_sha": self.commit_sha,
            "tree_sha": _git(self.root, "rev-parse", "HEAD^{tree}"),
            "patch_digest": self.proposal.manifest.patch_digest,
        }

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
