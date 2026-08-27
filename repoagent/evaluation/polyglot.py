"""Aider Polyglot dataset adapter with deterministic canary selection."""

from __future__ import annotations

import json
import hashlib
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

from .schema import digest_path, sha256_bytes
from ..tool_execution import ToolExecutionControl


POLYGLOT_LANGUAGES = ("cpp", "go", "java", "javascript", "python", "rust")
POLYGLOT_TEST_COMMANDS = MappingProxyType(
    {
        "cpp": ("/repoagent/benchmarks/cpp-test.sh",),
        "go": ("go", "test", "./..."),
        "java": ("gradle", "test", "--offline", "--no-daemon"),
        "javascript": ("/repoagent/benchmarks/npm-test.sh",),
        "python": ("pytest",),
        "rust": ("cargo", "test", "--", "--include-ignored"),
    }
)
_NEVER_EDIT = frozenset({"CMakeLists.txt", "Cargo.toml"})


def _safe_relative(value: str, *, field: str) -> str:
    path = PurePosixPath(str(value))
    if not str(value).strip() or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Polyglot {field} path must be safe and relative: {value}")
    return path.as_posix()


def _git_commit(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _git_dirty(root: Path) -> bool | None:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip()) if result.returncode == 0 else None


def _dataset_digest(root: Path, languages: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for language in languages:
        practice = root / language / "exercises" / "practice"
        for child in sorted(item for item in practice.rglob("*") if item.is_file()):
            if child.is_symlink():
                raise ValueError(f"Polyglot dataset must not contain symlinked files: {child}")
            relative = child.relative_to(root).as_posix().encode("utf-8")
            content = child.read_bytes()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True)
class PolyglotRunnerInput:
    task_id: str
    language: str
    exercise: str
    instructions: str
    solution_files: tuple[str, ...]


@dataclass(frozen=True)
class PolyglotInstance:
    runner: PolyglotRunnerInput
    exercise_root: Path
    test_files: tuple[str, ...]
    example_files: tuple[str, ...]
    test_command: tuple[str, ...]
    metadata: Mapping[str, Any]

    def __post_init__(self):
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def grader_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.runner.task_id,
            "language": self.runner.language,
            "exercise_root": str(self.exercise_root),
            "test_files": list(self.test_files),
            "example_files": list(self.example_files),
            "test_command": list(self.test_command),
            "fixture_digest": str(self.metadata["fixture_digest"]),
        }


