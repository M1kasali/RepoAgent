"""Train/sealed firewall with blind receipts and isolated evaluation backends."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from ..atomic_io import atomic_replace


class SealedBoundaryError(RuntimeError):
    pass


_CANDIDATE_ID = re.compile(r"^candidate_[A-Za-z0-9_-]+$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def assert_disjoint_splits(training_task_ids, sealed_task_ids):
    overlap = sorted(set(training_task_ids) & set(sealed_task_ids))
    if overlap:
        raise SealedBoundaryError(f"sealed tasks leaked into training: {overlap}")


@dataclass(frozen=True)
class SealedReceipt:
    candidate_id: str
    artifact_digest: str
    task_count: int
    receipt_id: str

    def to_dict(self):
        return {
            "candidate_id": self.candidate_id,
            "artifact_digest": self.artifact_digest,
            "task_count": self.task_count,
            "receipt_id": self.receipt_id,
        }


class SealedEvaluationVault:
    def __init__(self, root, *, training_task_ids, sealed_task_ids, grader_digest):
        assert_disjoint_splits(training_task_ids, sealed_task_ids)
        self.root = Path(root).resolve()
        self._sealed_task_ids = tuple(str(item) for item in sealed_task_ids)
        if not self._sealed_task_ids:
            raise ValueError("sealed evaluation requires test tasks")
        self.training_task_ids = tuple(str(item) for item in training_task_ids)
        self.grader_digest = str(grader_digest)
        if not _SHA256.fullmatch(self.grader_digest):
            raise ValueError("sealed evaluation requires a sha256 grader digest")
        self._finished_token = ""

    def score(self, candidate_id, candidate_ref, backend, *, candidate_workspace=None):
        candidate_id = str(candidate_id)
        if not _CANDIDATE_ID.fullmatch(candidate_id):
            raise SealedBoundaryError("sealed evaluation requires a safe candidate id")
        if not str(candidate_ref):
            raise SealedBoundaryError("sealed evaluation requires a candidate reference")
        if getattr(backend, "is_isolated", None) is not True:
            raise SealedBoundaryError(
                "sealed evaluation requires an explicitly isolated backend"
            )
        if candidate_workspace is not None:
            workspace = Path(candidate_workspace).resolve()
            if workspace == self.root or self.root in workspace.parents or workspace in self.root.parents:
                raise SealedBoundaryError("candidate workspace overlaps sealed storage")
        measurements = backend.evaluate(
            candidate_ref=str(candidate_ref),
            task_ids=self._sealed_task_ids,
            grader_digest=self.grader_digest,
        )
        payload = {
            "schema": "repoagent.sealed-result/v1",
            "candidate_id": candidate_id,
            "task_ids": list(self._sealed_task_ids),
            "grader_digest": self.grader_digest,
            "measurements": measurements,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        artifact = self.root / f"{candidate_id}.json"
        atomic_replace(artifact, json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return SealedReceipt(
            candidate_id=candidate_id,
            artifact_digest=digest,
            task_count=len(self._sealed_task_ids),
            receipt_id="sealed_" + uuid4().hex,
        )

    def finish_evolution(self):
        if not self._finished_token:
            self._finished_token = "unseal_" + uuid4().hex
        return self._finished_token

    def unseal(self, token):
        if not self._finished_token or token != self._finished_token:
            raise SealedBoundaryError("sealed results are unavailable before evolution finishes")
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(self.root.glob("candidate_*.json"))
        ]


__all__ = [
    "SealedBoundaryError",
    "SealedEvaluationVault",
    "SealedReceipt",
    "assert_disjoint_splits",
]
