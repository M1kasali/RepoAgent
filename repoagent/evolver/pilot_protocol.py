"""Offline preflight for a fixed-pair coding pilot; never authorizes inference."""

from dataclasses import asdict
from decimal import Decimal
import json
from pathlib import Path
import re

from ..pricing import ModelPricing
from .agent_snapshot import AgentSnapshotTask
from .behavior_grading import BehaviorCheck
from .contracts import sha256_bytes
from .evaluation import payload_digest
from .model_budget import EvaluationModelLimits
from .workspace import _git, _git_bytes


def _keys(value, required, optional=()):
    if (
        not isinstance(value, dict)
        or not set(required) <= set(value)
        or set(value) - set(required) - set(optional)
    ):
        raise ValueError("unexpected or missing protocol fields")


def _outside_repository(path, repo_root):
    path = Path(path).resolve()
    if path == repo_root or repo_root in path.parents or path in repo_root.parents:
        raise ValueError("private pilot files must be outside the evaluated repository")
    return path


def _identity(root, ref):
    if not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", ref):
        raise ValueError("pilot sources require exact commit SHAs")
    if _git(root, "rev-parse", ref + "^{commit}") != ref:
        raise ValueError("pilot source is not a commit")
    return {"commit_sha": ref, "tree_sha": _git(root, "rev-parse", ref + "^{tree}")}


def _task(row, limits, intervention):
    _keys(
        row,
        (
            "task_id",
            "family",
            "split",
            "prompt",
            "files",
            "behavior_files",
            "behavior_checks",
        ),
    )
    if (
        row["split"] not in {"training", "sealed"}
        or not isinstance(row["family"], str)
        or not re.fullmatch(r"[a-z0-9_-]{1,80}", row["family"])
    ):
        raise ValueError("task requires a split and canonical family identity")
    checks = []
    for check in row["behavior_checks"]:
        _keys(
            check,
            ("check_id", "program", "expected_json"),
            ("timeout_seconds", "max_output_chars"),
        )
        checks.append(BehaviorCheck(**check))
    if not checks:
        raise ValueError("pilot tasks require independent behavioral checks")
    return AgentSnapshotTask(
        task_id=row["task_id"],
        prompt=row["prompt"],
        files=row["files"],
        responses=(),
        expected_files={},
        model_mode="host",
        enable_skills=intervention == "skills",
        enable_tests=True,
        max_calls=limits.max_calls,
        max_output_tokens=limits.max_output_tokens,
        timeout_seconds=300,
        behavior_files=tuple(row["behavior_files"]),
        behavior_checks=tuple(checks),
    )


