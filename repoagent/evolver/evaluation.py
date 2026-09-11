"""Bounded deterministic checks of immutable candidates in local Docker."""

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re
import shutil
import tempfile

from ..sandbox import DockerSandboxAdapter, SandboxConfigurationError
from ..sandbox_process import _run
from ..sandbox_session import PersistentDockerSandboxAdapter
from ..tool_execution import ToolExecutionControl
from .contracts import sha256_bytes
from .gates import GateDecision, GateObservation
from .workspace import CandidateWorkspaceError, _git, _git_bytes


class CandidateEvaluationError(RuntimeError):
    pass


def payload_digest(payload):
    return sha256_bytes(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


@dataclass(frozen=True)
class CandidateCheck:
    check_id: str
    command: str
    timeout_seconds: float = 30
    max_output_chars: int = 8000

    def __post_init__(self):
        if not isinstance(self.check_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.check_id):
            raise ValueError("invalid candidate check id")
        if not isinstance(self.command, str) or not self.command.strip() or "\0" in self.command:
            raise ValueError("candidate check command must be nonempty")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("candidate check timeout must be finite and positive")
        if type(self.max_output_chars) is not int or self.max_output_chars < 1:
            raise ValueError("candidate check output budget must be positive")


class DockerCandidateEvaluator:
    """Trusted check commands; no model calls, score inference or host fallback."""

    def __init__(self, *, executable="docker", image="python:3.12-slim", path_converter=None):
        self.executable = executable
        self.image = image
        self.path_converter = path_converter

    def descriptor(self, repo_root):
        probe = DockerSandboxAdapter(repo_root, executable=self.executable, image=self.image)
        result = _run(probe, ["image", "inspect", "--format", "{{.Id}}", self.image])
        image_id = result.stdout.strip()
        if result.returncode or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise CandidateEvaluationError("candidate evaluation image is not available locally")
        return {
            "kind": "docker-deterministic/v1", "image_id": image_id,
            "network": "none", "memory": "2g", "cpus": 2.0, "pids_limit": 256,
        }

    def evaluate(self, repo_root, identity, checks, descriptor):
        if descriptor != self.descriptor(repo_root):
            raise CandidateEvaluationError("candidate evaluator image/configuration changed")
        commit = identity["commit_sha"]
        rows = []
        directory = Path(tempfile.mkdtemp(prefix="repoagent-check-"))
        workspace = directory / "worktree"
        adapter = None
        try:
            _git(repo_root, "-c", "core.autocrlf=false", "worktree", "add", "--detach", str(workspace), commit)
            if _git(workspace, "rev-parse", "HEAD^{tree}") != identity["tree_sha"]:
                raise CandidateEvaluationError("candidate evaluation tree mismatch")
            adapter = PersistentDockerSandboxAdapter(
                workspace, executable=self.executable, image=descriptor["image_id"],
                workspace_path_converter=self.path_converter,
            )
            for check in checks:
                try:
                    outcome = adapter.execute(check.command, cwd=workspace, env={}, control=ToolExecutionControl(
                        timeout_seconds=check.timeout_seconds, max_output_chars=check.max_output_chars,
                    ))
                    status = "error" if outcome.status != "completed" or outcome.exit_code in {None, 125, 126, 127} else ("pass" if outcome.exit_code == 0 else "fail")
                    rows.append({
                        "check_id": check.check_id, "status": status,
                        "stdout": outcome.stdout, "stderr": outcome.stderr,
                        "execution": outcome.metadata(),
                    })
                except (SandboxConfigurationError, OSError) as exc:
                    rows.append({"check_id": check.check_id, "status": "error", "error_type": type(exc).__name__})
                    break
                if status == "error":
                    break
            # Check tracked sources/index after guest execution, not just before.
            if _git(workspace, "rev-parse", "HEAD") != commit or _git_bytes(
                workspace, "diff", "--no-ext-diff", "--name-only", "-z", commit, "--"
            ):
                rows[-1]["status"] = "error"
                rows[-1]["error_type"] = "CandidateSourceDrift"
        finally:
            if adapter is not None:
                try:
                    adapter.stop()
                except Exception as exc:
                    raise CandidateEvaluationError(f"candidate cleanup failed; retained worktree: {workspace}") from exc
            if workspace.exists():
                _git(repo_root, "worktree", "remove", "--force", str(workspace))
            shutil.rmtree(directory)
        return rows


def evaluation_plan(identity, checks, descriptor):
    checks = tuple(checks)
    if not checks or not all(isinstance(check, CandidateCheck) for check in checks):
        raise ValueError("candidate evaluation requires explicit checks")
    if len({check.check_id for check in checks}) != len(checks):
        raise ValueError("candidate checks must have unique identities")
    if len(checks) > 20 or sum(check.timeout_seconds for check in checks) > 300:
        raise ValueError("candidate deterministic evaluation exceeds check/time budget")
    if 2 * sum(check.max_output_chars for check in checks) > 1_000_000:
        raise ValueError("candidate deterministic evaluation exceeds output budget")
    return {
        "schema": "repoagent.candidate-check-plan/v1", "identity": dict(identity),
        "checks": [asdict(check) for check in checks], "evaluator": dict(descriptor),
    }


def decision_from_receipt(receipt):
    plan = receipt["plan"]
    requested = [check["check_id"] for check in plan["checks"]]
    rows = receipt["results"]
    observed = [row["check_id"] for row in rows]
    if len(set(observed)) != len(observed) or observed != requested[:len(observed)]:
        raise CandidateWorkspaceError("candidate check evidence identities do not match plan")
    observations = [GateObservation(row["check_id"], row["status"]) for row in rows]
    observations.extend(GateObservation(check_id, "error", "not executed") for check_id in requested[len(observed):])
    return GateDecision(
        stage="deterministic", passed=bool(observations) and all(row.status == "pass" for row in observations),
        evidence_digest=payload_digest(receipt), observations=tuple(observations),
        metrics={"commit_sha": plan["identity"]["commit_sha"], "check_n": len(requested), "reported_n": len(rows)},
    )
