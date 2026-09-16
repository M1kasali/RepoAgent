"""Isolated pinned-Harness execution with scripted input and host-side grading."""

from dataclasses import dataclass
from importlib.metadata import distribution
import io
import json
from pathlib import Path, PurePosixPath
import shlex
import shutil
import stat
import tempfile
import tarfile
from types import MappingProxyType

from ..atomic_io import atomic_replace_unlocked
from ..sandbox_session import PersistentDockerSandboxAdapter
from ..tool_execution import ToolExecutionControl
from .contracts import sha256_bytes
from .evaluation import (
    CandidateCheck,
    CandidateEvaluationError,
    DockerCandidateEvaluator,
    payload_digest,
)
from .workspace import _git, _git_bytes
from .behavior_grading import BehaviorCheck, grade_behavior


def _files(values):
    result = {}
    for name, text in dict(values).items():
        if not isinstance(name, str) or not isinstance(text, str):
            raise ValueError("snapshot fixtures require text paths and contents")
        path = PurePosixPath(name)
        if (
            not path.parts
            or path.is_absolute()
            or any(part in {".", "..", ".git", ".repoagent"} for part in path.parts)
            or path.as_posix() != name
            or "\\" in name
            or "\0" in name
        ):
            raise ValueError("snapshot fixture path must stay in task workspace")
        if any(
            name.startswith(other + "/") or other.startswith(name + "/")
            for other in result
        ):
            raise ValueError("snapshot fixture paths overlap")
        result[name] = text
    if (
        len(result) > 100
        or sum(len(text.encode()) for text in result.values()) > 1_000_000
    ):
        raise ValueError("snapshot fixture exceeds file/byte budget")
    return MappingProxyType(result)


@dataclass(frozen=True)
class AgentSnapshotTask:
    task_id: str
    prompt: str
    files: dict
    responses: tuple[str, ...]
    expected_files: dict
    max_calls: int = 4
    max_output_tokens: int = 512
    timeout_seconds: float = 60
    model_mode: str = "scripted"
    enable_skills: bool = False
    enable_tests: bool = False
    behavior_checks: tuple[BehaviorCheck, ...] = ()
    behavior_files: tuple[str, ...] = ()
    native_tools: bool = False

    def __post_init__(self):
        if type(self.native_tools) is not bool:
            raise ValueError("native_tools must be boolean")
        if self.native_tools and self.model_mode != "host":
            raise ValueError("native tools require the host model channel")
        if type(self.enable_skills) is not bool:
            raise ValueError("enable_skills must be boolean")
        if type(self.enable_tests) is not bool:
            raise ValueError("enable_tests must be boolean")
        CandidateCheck(self.task_id, "snapshot", timeout_seconds=self.timeout_seconds)
        if (
            not isinstance(self.prompt, str)
            or not self.prompt.strip()
            or len(self.prompt.encode()) > 32000
        ):
            raise ValueError("snapshot task requires a bounded prompt")
        if type(self.max_calls) is not int or not 1 <= self.max_calls <= 20:
            raise ValueError("snapshot call budget must be in [1, 20]")
        if (
            type(self.max_output_tokens) is not int
            or not 1 <= self.max_output_tokens <= 8192
        ):
            raise ValueError("snapshot output token budget must be in [1, 8192]")
        if not 0 < self.timeout_seconds <= 300:
            raise ValueError("snapshot timeout exceeds trial budget")
        if isinstance(self.responses, (str, bytes)):
            raise ValueError("snapshot responses must be a sequence, not a string")
        responses = tuple(self.responses)
        if (
            (not responses and self.model_mode == "scripted")
            or len(responses) > 20
            or any(not isinstance(item, str) for item in responses)
        ):
            raise ValueError("snapshot requires a bounded response script")
        if self.model_mode not in {"scripted", "host"} or (
            self.model_mode == "host" and responses
        ):
            raise ValueError("host tasks cannot contain scripted responses")
        if sum(len(item.encode()) for item in responses) > 128000:
            raise ValueError("snapshot response script is too large")
        object.__setattr__(self, "files", _files(self.files))
        object.__setattr__(self, "expected_files", _files(self.expected_files))
        checks = tuple(self.behavior_checks)
        paths = tuple(self.behavior_files)
        if len(checks) > 20 or any(not isinstance(check, BehaviorCheck) for check in checks):
            raise ValueError("behavior checks must be bounded registered checks")
        if len({check.check_id for check in checks}) != len(checks):
            raise ValueError("behavior check identities must be unique")
        if len(set(paths)) != len(paths) or bool(paths) != bool(checks):
            raise ValueError("behavior checks require unique declared artifact paths")
        _files({name: "" for name in paths})
        object.__setattr__(self, "behavior_checks", checks)
        object.__setattr__(self, "behavior_files", paths)
        if not self.expected_files and not checks:
            raise ValueError("snapshot requires explicit host-side grading")
        object.__setattr__(self, "responses", responses)

    def worker_input(self):
        return {
            "prompt": self.prompt,
            "responses": list(self.responses),
            "max_calls": self.max_calls,
            "max_output_tokens": self.max_output_tokens,
            "enable_skills": self.enable_skills,
            **({"enable_tests": True} if self.enable_tests else {}),
            **({"native_tools": True} if self.native_tools else {}),
        }

    def descriptor(self):
        return {
            "task_id": self.task_id,
            "input_digest": payload_digest(self.worker_input()),
            "fixture_digest": payload_digest(dict(self.files)),
            "grader_digest": payload_digest(
                {"exact_files": dict(self.expected_files), "behavior_files": list(self.behavior_files),
                 "checks": [check.descriptor() for check in self.behavior_checks]}
                if self.behavior_checks else dict(self.expected_files)
            ),
            "timeout_seconds": self.timeout_seconds,
            "model_mode": self.model_mode,
        }

    def check(self):
        return CandidateCheck(
            self.task_id,
            "agent-snapshot:" + payload_digest(self.descriptor()),
            timeout_seconds=self.timeout_seconds,
            max_output_chars=64000,
        )