def preflight_pilot(config, *, repo_root):
    """Validate trusted configuration without importing probes or calling models.

    This is a fixed candidate comparison, not a search/promotion authorization.
    Family labels and probe correctness still require human review.
    """
    root = Path(_git(repo_root, "rev-parse", "--show-toplevel")).resolve()
    _keys(
        config,
        (
            "schema",
            "experiment_id",
            "intervention",
            "baseline_commit",
            "candidate_commit",
            "image_id",
            "model",
            "limits",
            "pricing",
            "max_total_cost_usd",
            "tasks",
        ),
    )
    if config["schema"] != "repoagent.coding-pilot-config/v1":
        raise ValueError("unsupported pilot schema")
    if not isinstance(config["experiment_id"], str) or not re.fullmatch(
        r"[a-z0-9_-]{1,80}", config["experiment_id"]
    ):
        raise ValueError("invalid experiment id")
    intervention = config["intervention"]
    if intervention not in {"prompt", "skills"}:
        raise ValueError("pilot must isolate prompt or skills")
    baseline = _identity(root, config["baseline_commit"])
    candidate = _identity(root, config["candidate_commit"])
    paths = sorted(
        set(
            _git_bytes(
                root,
                "diff",
                "--no-ext-diff",
                "--no-renames",
                "--name-only",
                "-z",
                baseline["commit_sha"],
                candidate["commit_sha"],
                "--",
            )
            .decode()
            .split("\0")
        )
        - {""}
    )
    if not paths or any(
        not (
            path == "repoagent/prompt_prefix.py"
            if intervention == "prompt"
            else path.startswith("skills/")
        )
        for path in paths
    ):
        raise ValueError("source changes do not isolate the declared intervention")
    for commit in (baseline["commit_sha"], candidate["commit_sha"]):
        for path in paths:
            entry = _git_bytes(root, "ls-tree", "-z", commit, "--", path)
            if entry and entry.split(b" ", 1)[0] not in {b"100644", b"100755"}:
                raise ValueError("intervention files must be regular files")
    if not isinstance(config["image_id"], str) or not re.fullmatch(
        r"sha256:[a-f0-9]{64}", config["image_id"]
    ):
        raise ValueError("pilot requires a pinned image digest")
    model = config["model"]
    _keys(model, ("provider", "model", "configuration_digest", "counter_identity"))
    if any(
        not isinstance(v, str) or not v.strip() for v in model.values()
    ) or not re.fullmatch(r"sha256:[a-f0-9]{64}", model["configuration_digest"]):
        raise ValueError("model settings and full-request counter must be identified")
    limits = EvaluationModelLimits(**config["limits"])
    if (
        limits.max_calls > 20
        or limits.max_output_tokens > 8192
        or limits.timeout_seconds > 300
    ):
        raise ValueError("model limits exceed snapshot task bounds")
    pricing = ModelPricing(**config["pricing"])
    if any(
        value is None
        for value in (
            pricing.input_per_1m_usd,
            pricing.output_per_1m_usd,
            pricing.cache_read_per_1m_usd,
            pricing.cache_write_per_1m_usd,
        )
    ):
        raise ValueError("pilot requires complete explicit pricing")
    admission = (
        Decimal(limits.max_input_tokens)
        * Decimal(
            str(
                max(
                    pricing.input_per_1m_usd,
                    pricing.cache_read_per_1m_usd,
                    pricing.cache_write_per_1m_usd,
                )
            )
        )
        + Decimal(limits.max_output_tokens) * Decimal(str(pricing.output_per_1m_usd))
    ) / Decimal(1_000_000)
    if admission > Decimal(str(limits.max_estimated_cost_usd)):
        raise ValueError("trial budget cannot admit one fully reserved model call")
    rows = config["tasks"]
    if not isinstance(rows, list) or not 24 <= len(rows) <= 100:
        raise ValueError("pilot requires 24 to 100 tasks")
    tasks = [_task(row, limits, intervention) for row in rows]
    if len({task.task_id for task in tasks}) != len(tasks):
        raise ValueError("duplicate task identity")
    split_rows = {
        split: [row for row in rows if row["split"] == split]
        for split in ("training", "sealed")
    }
    if any(len(values) < 12 for values in split_rows.values()):
        raise ValueError("pilot requires at least 12 tasks in each split")
    families = {
        split: {row["family"] for row in values} for split, values in split_rows.items()
    }
    if families["training"] & families["sealed"]:
        raise ValueError("training and sealed problem families overlap")
    fingerprints = [
        payload_digest({"prompt": t.prompt, "files": dict(t.files)}) for t in tasks
    ]
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError("duplicate task inputs under different identities")
    cap = config["max_total_cost_usd"]
    if type(cap) not in {int, float} or not Decimal(str(cap)).is_finite() or cap <= 0:
        raise ValueError("total cost cap must be finite and positive")
    trials = 2 * len(tasks)
    reservation = Decimal(str(limits.max_estimated_cost_usd)) * trials
    if reservation > Decimal(str(cap)):
        raise ValueError("total budget cannot reserve both arms for every task")
    return {
        "schema": "repoagent.coding-pilot-preflight/v1",
        "experiment_id": config["experiment_id"],
        "config_digest": payload_digest(config),
        "implementation_digests": {
            name: sha256_bytes(Path(__file__).with_name(name).read_bytes())
            for name in (
                "pilot_protocol.py",
                "pilot_runner.py",
                "agent_snapshot.py",
                "agent_snapshot_guest.py",
                "behavior_grading.py",
                "hosted_snapshot.py",
                "model_budget.py",
                "sealed_snapshot.py",
                "finalization.py",
            )
        },
        "status": "preflight_only",
        "execution_authorized": False,
        "intervention": intervention,
        "baseline": baseline,
        "candidate": candidate,
        "changed_paths": paths,
        "image_id": config["image_id"],
        "model": dict(model),
        "limits": asdict(limits),
        "pricing": pricing.to_dict(),
        "task_counts": {split: len(values) for split, values in split_rows.items()},
        "task_descriptors": [task.descriptor() for task in tasks],
        "reserved_trials": trials,
        "max_model_calls": trials * limits.max_calls,
        "reserved_cost_usd": str(reservation),
        "max_total_cost_usd": cap,
        "analysis": {
            "primary": "paired_behavioral_pass",
            "report": ["wins", "ties", "losses", "cost", "infrastructure_failures"],
            "repetitions": 1,
            "automatic_promotion": False,
            "claim": "exploratory_pilot_only",
        },
        "limitations": [
            "Family labels and hidden probes need independent review.",
            "No significance or general coding-quality claim follows from preflight.",
            "Budget excludes candidate generation and grading compute.",
            "Model/pricing/image identity is supplied, not remotely verified.",
            "A live runner must enforce this protocol; this file alone does not enforce execution.",
        ],
    }


def freeze_pilot(config_path, *, repo_root, output_root):
    """Retain private inputs and their digest outside the evaluated checkout."""
    root = Path(_git(repo_root, "rev-parse", "--show-toplevel")).resolve()
    source = _outside_repository(config_path, root)
    output = _outside_repository(output_root, root)
    if source.stat().st_size > 8_000_000:
        raise ValueError("pilot configuration exceeds byte limit")
    config = json.loads(source.read_text(encoding="utf-8"))
    result = preflight_pilot(config, repo_root=root)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    for name, value in (("private-config.json", config), ("preflight.json", result)):
        with (output / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
        (output / name).chmod(0o600)
    return result


def verify_frozen_pilot(output_root, *, repo_root):
    """Recompute preflight from private inputs; detect drift, not malicious forgery."""
    root = Path(_git(repo_root, "rev-parse", "--show-toplevel")).resolve()
    output = _outside_repository(output_root, root)
    paths = [output / name for name in ("private-config.json", "preflight.json")]
    for path in paths:
        _outside_repository(path, root)
        if path.is_symlink() or path.stat().st_size > 8_000_000:
            raise ValueError("invalid frozen pilot file")
    config, recorded = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    actual = preflight_pilot(config, repo_root=root)
    if actual != recorded:
        raise ValueError("frozen pilot configuration or implementation changed")
    return actual
