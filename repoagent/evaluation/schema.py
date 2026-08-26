"""Versioned evaluation result schema and reproducibility provenance."""

from __future__ import annotations

import hashlib
import json
import locale
import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..evidence import sha256_file


EVALUATION_RESULT_SCHEMA = "repoagent.evaluation-result/v1"
RUN_KINDS = frozenset({"scripted", "synthetic", "live"})
ROW_STATUSES = frozenset({"pass", "fail", "error", "skipped"})


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_path(path) -> str:
    path = Path(path)
    if path.is_file():
        return sha256_bytes(path.read_bytes())
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        relative = child.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content = child.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def _git(repo_root, *args) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def collect_source_provenance(repo_root) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    status = _git(root, "status", "--porcelain")
    material = [
        _git(root, "ls-files", "-s").encode("utf-8"),
        _git(root, "diff", "--binary", "HEAD").encode("utf-8"),
    ]
    for line in status.splitlines():
        if not line.startswith("?? "):
            continue
        untracked = root / line[3:]
        if untracked.is_file():
            material.extend((line[3:].encode("utf-8"), untracked.read_bytes()))
        elif untracked.is_dir():
            material.extend(
                (line[3:].encode("utf-8"), digest_path(untracked).encode("ascii"))
            )
    return {
        "commit_sha": _git(root, "rev-parse", "HEAD"),
        "branch": _git(root, "branch", "--show-current"),
        "dirty": bool(status),
        "tree_digest": sha256_bytes(b"\0".join(material)),
    }


def collect_environment_provenance(repo_root=None) -> dict[str, Any]:
    root = Path(repo_root or ".")
    lock = next(
        (
            root / name
            for name in ("uv.lock", "requirements.lock", "poetry.lock")
            if (root / name).is_file()
        ),
        None,
    )
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "os": platform.system(),
        "os_release": platform.release(),
        "arch": platform.machine(),
        "locale": locale.getlocale()[0] or "C",
        "executable": Path(sys.executable).name,
        "lock_digest": digest_path(lock) if lock else "",
    }


@dataclass(frozen=True)
class EvaluationRow:
    task_id: str
    variant: str
    repetition: int
    status: str
    metrics: Mapping[str, Any] = field(default_factory=dict)
    verifier: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    error: str = ""

    def __post_init__(self) -> None:
        if not self.task_id or not self.variant:
            raise ValueError("evaluation row task_id and variant must not be empty")
        if self.repetition < 0:
            raise ValueError("evaluation row repetition must be non-negative")
        if self.status not in ROW_STATUSES:
            raise ValueError(f"unsupported evaluation row status: {self.status}")
        for name in ("metrics", "verifier", "evidence"):
            if not isinstance(getattr(self, name), Mapping):
                raise TypeError(f"evaluation row {name} must be a mapping")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "variant": self.variant,
            "repetition": self.repetition,
            "status": self.status,
            "metrics": dict(self.metrics),
            "verifier": dict(self.verifier),
            "evidence": dict(self.evidence),
            "error": self.error,
        }


