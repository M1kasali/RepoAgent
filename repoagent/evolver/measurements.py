"""Validate both measured arms before attribution and statistical promotion."""

from dataclasses import asdict, dataclass
import math
import re

from .evaluation import payload_digest
from .gates import GateDecision, GateObservation


@dataclass(frozen=True)
class PairedMeasurement:
    task_id: str
    repetition: int
    arm: str
    status: str
    receipt_digest: str
    score: float | None = None
    passed: bool | None = None
    estimated_cost_usd: float | None = None

    def __post_init__(self):
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("measurement task id must be nonempty")
        if type(self.repetition) is not int or self.repetition < 0:
            raise ValueError("measurement repetition must be a nonnegative integer")
        if self.arm not in {"control", "treatment"}:
            raise ValueError("measurement arm must be control or treatment")
        if self.status not in {
            "completed",
            "provider_error",
            "infrastructure_error",
            "inconclusive",
        }:
            raise ValueError("unknown measurement status")
        if not isinstance(self.receipt_digest, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.receipt_digest
        ):
            raise ValueError("measurement requires a sha256 receipt digest")
        if self.status == "completed":
            if (
                type(self.passed) is not bool
                or not _finite_nonnegative(self.score)
                or self.score > 1
            ):
                raise ValueError(
                    "completed measurement requires a bool outcome and score in [0, 1]"
                )
        elif self.score is not None or self.passed is not None:
            raise ValueError("invalid measurement must not contain a quality outcome")
        if self.estimated_cost_usd is not None and not _finite_nonnegative(
            self.estimated_cost_usd
        ):
            raise ValueError(
                "measurement cost must be finite and nonnegative or unknown"
            )


def _finite_nonnegative(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def measured_promotion(
    gate, measurements, *, task_ids, repetitions, candidate_budget, fired_tasks=None
):
    """Pure evidence reduction; receipt integrity and execution belong to the caller."""
    task_ids = tuple(task_ids)
    if not task_ids or any(
        not isinstance(item, str) or not item.strip() for item in task_ids
    ):
        raise ValueError("measurement plan requires nonempty task ids")
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("measurement plan task ids must be unique")
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError("measurement plan repetitions must be a positive integer")
    measurements = tuple(measurements)
    if any(not isinstance(item, PairedMeasurement) for item in measurements):
        raise TypeError("measured promotion requires PairedMeasurement")
    fired = None if fired_tasks is None else set(fired_tasks)
    if fired is not None and not fired <= set(task_ids):
        raise ValueError("fired tasks must belong to the measurement plan")
    expected = {
        (task, rep, arm)
        for task in task_ids
        for rep in range(repetitions)
        for arm in ("control", "treatment")
    }
    indexed = {}
    for item in measurements:
        key = (item.task_id, item.repetition, item.arm)
        if key not in expected or key in indexed:
            raise ValueError("unexpected or duplicate measurement identity")
        indexed[key] = item
    missing = sorted(expected - indexed.keys())
    invalid = sorted(key for key, item in indexed.items() if item.status != "completed")
    unpriced = sorted(
        key for key, item in indexed.items() if item.estimated_cost_usd is None
    )
    valid = not (missing or invalid or unpriced)
    try:
        total_cost = math.fsum(
            indexed[key].estimated_cost_usd or 0.0 for key in sorted(indexed)
        )
    except OverflowError as exc:
        raise ValueError("aggregate measurement cost must be finite") from exc
    if not math.isfinite(total_cost):
        raise ValueError("aggregate measurement cost must be finite")
    eligible = [task for task in task_ids if fired is None or task in fired]
    metrics = {
        "planned_pair_n": len(task_ids) * repetitions,
        "planned_run_n": len(expected),
        "reported_run_n": len(measurements),
        "missing": missing,
        "invalid": invalid,
        "unpriced": unpriced,
        "known_estimated_cost_usd": total_cost,
        "attribution_applied": valid and fired is not None,
        "eligible_tasks": eligible if valid else [],
        "unfired_excluded": [task for task in task_ids if task not in eligible]
        if valid
        else [],
    }
    observations = [
        GateObservation("measurement_validity", "pass" if valid else "error")
    ]
    if valid:
        observations.append(
            GateObservation("task_attribution", "pass" if eligible else "fail")
        )
        # Trial admission uses the full planned matrix, not the attribution subset.
        observations.append(
            GateObservation(
                "measured_trial_budget",
                "pass"
                if len(task_ids) * repetitions <= candidate_budget.max_trials
                else "fail",
            )
        )
        if eligible:
            rows = []
            for task in eligible:
                for repetition in range(repetitions):
                    control = indexed[task, repetition, "control"]
                    treatment = indexed[task, repetition, "treatment"]
                    rows.append(
                        {
                            "task_id": task,
                            "repetition": repetition,
                            "control_score": control.score,
                            "treatment_score": treatment.score,
                            "control_passed": control.passed,
                            "treatment_passed": treatment.passed,
                        }
                    )
            paired = gate.run(
                rows, candidate_budget=candidate_budget, estimated_cost_usd=total_cost
            )
            observations.extend(paired.observations)
            metrics.update(paired.metrics)
    policy = {
        name: getattr(gate, name)
        for name in (
            "min_unique_tasks",
            "min_repetitions",
            "min_mean_lift",
            "max_estimated_cost_usd",
            "require_ci_lower",
        )
    }
    evidence = {
        "schema": "repoagent.measured-promotion/v1",
        "task_ids": list(task_ids),
        "repetitions": repetitions,
        "fired_tasks": None if fired is None else sorted(fired),
        "budget": asdict(candidate_budget),
        "policy": policy,
        "measurements": [asdict(indexed[key]) for key in sorted(indexed)],
        "metrics": metrics,
        "observations": [item.to_dict() for item in observations],
    }
    return GateDecision(
        stage="paired",
        passed=all(item.status == "pass" for item in observations),
        evidence_digest=payload_digest(evidence),
        observations=tuple(observations),
        metrics=metrics,
    )
