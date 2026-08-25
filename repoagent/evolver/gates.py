"""Recomputable deterministic and paired promotion gates."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from types import MappingProxyType

from ..evaluation.statistics import (
    exact_mcnemar,
    paired_bootstrap_interval,
    paired_win_tie_loss,
)
from .contracts import CandidateProposal, sha256_bytes


@dataclass(frozen=True)
class GateObservation:
    gate_id: str
    status: str
    detail: str = ""

    def __post_init__(self):
        if self.status not in {"pass", "fail", "error"}:
            raise ValueError("gate observation status must be pass, fail, or error")

    def to_dict(self):
        return {"gate_id": self.gate_id, "status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class GateDecision:
    stage: str
    passed: bool
    evidence_digest: str
    observations: tuple[GateObservation, ...]
    metrics: dict = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))

    def to_dict(self):
        return {
            "stage": self.stage,
            "passed": self.passed,
            "evidence_digest": self.evidence_digest,
            "observations": [item.to_dict() for item in self.observations],
            "metrics": dict(self.metrics),
        }


class DeterministicGatePipeline:
    def __init__(self, checks=()):
        self.checks = tuple(checks)
        if not all(callable(item) for item in self.checks):
            raise TypeError("deterministic gate checks must be callable")

    def run(self, proposal, workspace, *, observed_base_commit):
        if not isinstance(proposal, CandidateProposal):
            raise TypeError("deterministic gate requires CandidateProposal")
        observations = []
        manifest = proposal.manifest
        observations.append(
            GateObservation(
                "base_commit",
                "pass" if manifest.base_commit == observed_base_commit else "fail",
                str(observed_base_commit),
            )
        )
        def mutation_matches(item):
            target = workspace / item.path
            current = workspace
            for part in item.path.split("/"):
                current = current / part
                if current.is_symlink():
                    return False
            return (
                target.is_file()
                and sha256_bytes(target.read_bytes()) == item.after_sha256
            )

        digest_ok = all(mutation_matches(item) for item in manifest.mutations)
        observations.append(
            GateObservation("mutation_digests", "pass" if digest_ok else "fail")
        )
        for index, check in enumerate(self.checks, start=1):
            gate_id = getattr(check, "gate_id", f"deterministic_{index}")
            try:
                result = check(workspace, proposal)
                if isinstance(result, tuple):
                    passed, detail = result
                else:
                    passed, detail = bool(result), ""
                observations.append(
                    GateObservation(gate_id, "pass" if passed else "fail", str(detail))
                )
            except Exception as exc:
                observations.append(
                    GateObservation(gate_id, "error", f"{type(exc).__name__}: {exc}")
                )
        payload = json.dumps(
            [item.to_dict() for item in observations], sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return GateDecision(
            stage="deterministic",
            passed=all(item.status == "pass" for item in observations),
            evidence_digest="sha256:" + hashlib.sha256(payload).hexdigest(),
            observations=tuple(observations),
        )


class PairedPromotionGate:
    def __init__(
        self,
        *,
        min_unique_tasks=10,
        min_repetitions=1,
        min_mean_lift=0.0,
        max_estimated_cost_usd=None,
        require_ci_lower=None,
    ):
        if min_unique_tasks < 1 or min_repetitions < 1:
            raise ValueError("paired gate sample thresholds must be positive")
        numeric_thresholds = (min_mean_lift,)
        if require_ci_lower is not None:
            numeric_thresholds += (require_ci_lower,)
        if max_estimated_cost_usd is not None:
            numeric_thresholds += (max_estimated_cost_usd,)
        if any(not math.isfinite(float(value)) for value in numeric_thresholds):
            raise ValueError("paired gate thresholds must be finite")
        if max_estimated_cost_usd is not None and max_estimated_cost_usd < 0:
            raise ValueError("paired gate cost threshold must be non-negative")
        self.min_unique_tasks = int(min_unique_tasks)
        self.min_repetitions = int(min_repetitions)
        self.min_mean_lift = float(min_mean_lift)
        self.max_estimated_cost_usd = max_estimated_cost_usd
        self.require_ci_lower = require_ci_lower

    def run(self, rows, *, candidate_budget, estimated_cost_usd=0.0):
        rows = tuple(dict(row) for row in rows)
        required = {
            "task_id",
            "repetition",
            "control_score",
            "treatment_score",
            "control_passed",
            "treatment_passed",
        }
        if any(not required <= set(row) for row in rows):
            raise ValueError("paired gate row is missing required fields")
        if any(
            type(row["control_passed"]) is not bool
            or type(row["treatment_passed"]) is not bool
            for row in rows
        ):
            raise TypeError("paired pass outcomes must be bool")
        identities = {(row["task_id"], int(row["repetition"])) for row in rows}
        if len(identities) != len(rows) or not rows:
            raise ValueError("paired gate rows require unique task/repetition identities")
        task_ids = sorted({str(row["task_id"]) for row in rows})
        counts = {
            task_id: sum(str(row["task_id"]) == task_id for row in rows)
            for task_id in task_ids
        }
        control = [float(row["control_score"]) for row in rows]
        treatment = [float(row["treatment_score"]) for row in rows]
        if not all(math.isfinite(value) for value in (*control, *treatment)):
            raise ValueError("paired scores must be finite")
        estimated_cost_usd = float(estimated_cost_usd)
        if not math.isfinite(estimated_cost_usd) or estimated_cost_usd < 0:
            raise ValueError("paired estimated cost must be finite and non-negative")
        control_passed = [bool(row["control_passed"]) for row in rows]
        treatment_passed = [bool(row["treatment_passed"]) for row in rows]
        wtl = paired_win_tie_loss(control, treatment)
        bootstrap = paired_bootstrap_interval(control, treatment, samples=2000, seed=0)
        mcnemar = exact_mcnemar(control_passed, treatment_passed)
        observations = [
            GateObservation(
                "minimum_unique_tasks",
                "pass" if len(task_ids) >= self.min_unique_tasks else "fail",
                f"{len(task_ids)}/{self.min_unique_tasks}",
            ),
            GateObservation(
                "minimum_repetitions",
                "pass" if all(value >= self.min_repetitions for value in counts.values()) else "fail",
                json.dumps(counts, sort_keys=True),
            ),
            GateObservation(
                "trial_budget",
                "pass" if len(rows) <= candidate_budget.max_trials else "fail",
                f"{len(rows)}/{candidate_budget.max_trials}",
            ),
            GateObservation(
                "cost_budget",
                "pass"
                if estimated_cost_usd
                <= min(
                    candidate_budget.max_estimated_cost_usd,
                    self.max_estimated_cost_usd
                    if self.max_estimated_cost_usd is not None
                    else candidate_budget.max_estimated_cost_usd,
                )
                else "fail",
                str(float(estimated_cost_usd)),
            ),
            GateObservation(
                "quality_noninferiority",
                "pass" if sum(treatment_passed) >= sum(control_passed) else "fail",
                f"{sum(treatment_passed)}/{sum(control_passed)}",
            ),
            GateObservation(
                "paired_lift",
                "pass"
                if wtl["mean_delta"] > self.min_mean_lift and wtl["wins"] > wtl["losses"]
                else "fail",
                str(wtl["mean_delta"]),
            ),
        ]
        if self.require_ci_lower is not None:
            observations.append(
                GateObservation(
                    "bootstrap_lower_bound",
                    "pass" if bootstrap["low"] >= self.require_ci_lower else "fail",
                    str(bootstrap["low"]),
                )
            )
        metrics = {
            "effective_n": len(task_ids),
            "run_n": len(rows) * 2,
            "pair_n": len(rows),
            "control_passes": sum(control_passed),
            "treatment_passes": sum(treatment_passed),
            "estimated_cost_usd": float(estimated_cost_usd),
            "win_tie_loss": wtl,
            "paired_bootstrap_95": bootstrap,
            "mcnemar": mcnemar,
        }
        evidence_payload = json.dumps(
            {"rows": rows, "metrics": metrics},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return GateDecision(
            stage="paired",
            passed=all(item.status == "pass" for item in observations),
            evidence_digest="sha256:" + hashlib.sha256(evidence_payload).hexdigest(),
            observations=tuple(observations),
            metrics=metrics,
        )


@dataclass
class TerminationTracker:
    patience: int = 5
    max_rounds: int = 20
    max_consecutive_errors: int = 3
    rounds_completed: int = 0
    consecutive_no_promotion: int = 0
    consecutive_errors: int = 0

    def __post_init__(self):
        if min(self.patience, self.max_rounds, self.max_consecutive_errors) < 1:
            raise ValueError("evolver termination limits must be positive")

    def record(self, *, promoted, errored=False):
        self.rounds_completed += 1
        if errored:
            self.consecutive_errors += 1
            return
        self.consecutive_errors = 0
        self.consecutive_no_promotion = 0 if promoted else self.consecutive_no_promotion + 1

    def decision(self):
        if self.rounds_completed >= self.max_rounds:
            return True, "max_rounds"
        if self.consecutive_errors >= self.max_consecutive_errors:
            return True, "errors_exhausted"
        if self.consecutive_no_promotion >= self.patience:
            return True, "patience_exhausted"
        return False, None


__all__ = [
    "DeterministicGatePipeline",
    "GateDecision",
    "GateObservation",
    "PairedPromotionGate",
    "TerminationTracker",
]