@dataclass
class EvaluationResult:
    experiment: Mapping[str, Any]
    source: Mapping[str, Any]
    environment: Mapping[str, Any]
    benchmark: Mapping[str, Any]
    model: Mapping[str, Any]
    design: Mapping[str, Any]
    rows: list[EvaluationRow]
    aggregates: Mapping[str, Any] = field(default_factory=dict)
    gates: list[Mapping[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    schema: str = EVALUATION_RESULT_SCHEMA

    def validate(self, *, require_evidence=False) -> None:
        if self.schema != EVALUATION_RESULT_SCHEMA:
            raise ValueError("unsupported evaluation result schema")
        required_sections = {
            "experiment": self.experiment,
            "source": self.source,
            "environment": self.environment,
            "benchmark": self.benchmark,
            "model": self.model,
            "design": self.design,
        }
        for name, value in required_sections.items():
            if not isinstance(value, Mapping) or not value:
                raise ValueError(
                    f"evaluation result {name} must be a non-empty mapping"
                )
        run_kind = str(self.model.get("run_kind", ""))
        if run_kind not in RUN_KINDS:
            raise ValueError(
                "model.run_kind must distinguish scripted, synthetic, or live"
            )
        if not self.rows:
            raise ValueError("evaluation result must contain raw rows")
        identities = set()
        for row in self.rows:
            key = (row.task_id, row.variant, row.repetition)
            if key in identities:
                raise ValueError(f"duplicate evaluation row identity: {key}")
            identities.add(key)
            if require_evidence and not row.evidence:
                raise ValueError(f"evaluation row {row.task_id} has no evidence")
        effective_n = len({row.task_id for row in self.rows})
        if int(self.aggregates.get("effective_n", effective_n)) != effective_n:
            raise ValueError("aggregates.effective_n must equal unique task count")
        if int(self.aggregates.get("run_n", len(self.rows))) != len(self.rows):
            raise ValueError("aggregates.run_n must equal raw row count")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.schema,
            "experiment": dict(self.experiment),
            "source": dict(self.source),
            "environment": dict(self.environment),
            "benchmark": dict(self.benchmark),
            "model": dict(self.model),
            "design": dict(self.design),
            "rows": [row.to_dict() for row in self.rows],
            "aggregates": dict(self.aggregates),
            "gates": [dict(gate) for gate in self.gates],
            "limitations": list(self.limitations),
        }

    def write(self, path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
        temporary.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=True)
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
        return target


def new_experiment(suite: str, experiment_id: str = "") -> dict[str, Any]:
    if not suite:
        raise ValueError("evaluation suite must not be empty")
    return {
        "id": experiment_id
        or f"{suite}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        "suite": suite,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def validate_result_payload(payload, *, require_evidence=False) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("evaluation result must be a mapping")
    rows = [EvaluationRow(**row) for row in payload.get("rows", [])]
    result = EvaluationResult(
        experiment=payload.get("experiment", {}),
        source=payload.get("source", {}),
        environment=payload.get("environment", {}),
        benchmark=payload.get("benchmark", {}),
        model=payload.get("model", {}),
        design=payload.get("design", {}),
        rows=rows,
        aggregates=payload.get("aggregates", {}),
        gates=list(payload.get("gates", [])),
        limitations=list(payload.get("limitations", [])),
        schema=str(payload.get("schema", "")),
    )
    result.validate(require_evidence=require_evidence)
    return result.to_dict()


def validate_result_evidence(payload, evidence_root) -> dict[str, Any]:
    """Verify relative evidence paths and their declared content digests."""

    validated = validate_result_payload(payload, require_evidence=True)
    root = Path(evidence_root).resolve()
    for row in validated["rows"]:
        evidence = row["evidence"]
        path_keys = [key for key in evidence if not key.endswith("sha256")]
        if not path_keys:
            raise ValueError(f"evaluation row {row['task_id']} has no evidence paths")
        for key in path_keys:
            relative = Path(str(evidence[key]))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(
                    f"evaluation row {row['task_id']} has unsafe evidence path: {relative}"
                )
            candidate = root / relative
            current = root
            for part in relative.parts:
                current /= part
                if current.is_symlink():
                    raise ValueError(
                        f"evaluation row {row['task_id']} evidence must not use symlinks"
                    )
            if not candidate.exists():
                raise ValueError(
                    f"evaluation row {row['task_id']} evidence is missing: {relative}"
                )
            if candidate.is_dir():
                candidate = candidate / "manifest.json"
                digest_key = (
                    "manifest_sha256"
                    if key == "bundle"
                    else f"{key.removesuffix('_bundle')}_manifest_sha256"
                )
            else:
                digest_key = f"{key}_sha256"
            if candidate.is_symlink():
                raise ValueError(
                    f"evaluation row {row['task_id']} evidence must not use symlinks"
                )
            if not candidate.is_file():
                raise ValueError(
                    f"evaluation row {row['task_id']} evidence file is missing: {candidate}"
                )
            declared = str(evidence.get(digest_key, ""))
            if not declared:
                raise ValueError(f"evaluation row {row['task_id']} lacks {digest_key}")
            if sha256_file(candidate) != declared:
                raise ValueError(
                    f"evaluation row {row['task_id']} evidence checksum mismatch: {relative}"
                )
    return validated


__all__ = [
    "EVALUATION_RESULT_SCHEMA",
    "EvaluationResult",
    "EvaluationRow",
    "RUN_KINDS",
    "collect_environment_provenance",
    "collect_source_provenance",
    "digest_path",
    "new_experiment",
    "validate_result_evidence",
    "validate_result_payload",
]
