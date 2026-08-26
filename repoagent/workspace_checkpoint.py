"""Best-effort, out-of-band workspace snapshots for completed Turns."""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path


_GIT_IDENTITY = (
    "-c",
    "user.name=RepoAgent",
    "-c",
    "user.email=checkpoint@repoagent.local",
    "-c",
    "commit.gpgsign=false",
)
_GIT_TIMEOUT_SECONDS = 30
_GC_EVERY_N_COMMITS = 50
_DEFAULT_EXCLUDES = """\
# RepoAgent out-of-band workspace checkpoints.
.repoagent/
.pico/
__pycache__/
*.pyc
*.pyo
dist/
build/
target/
*.egg-info/
.eggs/
node_modules/
.next/
.nuxt/
out/
venv/
.venv/
.tox/
.env
.env.*
*.key
*.pem
*.crt
*.p12
.aws/credentials
secrets.yaml
secrets.yml
*.log
logs/
.DS_Store
Thumbs.db
.idea/
.vscode/
"""


@dataclass(frozen=True)
class WorkspaceCheckpointResult:
    status: str
    checkpoint_id: str = ""
    edited_files: tuple[str, ...] = ()
    error: str = ""


class WorkspaceCheckpointService:
    """Snapshot a work-tree without reading or modifying its own Git metadata."""

    def __init__(
        self,
        workspace: Path,
        *,
        state_root: Path,
        shadow_dir: str = "shadow.git",
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.state_root = Path(state_root).expanduser().resolve()
        relative = Path(shadow_dir)
        candidate = (self.state_root / relative).resolve()
        if (
            relative.is_absolute()
            or candidate == self.state_root
            or not candidate.is_relative_to(self.state_root)
        ):
            raise ValueError(
                "shadow_dir must resolve strictly below the workspace state root"
            )
        self.git_dir = candidate
        self._ready = False
        self._commit_count = 0
        self._commit_lock = threading.Lock()

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (
                "git",
                f"--git-dir={self.git_dir}",
                f"--work-tree={self.workspace}",
                "-c",
                "core.quotePath=false",
                *args,
            ),
            cwd=self.workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )

    def _ensure_initialized(self) -> bool:
        if self._ready:
            return True
        if not self.workspace.is_dir():
            return False
        if not (self.git_dir / "HEAD").exists():
            self.git_dir.parent.mkdir(parents=True, exist_ok=True)
            initialized = self._git("init")
            if initialized.returncode != 0:
                return False
            try:
                (self.git_dir.parent / "NOTICE.txt").write_text(
                    "This directory contains RepoAgent's out-of-band workspace "
                    "checkpoints. It is safe to delete and will be recreated "
                    "when checkpoint policy enables it. The workspace's own "
                    "Git repository is not modified.\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
        exclude = self.git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(_DEFAULT_EXCLUDES, encoding="utf-8")
        self._git("config", "gc.auto", "256")
        self._git("config", "gc.autoDetach", "false")
        self._ready = True
        return True

    def commit_turn(self, label: str) -> WorkspaceCheckpointResult:
        """Commit changed files, degrading to evidence instead of raising."""

        with self._commit_lock:
            return self._commit_turn_unlocked(label)

    def _commit_turn_unlocked(self, label: str) -> WorkspaceCheckpointResult:
        try:
            if not self._ensure_initialized():
                return WorkspaceCheckpointResult(
                    status="unavailable", error="git initialization failed"
                )
            added = self._git("add", "-A")
            if added.returncode != 0:
                return WorkspaceCheckpointResult(
                    status="unavailable", error="git add failed"
                )
            diff = self._git("diff", "--cached", "--name-only")
            if diff.returncode != 0:
                return WorkspaceCheckpointResult(
                    status="unavailable", error="git diff failed"
                )
            edited_files = tuple(
                line for line in diff.stdout.splitlines() if line.strip()
            )
            if not edited_files:
                return WorkspaceCheckpointResult(status="unchanged")
            committed = self._git(*_GIT_IDENTITY, "commit", "-m", str(label))
            if committed.returncode != 0:
                return WorkspaceCheckpointResult(
                    status="unavailable",
                    edited_files=edited_files,
                    error="git commit failed",
                )
            head = self._git("rev-parse", "--short", "HEAD")
            if head.returncode != 0 or not head.stdout.strip():
                return WorkspaceCheckpointResult(
                    status="unavailable",
                    edited_files=edited_files,
                    error="git rev-parse failed",
                )
            self._commit_count += 1
            if self._commit_count % _GC_EVERY_N_COMMITS == 0:
                self._git("gc", "--auto")
            return WorkspaceCheckpointResult(
                status="committed",
                checkpoint_id=head.stdout.strip(),
                edited_files=edited_files,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return WorkspaceCheckpointResult(
                status="unavailable",
                error=type(exc).__name__,
            )


def checkpoint_policy_active(policy: str, *, interactive: bool) -> bool:
    if policy not in {"always", "interactive", "never"}:
        raise ValueError("checkpoint_policy must be always, interactive, or never")
    if policy == "always":
        return True
    if policy == "never":
        return False
    return bool(interactive)


__all__ = [
    "WorkspaceCheckpointResult",
    "WorkspaceCheckpointService",
    "checkpoint_policy_active",
]
