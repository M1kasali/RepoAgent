"""Host-compared behavioral probes in a fresh, network-disabled sandbox."""

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import shlex
import stat
from tempfile import TemporaryDirectory

from ..sandbox_session import PersistentDockerSandboxAdapter
from ..tool_execution import ToolExecutionControl
from .contracts import sha256_bytes
from .evaluation import CandidateCheck, CandidateEvaluationError, payload_digest


def _canonical(text):
    return json.dumps(json.loads(text), sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class BehaviorCheck:
    check_id: str
    program: str
    expected_json: str
    timeout_seconds: float = 15
    max_output_chars: int = 16000

    def __post_init__(self):
        CandidateCheck(self.check_id, "behavior", timeout_seconds=self.timeout_seconds,
                       max_output_chars=self.max_output_chars)
        if self.timeout_seconds > 60 or self.max_output_chars > 64000:
            raise ValueError("behavior check exceeds time/output limits")
        if not isinstance(self.program, str) or not self.program.strip() or len(self.program.encode()) > 32000 or "\0" in self.program:
            raise ValueError("behavior program must be bounded nonempty Python source")
        if not isinstance(self.expected_json, str) or len(self.expected_json.encode()) > 64000:
            raise ValueError("expected JSON must be bounded text")
        object.__setattr__(self, "expected_json", _canonical(self.expected_json))

    def descriptor(self):
        return {"check_id": self.check_id, "program_digest": sha256_bytes(self.program.encode()),
                "expected_digest": sha256_bytes(self.expected_json.encode()),
                "timeout_seconds": self.timeout_seconds, "max_output_chars": self.max_output_chars}


def grade_behavior(work, paths, checks, *, executable, image, path_converter=None):
    paths, checks = tuple(paths), tuple(checks)
    if not paths or len(paths) > 100 or not checks or len(checks) > 20:
        raise ValueError("behavior grading requires bounded files and checks")
    for name in paths:
        path = PurePosixPath(name)
        if (not isinstance(name, str) or path.is_absolute() or not path.parts
                or path.as_posix() != name or "\\" in name or "\0" in name
                or any(part in {"..", ".git", ".repoagent"} for part in path.parts)):
            raise ValueError("behavior artifact must stay within workspace")
    # Snapshot only declared outputs after the Agent container has stopped.
    contents = {}
    total = 0
    for name in paths:
        target = Path(work)
        safe = not target.is_symlink()
        for part in PurePosixPath(name).parts:
            target /= part
            safe = safe and not target.is_symlink()
        try:
            info = target.lstat()
            safe = safe and stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            total += info.st_size
            if not safe or total > 1_000_000:
                raise ValueError("unsafe or oversized artifact")
            contents[name] = target.read_bytes()
        except (OSError, ValueError):
            return {"status": "completed", "passed": False, "reason": "missing_or_unsafe_artifact", "path": name, "checks": []}
    rows = []
    for check in checks:
        with TemporaryDirectory(prefix="repoagent-behavior-") as directory:
            root = Path(directory)
            for name, content in contents.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            adapter = PersistentDockerSandboxAdapter(
                root, executable=executable, image=image, workspace_path_converter=path_converter,
            )
            try:
                outcome = adapter.execute(
                    "python -I -B -c " + shlex.quote(check.program), cwd=root, env={},
                    control=ToolExecutionControl(timeout_seconds=check.timeout_seconds,
                                                 max_output_chars=check.max_output_chars),
                )
            finally:
                try:
                    adapter.stop()
                except Exception as exc:
                    raise CandidateEvaluationError("behavior grader cleanup failed") from exc
        valid = outcome.status == "completed" and not outcome.output_truncated and outcome.exit_code not in {None, 126, 127}
        actual = None
        if valid and outcome.exit_code == 0:
            try:
                actual = _canonical(outcome.stdout)
            except (ValueError, TypeError):
                pass
        passed = valid and outcome.exit_code == 0 and actual == check.expected_json
        rows.append({"check_id": check.check_id, "valid": valid, "passed": passed,
                     "execution": outcome.metadata(), "stdout": outcome.stdout, "stderr": outcome.stderr,
                     "expected_digest": check.descriptor()["expected_digest"],
                     "actual_digest": sha256_bytes(actual.encode()) if actual is not None else None})
        if not valid:
            break
    valid = len(rows) == len(checks) and all(row["valid"] for row in rows)
    return {"status": "completed" if valid else "infrastructure_error",
            "passed": all(row["passed"] for row in rows) if valid else None,
            "artifact_digest": payload_digest({name: sha256_bytes(data) for name, data in contents.items()}),
            "image_id": image, "checks": rows}
