"""Host-reviewed Issue observations adapted to existing Evolver evidence.

Digests detect accidental drift, not malicious forgery. Raw Issue text, logs,
code and verifier programs stay in the private case store, outside this export.
"""

import json
from pathlib import Path
import re

from ..evolver.contracts import FailureEvidence
from .cases import digest
from .feedback import diagnose


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError("expected a canonical identifier")
    return value


def observation(state, *, task_id, family, split):
    if split not in {"training", "sealed"}:
        raise ValueError("explicit training or sealed split required")
    feedback = diagnose(state)
    if not feedback["eligible_for_manual_training_review"]:
        raise ValueError("case has no eligible completed failure")
    revision = state.get("repository", {}).get("base_revision", "")
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", revision):
        raise ValueError("case requires a fixed repository revision")
    run = state["runs"][-1]
    # Allowlist scalar facts only: no stdout, stderr, Issue body or model answer.
    facts = {
        name: {
            key: result.get(key)
            for key in ("status", "exit_code", "passed", "reproduced", "output_truncated")
        }
        for name, result in run["verification"].items()
        if name in {"baseline", "candidate"}
    }
    return {
        "schema": "repoagent.issue-observation/v1",
        "case_id": _identifier(state["case_id"]),
        "task_id": _identifier(task_id),
        "family": _identifier(family),
        "split": split,
        "base_revision": revision,
        "source_state_digest": feedback["state_digest"],
        "category": feedback["category"],
        "facts": facts,
        "evidence_pointers": feedback["evidence_pointers"],
    }


def freeze_observation(state, *, output, task_id, family, split):
    value = observation(state, task_id=task_id, family=family, split=split)
    envelope = {"observation": value, "digest": digest(value)}
    # Exclusive creation prevents silently replacing already reviewed evidence.
    path = Path(output)
    with path.open("x", encoding="utf-8") as stream:
        path.chmod(0o600)
        json.dump(envelope, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
    return envelope


def reviewed_failure(state, envelope, review):
    """Bridge into CandidateGenerator/ModelCandidateProposer after host review.

    Review is a trusted operator artifact, not a model response. It binds the
    entire observation. Callers still own dataset split/family validation.
    """
    value = envelope.get("observation")
    if (
        not isinstance(value, dict)
        or not {"task_id", "family", "split"} <= value.keys()
        or "digest" not in envelope
    ):
        raise ValueError("invalid observation envelope")
    actual = observation(
        state, task_id=value["task_id"], family=value["family"], split=value["split"]
    )
    if actual != value or digest(actual) != envelope["digest"]:
        raise ValueError("observation or source state changed; review again")
    if value["split"] != "training":
        raise ValueError("sealed evidence must not enter candidate generation")
    if (
        review.get("schema") != "repoagent.issue-training-review/v1"
        or review.get("observation_digest") != envelope["digest"]
        or review.get("approved") is not True
    ):
        raise ValueError("explicit digest-bound approval required")
    _identifier(review.get("reviewer"))
    return FailureEvidence(
        evidence_id="issue_" + envelope["digest"],
        task_id=value["task_id"],
        category=value["category"],
        summary=(
            "Host verification reproduced the known failure before repair and "
            "after applying the candidate in an independent environment. "
            "Root cause is not established; do not modify acceptance checks."
        ),
        artifact_digest="sha256:" + envelope["digest"],
    )