class ScriptedAgentSnapshotEvaluator(DockerCandidateEvaluator):
    """No credentials or network; does not implement billable inference."""

    task_mode = "scripted"

    def __init__(self, tasks, **kwargs):
        super().__init__(**kwargs)
        tasks = tuple(tasks)
        if not tasks or any(not isinstance(task, AgentSnapshotTask) for task in tasks):
            raise ValueError("snapshot evaluator requires registered tasks")
        if len({task.task_id for task in tasks}) != len(tasks):
            raise ValueError("snapshot task identities must be unique")
        if any(task.model_mode != self.task_mode for task in tasks):
            raise ValueError("task model mode differs from evaluator")
        self.tasks = MappingProxyType({task.task_id: task for task in tasks})
        self._driver = Path(__file__).with_name("agent_snapshot_guest.py").read_text()
        package = distribution("json-repair")
        self._dependency_version = package.version
        self._dependencies = {}
        for entry in package.files or ():
            path = PurePosixPath(str(entry))
            if path.is_absolute() or ".." in path.parts or "__pycache__" in path.parts:
                continue
            if path.parts[0] == "json_repair" or path.parts[0].endswith(".dist-info"):
                self._dependencies[str(path)] = Path(
                    package.locate_file(entry)
                ).read_bytes()
        if "json_repair/__init__.py" not in self._dependencies:
            raise CandidateEvaluationError(
                "json-repair source distribution is unavailable"
            )

    def checks(self):
        return tuple(task.check() for task in self.tasks.values())

    def descriptor(self, repo_root):
        return {
            **super().descriptor(repo_root),
            "worker": "scripted-agent-snapshot/v1",
            "driver_digest": sha256_bytes(self._driver.encode()),
            "tasks": [self.tasks[key].descriptor() for key in sorted(self.tasks)],
            "grader": ("host-exact-and-behavior-json/v1" if any(task.behavior_checks for task in self.tasks.values())
                       else "host-exact-text-files/v1"),
            **({"behavior_grader_digest": sha256_bytes(
                Path(__file__).with_name("behavior_grading.py").read_bytes()
            )} if any(task.behavior_checks for task in self.tasks.values()) else {}),
            "model_mode": "scripted-no-network",
            "dependency": {
                "name": "json-repair",
                "version": self._dependency_version,
                "files_digest": payload_digest(
                    {
                        name: sha256_bytes(data)
                        for name, data in self._dependencies.items()
                    }
                ),
            },
        }

    def run_trial(
        self, repo_root, identity, check, repetition, descriptor, *, cost_limit_usd
    ):
        task = self.tasks.get(check.check_id)
        if (
            task is None
            or check != task.check()
            or descriptor != self.descriptor(repo_root)
        ):
            raise CandidateEvaluationError(
                "snapshot task/evaluator differs from frozen plan"
            )
        directory = Path(tempfile.mkdtemp(prefix="repoagent-agent-snapshot-"))
        source, work = directory / "harness", directory / "task"
        adapter = None
        cleanup_failed = False
        try:
            if (
                _git(repo_root, "rev-parse", identity["commit_sha"] + "^{tree}")
                != identity["tree_sha"]
            ):
                raise CandidateEvaluationError("snapshot tree mismatch")
            paths = ["repoagent"]
            if task.enable_skills and _git(
                repo_root, "ls-tree", "--name-only", identity["commit_sha"], "skills"
            ):
                paths.append("skills")
            archive = _git_bytes(repo_root, "archive", identity["commit_sha"], *paths)
            with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                members = bundle.getmembers()
                if (
                    len(members) > 10000
                    or sum(item.size for item in members) > 64_000_000
                    or any(not (item.isfile() or item.isdir()) for item in members)
                ):
                    raise CandidateEvaluationError(
                        "snapshot requires bounded regular source files"
                    )
                bundle.extractall(source, filter="data")
            source_inventory = _inventory(source)
            dependencies = directory / "dependencies"
            for name, content in self._dependencies.items():
                target = dependencies / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            dependency_inventory = _inventory(dependencies)
            expected_modules = {
                name: {
                    "path": path,
                    "sha256": sha256_bytes(
                        _git_bytes(
                            repo_root, "show", identity["commit_sha"] + ":" + path
                        )
                    ),
                }
                for name, path in (
                    ("repoagent", "repoagent/__init__.py"),
                    ("repoagent.runtime", "repoagent/runtime.py"),
                    ("repoagent.prompt_prefix", "repoagent/prompt_prefix.py"),
                )
            }
            work.mkdir()
            for name, content in task.files.items():
                atomic_replace_unlocked(work / name, content)
            config_path = directory / "input.json"
            worker_input = json.dumps(
                self._worker_input(task), sort_keys=True, allow_nan=False
            )
            atomic_replace_unlocked(config_path, worker_input)
            adapter = PersistentDockerSandboxAdapter(
                directory,
                executable=self.executable,
                image=descriptor["image_id"],
                workspace_path_converter=self.path_converter,
            )
            outcome, model_record, model_cost = self._execute(
                adapter, work, task, check, identity, repetition, cost_limit_usd
            )
            try:
                adapter.stop()
            except Exception as exc:
                cleanup_failed = True
                raise CandidateEvaluationError(
                    f"snapshot cleanup failed; retained directory: {directory}"
                ) from exc
            adapter = None
            if (
                _inventory(source) != source_inventory
                or _inventory(dependencies) != dependency_inventory
                or not _grade(directory, {"input.json": worker_input})[0]["passed"]
            ):
                raise CandidateEvaluationError(
                    "snapshot source or worker input changed during execution"
                )
            raw = {
                "execution": outcome.metadata(),
                "stdout": outcome.stdout,
                "stderr": outcome.stderr,
                "source": dict(identity),
                "task": task.descriptor(),
                "model_mode": descriptor.get("model_mode", "scripted-no-network"),
                **model_record,
                "source_files_digest": payload_digest(source_inventory),
            }
            if (
                outcome.status != "completed"
                or outcome.exit_code != 0
                or outcome.output_truncated
            ):
                return {
                    "status": "infrastructure_error",
                    "score": None,
                    "passed": None,
                    "estimated_cost_usd": model_cost,
                    "raw": raw,
                }
            try:
                worker = json.loads(outcome.stdout)
                if (
                    worker["schema"] != "repoagent.agent-snapshot-worker/v1"
                    or worker["modules"] != expected_modules
                    or not 0 < len(worker["calls"]) <= task.max_calls
                ):
                    raise ValueError(
                        "snapshot worker identity or call evidence differs"
                    )
            except (KeyError, TypeError, ValueError) as exc:
                raise CandidateEvaluationError(
                    "snapshot worker evidence is invalid"
                ) from exc
            grades = _grade(work, task.expected_files)
            passed = (
                all(row["passed"] for row in grades)
                and worker["status"] == "completed"
                and worker["stop_reason"] == "final_answer_returned"
            )
            raw.update(worker=worker, grades=grades)
            if task.behavior_checks:
                behavior = grade_behavior(
                    work, task.behavior_files, task.behavior_checks,
                    executable=self.executable, image=descriptor["image_id"],
                    path_converter=self.path_converter,
                )
                raw["behavior"] = behavior
                if behavior["status"] != "completed":
                    return {"status": "infrastructure_error", "score": None,
                            "passed": None, "estimated_cost_usd": model_cost, "raw": raw}
                passed = passed and behavior["passed"]
            return {
                "status": "completed",
                "score": float(passed),
                "passed": passed,
                "estimated_cost_usd": model_cost,
                "raw": raw,
            }
        finally:
            if adapter is not None and not cleanup_failed:
                try:
                    adapter.stop()
                except Exception as exc:
                    raise CandidateEvaluationError(
                        f"snapshot cleanup failed; retained directory: {directory}"
                    ) from exc
            if not cleanup_failed:
                shutil.rmtree(directory)

    def _worker_input(self, task):
        return task.worker_input()

    def _execute(self, adapter, work, task, check, identity, repetition, cost_limit):
        command = (
            "python -I -B -c "
            + shlex.quote(self._driver)
            + " ../harness . ../input.json"
        )
        outcome = adapter.execute(
            command,
            cwd=work,
            env={},
            control=ToolExecutionControl(
                timeout_seconds=check.timeout_seconds,
                max_output_chars=check.max_output_chars,
            ),
        )
        return outcome, {}, 0.0


