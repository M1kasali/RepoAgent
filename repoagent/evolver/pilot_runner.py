"""One-shot fixed-pair pilot using the existing isolated, budgeted evaluator."""

from collections import deque
from decimal import Decimal
import json
import math
from pathlib import Path
import time

from ..atomic_io import atomic_replace_unlocked, file_lock, _fsync_directory
from .evaluation import CandidateEvaluationError, payload_digest
from .hosted_snapshot import HostedAgentSnapshotEvaluator
from .ledger import EvolutionLedger
from .model_budget import BudgetedEvaluationClient, EvaluationModelLimits
from .pilot_protocol import _outside_repository, _task, verify_frozen_pilot
from .workspace import _git


def pilot_model_identity(client):
    """Describe a trusted gateway, without making a model call."""
    if not isinstance(client, BudgetedEvaluationClient):
        raise TypeError("pilot requires a BudgetedEvaluationClient")
    descriptor = client.descriptor()
    profile = descriptor.get("profile")
    if not isinstance(profile, dict) or not profile.get("provider"):
        raise ValueError("pilot gateway requires an explicit model profile")
    return {
        "provider": profile["provider"],
        "model": descriptor["model"],
        "counter_identity": descriptor["counter_identity"],
        "configuration_digest": payload_digest(descriptor),
    }


def _write(path, value):
    atomic_replace_unlocked(
        path, json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    )


