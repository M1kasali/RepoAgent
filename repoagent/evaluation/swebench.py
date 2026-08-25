"""SWE-bench dataset and prediction adapter with answer isolation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .schema import digest_path


SWEBENCH_REQUIRED_FIELDS = frozenset(
    {
        "instance_id",
        "repo",
        "base_commit",
        "problem_statement",
        "patch",
        "test_patch",
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
    }
)


def _tests(value, field):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"SWE-bench {field} must be a JSON array or list") from exc
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"SWE-bench {field} must contain strings")
    return tuple(value)


@dataclass(frozen=True)
class SWEBenchRunnerInput:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    version: str = ""


@dataclass(frozen=True)
class SWEBenchInstance:
    runner: SWEBenchRunnerInput
    gold_patch: str
    test_patch: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    metadata: Mapping[str, Any]

    def __post_init__(self):
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def grader_payload(self):
        return {
            "instance_id": self.runner.instance_id,
            "repo": self.runner.repo,
            "base_commit": self.runner.base_commit,
            "patch": self.gold_patch,
            "test_patch": self.test_patch,
            "FAIL_TO_PASS": list(self.fail_to_pass),
            "PASS_TO_PASS": list(self.pass_to_pass),
        }


class SWEBenchAdapter:
    benchmark_id = "SWE-bench"

    def load(self, path):
        path = Path(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".jsonl":
            records = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            records = json.loads(text)
        if not isinstance(records, list) or not records:
            raise ValueError("SWE-bench dataset must be a non-empty JSON array or JSONL")
        instances = tuple(self.adapt(record) for record in records)
        ids = [item.runner.instance_id for item in instances]
        if len(set(ids)) != len(ids):
            raise ValueError("SWE-bench instance IDs must be unique")
        return {
            "benchmark": {
                "id": self.benchmark_id,
                "version": 1,
                "definition_digest": digest_path(path),
                "unique_tasks": len(instances),
            },
            "instances": instances,
        }

    def adapt(self, record):
        if not isinstance(record, Mapping):
            raise TypeError("SWE-bench instance must be a mapping")
        missing = sorted(SWEBENCH_REQUIRED_FIELDS - set(record))
        if missing:
            raise ValueError(f"SWE-bench instance is missing: {', '.join(missing)}")
        runner = SWEBenchRunnerInput(
            instance_id=str(record["instance_id"]),
            repo=str(record["repo"]),
            base_commit=str(record["base_commit"]),
            problem_statement=str(record["problem_statement"]),
            version=str(record.get("version", "")),
        )
        if not all((runner.instance_id, runner.repo, runner.base_commit, runner.problem_statement)):
            raise ValueError("SWE-bench runner fields must not be empty")
        return SWEBenchInstance(
            runner=runner,
            gold_patch=str(record["patch"]),
            test_patch=str(record["test_patch"]),
            fail_to_pass=_tests(record["FAIL_TO_PASS"], "FAIL_TO_PASS"),
            pass_to_pass=_tests(record["PASS_TO_PASS"], "PASS_TO_PASS"),
            metadata={
                key: record[key]
                for key in ("issue_url", "version", "difficulty")
                if key in record
            },
        )

    @staticmethod
    def prediction(instance: SWEBenchInstance, model_patch: str, model_name: str):
        return {
            "instance_id": instance.runner.instance_id,
            "model_name_or_path": str(model_name),
            "model_patch": str(model_patch),
        }


__all__ = [
    "SWEBENCH_REQUIRED_FIELDS",
    "SWEBenchAdapter",
    "SWEBenchInstance",
    "SWEBenchRunnerInput",
]