def _inventory(root):
    if root.is_symlink() or not root.is_dir():
        raise CandidateEvaluationError("snapshot root changed")
    result = {}
    total = 0
    for index, path in enumerate(root.rglob("*")):
        if index >= 10000:
            raise CandidateEvaluationError("snapshot source entry limit exceeded")
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not (
            stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
        ):
            raise CandidateEvaluationError("snapshot contains a link or special file")
        if path.is_file():
            total += info.st_size
            if len(result) >= 10000 or total > 64_000_000:
                raise CandidateEvaluationError("snapshot source size exceeded")
            result[str(path.relative_to(root))] = {
                "sha256": sha256_bytes(path.read_bytes()),
                "size": info.st_size,
                "mode": stat.S_IMODE(info.st_mode),
            }
    return result


def _grade(root, expected):
    rows = []
    for name, content in expected.items():
        target = root / name
        current = root
        safe = not root.is_symlink()
        for part in PurePosixPath(name).parts:
            current = current / part
            safe = safe and not current.is_symlink()
        expected_bytes = content.encode()
        actual = None
        if safe and target.exists() and stat.S_ISREG(target.stat().st_mode):
            if target.stat().st_size == len(expected_bytes):
                actual = target.read_bytes()
        rows.append(
            {
                "path": name,
                "passed": actual == expected_bytes,
                "expected_digest": sha256_bytes(expected_bytes),
                "actual_digest": sha256_bytes(actual) if actual is not None else None,
            }
        )
    return rows
