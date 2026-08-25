"""Immutable candidate identity, mutation, budget, and provenance contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4


CANDIDATE_SCHEMA = "repoagent.evolver-candidate/v1"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class EvolutionLabel(str, Enum):
    PROMPT = "prompt"
    SKILL = "skill"
    TOOL_POLICY = "tool_policy"
    ROUTING = "routing"


@dataclass(frozen=True)
class MutationPolicy:
    label: EvolutionLabel
    exact_paths: tuple[str, ...] = ()
    path_prefixes: tuple[str, ...] = ()
    requires_human_confirmation: bool = True

    def allows(self, path):
        path = str(path)
        return path in self.exact_paths or any(
            path.startswith(prefix) for prefix in self.path_prefixes
        )


MUTATION_POLICIES = MappingProxyType(
    {
        EvolutionLabel.PROMPT: MutationPolicy(
            EvolutionLabel.PROMPT,
            exact_paths=("repoagent/prompt_prefix.py",),
        ),
        EvolutionLabel.SKILL: MutationPolicy(
            EvolutionLabel.SKILL,
            path_prefixes=("skills/",),
        ),
        EvolutionLabel.TOOL_POLICY: MutationPolicy(
            EvolutionLabel.TOOL_POLICY,
            exact_paths=("repoagent/tools.py", "repoagent/tool_contracts.py"),
        ),
        EvolutionLabel.ROUTING: MutationPolicy(
            EvolutionLabel.ROUTING,
            exact_paths=("repoagent/routing.py",),
        ),
    }
)


def safe_candidate_path(value):
    path = PurePosixPath(str(value))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("candidate mutation path must stay inside the repository")
    normalized = path.as_posix()
    if normalized.startswith((".repoagent/", ".pico/", ".git/", "sealed/")):
        raise ValueError("candidate mutation targets protected runtime or sealed state")
    return normalized


def sha256_bytes(value):
    return "sha256:" + hashlib.sha256(bytes(value)).hexdigest()


@dataclass(frozen=True)
class CandidateBudget:
    max_files: int = 1
    max_changed_bytes: int = 8192
    max_trials: int = 50
    max_estimated_cost_usd: float = 5.0

    def __post_init__(self):
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (self.max_files, self.max_changed_bytes, self.max_trials)
        ):
            raise ValueError("candidate integer budgets must be positive")
        if (
            isinstance(self.max_estimated_cost_usd, bool)
            or not isinstance(self.max_estimated_cost_usd, (int, float))
            or not math.isfinite(self.max_estimated_cost_usd)
            or self.max_estimated_cost_usd <= 0
        ):
            raise ValueError("candidate cost budget must be positive")


@dataclass(frozen=True)
class FailureEvidence:
    evidence_id: str
    task_id: str
    category: str
    summary: str
    artifact_digest: str

    def __post_init__(self):
        if not all((self.evidence_id, self.task_id, self.category, self.summary)):
            raise ValueError("failure evidence fields must not be empty")
        if not _SHA256.fullmatch(self.artifact_digest):
            raise ValueError("failure evidence requires a sha256 artifact digest")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class CandidateMutation:
    path: str
    before_sha256: str | None
    after_sha256: str
    changed_bytes: int

    def __post_init__(self):
        object.__setattr__(self, "path", safe_candidate_path(self.path))
        if self.before_sha256 is not None and not _SHA256.fullmatch(self.before_sha256):
            raise ValueError("candidate before digest must be sha256 or null")
        if not _SHA256.fullmatch(self.after_sha256):
            raise ValueError("candidate after digest must be sha256")
        if self.before_sha256 == self.after_sha256:
            raise ValueError("candidate mutation must change content")
        if self.changed_bytes < 1:
            raise ValueError("candidate changed_bytes must be positive")

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class CandidateManifest:
    candidate_id: str
    label: EvolutionLabel
    base_commit: str
    evidence_ids: tuple[str, ...]
    evidence_digest: str
    mutations: tuple[CandidateMutation, ...]
    budget: CandidateBudget
    patch_digest: str
    created_at: str
    schema: str = CANDIDATE_SCHEMA

    def __post_init__(self):
        if self.schema != CANDIDATE_SCHEMA or not self.candidate_id.startswith("candidate_"):
            raise ValueError("invalid candidate schema or id")
        if not self.base_commit or not self.evidence_ids or len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("candidate requires a base commit and unique evidence ids")
        if not _SHA256.fullmatch(self.evidence_digest) or not _SHA256.fullmatch(self.patch_digest):
            raise ValueError("candidate evidence and patch digests must be sha256")
        if not self.mutations or len({item.path for item in self.mutations}) != len(self.mutations):
            raise ValueError("candidate requires unique mutations")
        policy = MUTATION_POLICIES[self.label]
        denied = [item.path for item in self.mutations if not policy.allows(item.path)]
        if denied:
            raise ValueError(f"candidate mutation is outside {self.label.value} policy: {denied}")
        if len(self.mutations) > self.budget.max_files:
            raise ValueError("candidate exceeds its file budget")
        if sum(item.changed_bytes for item in self.mutations) > self.budget.max_changed_bytes:
            raise ValueError("candidate exceeds its byte budget")

    def to_dict(self):
        return {
            "schema": self.schema,
            "candidate_id": self.candidate_id,
            "label": self.label.value,
            "base_commit": self.base_commit,
            "evidence_ids": list(self.evidence_ids),
            "evidence_digest": self.evidence_digest,
            "mutations": [item.to_dict() for item in self.mutations],
            "budget": asdict(self.budget),
            "patch_digest": self.patch_digest,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class CandidateProposal:
    manifest: CandidateManifest
    content: Mapping[str, bytes]

    def __post_init__(self):
        values = {safe_candidate_path(path): bytes(value) for path, value in dict(self.content).items()}
        if set(values) != {item.path for item in self.manifest.mutations}:
            raise ValueError("candidate content paths must match manifest mutations")
        for mutation in self.manifest.mutations:
            if sha256_bytes(values[mutation.path]) != mutation.after_sha256:
                raise ValueError("candidate content does not match manifest digest")
        object.__setattr__(self, "content", MappingProxyType(values))


def create_manifest(*, label, base_commit, evidence, before, after, budget):
    evidence = tuple(evidence)
    paths = tuple(sorted(after))
    mutations = tuple(
        CandidateMutation(
            path=path,
            before_sha256=(sha256_bytes(before[path]) if before.get(path) is not None else None),
            after_sha256=sha256_bytes(after[path]),
            changed_bytes=max(len(before.get(path) or b""), len(after[path])),
        )
        for path in paths
    )
    evidence_payload = json.dumps(
        [item.to_dict() for item in evidence], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    patch_payload = json.dumps(
        [item.to_dict() for item in mutations], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return CandidateManifest(
        candidate_id="candidate_" + uuid4().hex,
        label=EvolutionLabel(label),
        base_commit=str(base_commit),
        evidence_ids=tuple(item.evidence_id for item in evidence),
        evidence_digest=sha256_bytes(evidence_payload),
        mutations=mutations,
        budget=budget,
        patch_digest=sha256_bytes(patch_payload),
        created_at=datetime.now(timezone.utc).isoformat(),
    )


__all__ = [
    "CANDIDATE_SCHEMA",
    "CandidateBudget",
    "CandidateManifest",
    "CandidateMutation",
    "CandidateProposal",
    "EvolutionLabel",
    "FailureEvidence",
    "MUTATION_POLICIES",
    "MutationPolicy",
    "create_manifest",
    "safe_candidate_path",
    "sha256_bytes",
]
