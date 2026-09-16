"""Host-owned frozen task partition for Issue-driven strategy experiments.

Task fixtures and independent checks are curated by an operator, never inferred
from a failed patch. This adapter reuses the existing isolated snapshot runner.
"""

import json
from pathlib import Path
import re

from ..evolver.agent_snapshot import AgentSnapshotTask
from ..evolver.behavior_grading import BehaviorCheck
from .cases import digest
from .training_evidence import _identifier, reviewed_failure


def snapshot_task(row):
    return AgentSnapshotTask(
        task_id=_identifier(row["task_id"]), prompt=row["prompt"],
        files=row["files"], responses=(), expected_files={}, model_mode="host",
        native_tools=True,
        enable_skills=row.get("enable_skills", False), enable_tests=True,
        max_calls=row.get("max_calls", 12),
        max_output_tokens=row.get("max_output_tokens", 4096),
        timeout_seconds=row.get("timeout_seconds", 300),
        behavior_files=tuple(row["behavior_files"]),
        behavior_checks=tuple(BehaviorCheck(**check) for check in row["behavior_checks"]),
    )


def validate_campaign(config):
    if config.get("schema") != "repoagent.issue-campaign/v1":
        raise ValueError("unsupported Issue campaign schema")
    rows = config.get("tasks")
    if not isinstance(rows, list) or not 2 <= len(rows) <= 100:
        raise ValueError("campaign requires 2 to 100 curated tasks")
    ids, cases, sources, inputs = set(), set(), set(), set()
    families = {"training": set(), "sealed": set()}
    descriptors = []
    for row in rows:
        task = snapshot_task(row)
        case_id = _identifier(row["case_id"])
        family = _identifier(row["family"])
        split = row["split"]
        revision = row["base_revision"]
        source = row["source_issue"]
        if split not in families:
            raise ValueError("explicit task split required")
        if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", revision):
            raise ValueError("task requires exact source revision")
        if not isinstance(source, str) or not re.fullmatch(
            r"https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues/[1-9][0-9]*", source
        ):
            raise ValueError("task requires a canonical source Issue URL")
        # Fixture identity also catches the same code under a different prompt.
        fingerprint = digest(row["files"])
        for value, seen in ((task.task_id, ids), (case_id, cases),
                            (source.lower(), sources), (fingerprint, inputs)):
            if value in seen:
                raise ValueError("duplicate task, case, source Issue or fixture")
            seen.add(value)
        families[split].add(family)
        descriptors.append({"case_id": case_id, "family": family, "split": split,
                            **task.descriptor()})
    if not all(families.values()) or families["training"] & families["sealed"]:
        raise ValueError("training and sealed families must be nonempty and disjoint")
    return {"schema": "repoagent.issue-campaign-manifest/v1",
            "config_digest": digest(config), "tasks": descriptors,
            "automatic_activation": False, "claim": "exploratory_only"}


def freeze_campaign(config, output):
    manifest = validate_campaign(config)
    payload = json.dumps({"config": config, "manifest": manifest},
                         sort_keys=True, indent=2, allow_nan=False) + "\n"
    if len(payload.encode("utf-8")) > 8_000_000:
        raise ValueError("campaign exceeds byte limit")
    path = Path(output)
    with path.open("x", encoding="utf-8") as stream:
        path.chmod(0o600)
        stream.write(payload)
    return manifest


def load_campaign(path, *, expected_digest):
    with Path(path).open("rb") as stream:
        raw = stream.read(8_000_001)
    if len(raw) > 8_000_000:
        raise ValueError("campaign exceeds byte limit")
    frozen = json.loads(raw)
    manifest = validate_campaign(frozen["config"])
    if manifest != frozen["manifest"] or manifest["config_digest"] != expected_digest:
        raise ValueError("campaign changed from the pinned protocol")
    return frozen["config"]


def training_input(state, envelope, review, *, campaign, expected_digest):
    """Return reviewed failure plus executable training task, never sealed tasks."""
    manifest = validate_campaign(campaign)
    if manifest["config_digest"] != expected_digest or review.get("campaign_digest") != expected_digest:
        raise ValueError("review must bind the frozen campaign")
    matches = [row for row in campaign["tasks"] if row["case_id"] == state["case_id"]]
    if len(matches) != 1:
        raise ValueError("case is not registered in the frozen campaign")
    row = matches[0]
    value = envelope.get("observation", {})
    if row["split"] != "training" or any(
        row[key] != value.get(key) for key in ("task_id", "family", "split", "base_revision")
    ):
        raise ValueError("case partition or source identity mismatch")
    if state.get("issue", {}).get("url") != row["source_issue"]:
        raise ValueError("case source Issue does not match registered task")
    evidence = reviewed_failure(state, envelope, review)
    return evidence, snapshot_task(row)