def _priced(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def _summary(rows, config):
    splits = {}
    for split in ("training", "sealed"):
        tasks = [r for r in config["tasks"] if r["split"] == split]
        scores = {
            "tasks": len(tasks),
            "baseline_passes": 0,
            "candidate_passes": 0,
            "wins": 0,
            "ties": 0,
            "losses": 0,
        }
        for task in tasks:
            arms = {
                row["arm"]: row["result"]["passed"]
                for row in rows
                if row["task_id"] == task["task_id"]
            }
            scores["baseline_passes"] += int(arms["baseline"])
            scores["candidate_passes"] += int(arms["candidate"])
            delta = int(arms["candidate"]) - int(arms["baseline"])
            scores[{1: "wins", 0: "ties", -1: "losses"}[delta]] += 1
        splits[split] = scores
    return splits


def run_frozen_pilot(
    *,
    repo_root,
    frozen_root,
    client_factory,
    approved_preflight_digest,
    actor,
    executable="docker",
):
    """Run once after an operator explicitly acknowledges the frozen preflight.

    The factory is trusted host code, must not send requests during construction,
    and must return a fresh gateway for each trial. No resume, retry, candidate
    selection or deployment is performed. Never invoke this API merely to preview.
    """
    if not callable(client_factory) or not isinstance(actor, str) or not actor.strip():
        raise ValueError("pilot requires a trusted factory and operator identity")
    repo = Path(_git(repo_root, "rev-parse", "--show-toplevel")).resolve()
    frozen = _outside_repository(frozen_root, repo)
    with file_lock(frozen / ".execution.lock", blocking=False):
        preflight = verify_frozen_pilot(frozen, repo_root=repo)
        digest = payload_digest(preflight)
        if approved_preflight_digest != digest:
            raise PermissionError("pilot spending requires the exact preflight digest")
        config = json.loads((frozen / "private-config.json").read_text())
        if payload_digest(config) != preflight["config_digest"]:
            raise CandidateEvaluationError("pilot inputs changed after verification")
        run_root = frozen / "execution"
        # Exclusive, durable reservation before constructing even a host gateway.
        # A crash at any later point leaves this fence in place for manual review.
        run_root.mkdir(mode=0o700, exist_ok=False)
        _fsync_directory(frozen)
        ledger = EvolutionLedger(run_root / "ledger.jsonl")
        rows, completed = [], 0
        attempted = 0
        known_cost = Decimal(0)
        base = {
            "schema": "repoagent.coding-pilot-execution/v1",
            "preflight_digest": digest,
            "config_digest": preflight["config_digest"],
            "actor": actor.strip(),
            "experiment_id": preflight["experiment_id"],
            "reserved_trials": preflight["reserved_trials"],
            "reserved_cost_usd": preflight["reserved_cost_usd"],
            "automatic_promotion": False,
        }

        def record(kind, payload):
            ledger.append(kind, actor=actor.strip(), payload=payload)

        record("pilot.started", base)
        try:
            probe = client_factory()
            if pilot_model_identity(probe) != preflight["model"]:
                raise CandidateEvaluationError("pilot model configuration differs")
            expected_model = probe.descriptor()
            if (
                expected_model["limits"] != preflight["limits"]
                or expected_model["pricing"] != preflight["pricing"]
            ):
                raise CandidateEvaluationError("pilot model limits or pricing differ")
            pending = deque([probe])

            def factory():
                client = pending.popleft() if pending else client_factory()
                if (
                    pilot_model_identity(client) != preflight["model"]
                    or client.descriptor() != expected_model
                    or client.evidence()["calls_reserved"] != 0
                ):
                    raise CandidateEvaluationError(
                        "pilot gateway changed or was already used"
                    )
                return client

            if probe.evidence()["calls_reserved"]:
                raise CandidateEvaluationError("pilot gateway was already used")
            limits = EvaluationModelLimits(**config["limits"])
            ordered = [
                row
                for split in ("training", "sealed")
                for row in config["tasks"]
                if row["split"] == split
            ]
            tasks = [_task(row, limits, config["intervention"]) for row in ordered]
            backend = HostedAgentSnapshotEvaluator(
                tasks,
                client_factory=factory,
                model_descriptor=expected_model,
                journal_root=run_root / "model-journals",
                executable=executable,
                image=preflight["image_id"],
            )
            descriptor = backend.descriptor(repo)
            if descriptor["image_id"] != preflight["image_id"]:
                raise CandidateEvaluationError("pilot image identity differs")
            task_descriptors = {
                row["task_id"]: row for row in preflight["task_descriptors"]
            }
            if any(
                task.descriptor() != task_descriptors[task.task_id] for task in tasks
            ):
                raise CandidateEvaluationError("pilot task configuration differs")
            _write(
                run_root / "plan.json",
                {
                    **base,
                    "backend": descriptor,
                    "task_order": [task.task_id for task in tasks],
                    "arm_order": "alternate-baseline-first-by-task-index",
                    "baseline": preflight["baseline"],
                    "candidate": preflight["candidate"],
                },
            )
            record(
                "pilot.plan_frozen",
                {
                    "plan_digest": payload_digest(
                        json.loads((run_root / "plan.json").read_text())
                    )
                },
            )
            cap = limits.max_estimated_cost_usd
            for index, (task, spec) in enumerate(zip(tasks, ordered, strict=True)):
                order = (
                    ("baseline", "candidate")
                    if index % 2 == 0
                    else ("candidate", "baseline")
                )
                for arm in order:
                    if verify_frozen_pilot(frozen, repo_root=repo) != preflight:
                        raise CandidateEvaluationError(
                            "pilot preflight changed during execution"
                        )
                    trial = {
                        "index": attempted,
                        "task_id": task.task_id,
                        "split": spec["split"],
                        "arm": arm,
                        "source": preflight[arm],
                        "reserved_cost_usd": cap,
                    }
                    record("pilot.trial_started", trial)
                    attempted += 1
                    started = time.monotonic()
                    result = backend.run_trial(
                        repo,
                        preflight[arm],
                        task.check(),
                        0,
                        descriptor,
                        cost_limit_usd=cap,
                    )
                    row = {
                        **trial,
                        "duration_seconds": time.monotonic() - started,
                        "result": result,
                    }
                    _write(run_root / f"trial-{trial['index']:03d}.json", row)
                    rows.append(row)
                    amount = result.get("estimated_cost_usd")
                    if _priced(amount):
                        known_cost += Decimal(str(amount))
                    if (
                        result.get("status") != "completed"
                        or type(result.get("passed")) is not bool
                        or not _priced(amount)
                        or amount > cap
                        or result.get("raw", {}).get("source") != preflight[arm]
                        or result.get("raw", {}).get("task") != task.descriptor()
                    ):
                        raise CandidateEvaluationError(
                            "pilot trial invalid or unpriced; review required"
                        )
                    if known_cost > Decimal(str(preflight["max_total_cost_usd"])):
                        raise CandidateEvaluationError(
                            "pilot reported cost exceeds total budget"
                        )
                    completed += 1
                    record(
                        "pilot.trial_completed",
                        {**trial, "receipt_digest": payload_digest(row)},
                    )
            if completed != preflight["reserved_trials"]:
                raise CandidateEvaluationError("pilot matrix incomplete")
            if verify_frozen_pilot(frozen, repo_root=repo) != preflight:
                raise CandidateEvaluationError(
                    "pilot preflight changed during execution"
                )
            summary = {
                **base,
                "status": "completed",
                "completed_trials": completed,
                "attempted_trials": attempted,
                "cost_complete": True,
                "known_estimated_cost_usd": str(known_cost),
                "comparison": _summary(rows, config),
                "claim": "exploratory-fixed-pair-only",
            }
            record("pilot.completed", summary)
            _write(run_root / "summary.json", summary)
            return summary
        except BaseException as exc:
            summary = {
                **base,
                "status": "needs_review",
                "error_type": type(exc).__name__,
                "completed_trials": completed,
                "attempted_trials": attempted,
                "cost_complete": False,
                "known_estimated_cost_usd": str(known_cost),
                "note": "Known cost covers priced returned trials only; inspect journals for uncertain sends. No retry or refund.",
            }
            record("pilot.failed", summary)
            _write(run_root / "summary.json", summary)
            raise