class PolyglotAdapter:
    """Load the official six-language benchmark without exposing grader files."""

    benchmark_id = "aider-polyglot"

    def load(self, root, *, languages=None, limit=None):
        root = Path(root).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Polyglot dataset does not exist: {root}")
        selected_languages = self._languages(languages)
        instances_by_language = {
            language: self._load_language(root, language)
            for language in selected_languages
        }
        instances = self._round_robin(instances_by_language, limit=limit)
        if not instances:
            raise ValueError("Polyglot dataset selection contains no exercises")
        ids = [instance.runner.task_id for instance in instances]
        if len(ids) != len(set(ids)):
            raise ValueError("Polyglot task IDs must be unique")
        counts = {
            language: sum(item.runner.language == language for item in instances)
            for language in selected_languages
        }
        corpus_counts = {
            language: len(instances_by_language[language])
            for language in selected_languages
        }
        return {
            "benchmark": {
                "id": self.benchmark_id,
                "version": 1,
                "definition_digest": _dataset_digest(root, selected_languages),
                "source_commit": _git_commit(root),
                "source_dirty": _git_dirty(root),
                "unique_tasks": len(instances),
                "languages": counts,
                "corpus_tasks": sum(corpus_counts.values()),
                "corpus_languages": corpus_counts,
                "selection": "sorted-language-round-robin",
            },
            "instances": tuple(instances),
        }

    @staticmethod
    def _languages(languages):
        if languages is None:
            return POLYGLOT_LANGUAGES
        requested = tuple(dict.fromkeys(str(item).strip().lower() for item in languages))
        unknown = sorted(set(requested) - set(POLYGLOT_LANGUAGES))
        if unknown:
            raise ValueError(f"unsupported Polyglot languages: {', '.join(unknown)}")
        if not requested:
            raise ValueError("Polyglot languages must not be empty")
        return tuple(language for language in POLYGLOT_LANGUAGES if language in requested)

    def _load_language(self, root: Path, language: str):
        practice = root / language / "exercises" / "practice"
        if not practice.is_dir():
            raise FileNotFoundError(f"Polyglot language directory does not exist: {practice}")
        return tuple(self._load_exercise(path, language) for path in sorted(practice.iterdir()) if path.is_dir())

    def _load_exercise(self, exercise_root: Path, language: str):
        config_path = exercise_root / ".meta" / "config.json"
        instructions_path = exercise_root / ".docs" / "instructions.md"
        if not config_path.is_file() or not instructions_path.is_file():
            raise ValueError(f"Polyglot exercise is missing config or instructions: {exercise_root}")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        files = config.get("files")
        if not isinstance(files, Mapping):
            raise ValueError(f"Polyglot exercise files must be a mapping: {exercise_root}")
        solution_files = self._file_list(files, "solution", required=True)
        test_files = self._file_list(files, "test", required=True)
        example_files = self._file_list(files, "example", required=False)
        excluded = set(test_files) | set(example_files) | _NEVER_EDIT
        editable = tuple(path for path in solution_files if path not in excluded)
        if not editable:
            raise ValueError(f"Polyglot exercise contains no editable solution files: {exercise_root}")
        for path in set(solution_files) | set(test_files) | set(example_files):
            declared = exercise_root / path
            if declared.is_symlink():
                raise ValueError(f"Polyglot dataset must not contain symlinked files: {declared}")
            if not declared.is_file():
                raise FileNotFoundError(f"Polyglot declared file does not exist safely: {declared}")
        instructions = self._instructions(exercise_root)
        task_id = f"{language}/{exercise_root.name}"
        return PolyglotInstance(
            runner=PolyglotRunnerInput(
                task_id=task_id,
                language=language,
                exercise=exercise_root.name,
                instructions=instructions,
                solution_files=editable,
            ),
            exercise_root=exercise_root,
            test_files=test_files,
            example_files=example_files,
            test_command=POLYGLOT_TEST_COMMANDS[language],
            metadata={
                "fixture_digest": digest_path(exercise_root),
                "config_digest": sha256_bytes(config_path.read_bytes()),
                "blurb": str(config.get("blurb", "")),
            },
        )

    @staticmethod
    def _file_list(files, name, *, required):
        values = files.get(name, [])
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise ValueError(f"Polyglot files.{name} must be a list of paths")
        normalized = tuple(_safe_relative(item, field=f"files.{name}") for item in values)
        if required and not normalized:
            raise ValueError(f"Polyglot files.{name} must not be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"Polyglot files.{name} contains duplicate paths")
        return normalized

    @staticmethod
    def _instructions(exercise_root: Path):
        parts = []
        for relative in (
            ".docs/introduction.md",
            ".docs/instructions.md",
            ".docs/instructions.append.md",
        ):
            path = exercise_root / relative
            if path.is_file():
                parts.append(path.read_text(encoding="utf-8").strip())
        return "\n\n".join(part for part in parts if part)

    @staticmethod
    def _round_robin(instances_by_language, *, limit):
        total = sum(len(items) for items in instances_by_language.values())
        if limit is None:
            limit = total
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("Polyglot limit must be a positive integer")
        selected = []
        offset = 0
        languages = tuple(instances_by_language)
        while len(selected) < min(limit, total):
            progressed = False
            for language in languages:
                items = instances_by_language[language]
                if offset < len(items):
                    selected.append(items[offset])
                    progressed = True
                    if len(selected) == min(limit, total):
                        break
            if not progressed:
                break
            offset += 1
        return selected


def polyglot_plan_payload(loaded) -> dict[str, Any]:
    """Return a shareable plan containing no tests, examples, or reference answers."""

    benchmark = dict(loaded["benchmark"])
    tasks = []
    for instance in loaded["instances"]:
        if not isinstance(instance, PolyglotInstance):
            raise TypeError("Polyglot plan requires PolyglotInstance entries")
        tasks.append(
            {
                "task_id": instance.runner.task_id,
                "language": instance.runner.language,
                "exercise": instance.runner.exercise,
                "solution_files": list(instance.runner.solution_files),
                "instruction_digest": sha256_bytes(instance.runner.instructions.encode("utf-8")),
                "fixture_digest": instance.metadata["fixture_digest"],
            }
        )
    return {
        "schema": "repoagent.polyglot-plan/v1",
        "benchmark": benchmark,
        "tasks": tasks,
        "execution": {
            "status": "not_run",
            "requires_isolation": True,
            "runner_grader_separated": True,
        },
    }


def prepare_polyglot_runner_workspace(instance: PolyglotInstance, destination) -> Path:
    """Copy only model-visible solution files into a fresh runner workspace."""

    if not isinstance(instance, PolyglotInstance):
        raise TypeError("Polyglot workspace preparation requires PolyglotInstance")
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(f"Polyglot runner workspace already exists: {destination}")
    destination.mkdir(parents=True)
    for relative in instance.runner.solution_files:
        source = instance.exercise_root / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return destination


def polyglot_workspace_context(instance: PolyglotInstance, destination):
    """Build a WorkspaceContext whose authority cannot escape the trial root."""

    from ..workspace import WorkspaceContext

    root = prepare_polyglot_runner_workspace(instance, destination)
    context = WorkspaceContext.build(root, repo_root_override=root)
    if Path(context.repo_root).resolve() != root:
        raise RuntimeError("Polyglot runner workspace authority escaped the trial root")
    return context


class PolyglotContainerGrader:
    """Restore hidden grading inputs and execute them in an isolated backend."""

    def __init__(self, container_runner, *, timeout_seconds=180, max_output_chars=20000):
        if getattr(container_runner, "is_isolated", None) is not True:
            raise ValueError("Polyglot grading requires an explicitly isolated container runner")
        if timeout_seconds <= 0 or max_output_chars <= 0:
            raise ValueError("Polyglot grader limits must be positive")
        self.container_runner = container_runner
        self.timeout_seconds = float(timeout_seconds)
        self.max_output_chars = int(max_output_chars)

    def grade(self, instance: PolyglotInstance, runner_workspace):
        runner_workspace = Path(runner_workspace).resolve()
        if not runner_workspace.is_dir():
            raise FileNotFoundError(f"Polyglot runner workspace does not exist: {runner_workspace}")
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="repoagent-polyglot-grade-") as temporary:
            grading_workspace = Path(temporary) / instance.runner.exercise
            shutil.copytree(instance.exercise_root, grading_workspace)
            self._enable_all_tests(instance, grading_workspace)
            for relative in instance.runner.solution_files:
                produced = runner_workspace / relative
                if produced.is_symlink() or not produced.is_file():
                    raise FileNotFoundError(f"Polyglot solution output is missing safely: {produced}")
                target = grading_workspace / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(produced, target)
            outcome = self.container_runner.execute(
                instance.test_command,
                cwd=grading_workspace,
                env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                control=ToolExecutionControl(
                    timeout_seconds=self.timeout_seconds,
                    max_output_chars=self.max_output_chars,
                ),
            )
        passed = outcome.status == "completed" and outcome.exit_code == 0
        return {
            "task_id": instance.runner.task_id,
            "passed": passed,
            "status": outcome.status,
            "exit_code": outcome.exit_code,
            "duration_seconds": round(time.monotonic() - started, 6),
            "stdout": outcome.stdout,
            "stderr": outcome.stderr,
            "stdout_chars": outcome.stdout_chars,
            "stderr_chars": outcome.stderr_chars,
            "output_truncated": outcome.output_truncated,
            "fixture_digest": instance.metadata["fixture_digest"],
        }

    @staticmethod
    def _enable_all_tests(instance, grading_workspace):
        if instance.runner.language != "java":
            return
        for relative in instance.test_files:
            if not relative.endswith(".java"):
                continue
            path = grading_workspace / relative
            content = path.read_text(encoding="utf-8")
            content = re.sub(r"@Disabled\([^)]*\)\s*\n", "", content)
            path.write_text(content, encoding="utf-8")


__all__ = [
    "POLYGLOT_LANGUAGES",
    "POLYGLOT_TEST_COMMANDS",
    "PolyglotAdapter",
    "PolyglotInstance",
    "PolyglotContainerGrader",
    "PolyglotRunnerInput",
    "polyglot_plan_payload",
    "polyglot_workspace_context",
    "prepare_polyglot_runner_workspace",
]
