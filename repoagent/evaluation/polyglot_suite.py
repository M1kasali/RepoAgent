"""Multi-task Polyglot campaign orchestration with explicit budget gates."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from ..atomic_io import LockUnavailableError, file_lock
from ..evidence import sha256_file
from ..pricing import ModelPricing
from .polyglot import PolyglotInstance
from .polyglot_campaign import PolyglotSingleTaskCampaign
from .polyglot_pair import polyglot_task_pairing_identity
from .schema import (
    EvaluationResult,
    EvaluationRow,
    collect_environment_provenance,
    collect_source_provenance,
    new_experiment,
)
from .statistics import wilson_interval
from .workspace import RawRowWriter


@dataclass(frozen=True)
class CampaignBudget:
    max_provider_calls_per_attempt: int
    max_input_tokens_per_call: int
    max_output_tokens_per_call: int
    hard_cost_cap_usd: float
    pricing: ModelPricing
    provider_probe_calls: int = 2
    provider_probe_output_tokens: int = 128

    def __post_init__(self):
        for name in (
            "max_provider_calls_per_attempt",
            "max_input_tokens_per_call",
            "max_output_tokens_per_call",
            "provider_probe_calls",
            "provider_probe_output_tokens",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(self.hard_cost_cap_usd, bool)
            or not isinstance(self.hard_cost_cap_usd, (int, float))
            or not math.isfinite(float(self.hard_cost_cap_usd))
            or self.hard_cost_cap_usd <= 0
        ):
            raise ValueError("hard_cost_cap_usd must be a finite positive number")
        if not isinstance(self.pricing, ModelPricing):
            raise TypeError("campaign budget requires an explicit ModelPricing")

    def estimate(self, planned_attempts, *, include_provider_probe=True):
        if (
            isinstance(planned_attempts, bool)
            or not isinstance(planned_attempts, int)
            or planned_attempts < 1
        ):
            raise ValueError("planned_attempts must be a positive integer")
        calls = planned_attempts * self.max_provider_calls_per_attempt
        input_tokens = calls * self.max_input_tokens_per_call
        output_tokens = calls * self.max_output_tokens_per_call
        probe_calls = self.provider_probe_calls if include_provider_probe else 0
        probe_input = probe_calls * self.max_input_tokens_per_call
        probe_output = probe_calls * self.provider_probe_output_tokens
        estimated = (
            (input_tokens + probe_input) * self.pricing.input_per_1m_usd
            + (output_tokens + probe_output) * self.pricing.output_per_1m_usd
        ) / 1_000_000
        return {
            "planned_attempts": planned_attempts,
            "max_provider_calls": calls + probe_calls,
            "max_input_tokens": input_tokens + probe_input,
            "max_output_tokens": output_tokens + probe_output,
            "estimated_worst_case_usd": estimated,
            "hard_cost_cap_usd": float(self.hard_cost_cap_usd),
            "pricing": self.pricing.to_dict(),
            "admitted": estimated <= self.hard_cost_cap_usd,
        }


class PolyglotCampaign:
    def __init__(
        self,
        *,
        repo_root,
        output_root,
        instances,
        benchmark,
        agent_factory,
        grader,
        repetitions=1,
        budget: CampaignBudget | None = None,
        require_provider_probe=True,
        require_clean_source=True,
        workspace_root=None,
    ):
        self.repo_root = Path(repo_root).resolve()
        self.output_root = Path(output_root).resolve()
        self.instances = tuple(instances)
        if not self.instances or any(
            not isinstance(instance, PolyglotInstance) for instance in self.instances
        ):
            raise TypeError("Polyglot campaign requires at least one instance")
        task_ids = [instance.runner.task_id for instance in self.instances]
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("Polyglot campaign task ids must be unique")
        if (
            isinstance(repetitions, bool)
            or not isinstance(repetitions, int)
            or repetitions < 1
        ):
            raise ValueError("Polyglot campaign repetitions must be positive")
        self.benchmark = dict(benchmark)
        self.agent_factory = agent_factory
        self.grader = grader
        self.repetitions = repetitions
        self.budget = budget
        self.require_provider_probe = bool(require_provider_probe)
        self.require_clean_source = bool(require_clean_source)
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    def run(self):
        planned = len(self.instances) * self.repetitions
        budget_evidence = (
            self.budget.estimate(
                planned,
                include_provider_probe=self.require_provider_probe,
            )
            if self.budget
            else None
        )
        if budget_evidence and not budget_evidence["admitted"]:
            raise ValueError(
                "Polyglot campaign worst-case cost exceeds hard cap: "
                f"{budget_evidence['estimated_worst_case_usd']:.6f} USD > "
                f"{budget_evidence['hard_cost_cap_usd']:.6f} USD"
            )
        lock_probe = PolyglotSingleTaskCampaign(
            repo_root=self.repo_root,
            output_root=self.output_root / ".lock-probe",
            instance=self.instances[0],
            benchmark=self.benchmark,
            agent_factory=self.agent_factory,
            grader=self.grader,
            require_provider_probe=self.require_provider_probe,
        )
        lock = (
            file_lock(lock_probe._campaign_lock_path(), blocking=False)
            if self.require_provider_probe
            else _NullLock()
        )
        try:
            with lock:
                return self._run_locked(planned, budget_evidence)
        except LockUnavailableError as exc:
            raise RuntimeError(
                "this benchmark already has an active paid campaign"
            ) from exc

    def _run_locked(self, planned, budget_evidence):
        source = collect_source_provenance(self.repo_root)
        if self.require_clean_source:
            if source.get("dirty"):
                raise ValueError("formal Polyglot campaign requires a clean worktree")
            if not str(source.get("commit_sha", "")).strip():
                raise ValueError("formal Polyglot campaign requires a Git commit")
        if self.output_root.exists():
            raise FileExistsError(
                f"Polyglot campaign output already exists: {self.output_root}"
            )
        self.output_root.mkdir(parents=True)
        rows = []
        first_result = None
        stop_reason = ""
        for instance in self.instances:
            for repetition in range(self.repetitions):
                relative = (
                    Path("attempts")
                    / instance.runner.task_id.replace("/", "__")
                    / f"repeat-{repetition}"
                )
                if stop_reason:
                    row = self._skipped_row(
                        instance.runner.task_id,
                        repetition,
                        relative,
                        stop_reason,
                    )
                else:
                    result = PolyglotSingleTaskCampaign(
                        repo_root=self.repo_root,
                        output_root=self.output_root / relative,
                        instance=instance,
                        benchmark=self.benchmark,
                        agent_factory=self.agent_factory,
                        grader=self.grader,
                        require_provider_probe=self.require_provider_probe,
                        require_clean_source=self.require_clean_source,
                        repetition=repetition,
                        manage_paid_lock=False,
                        workspace_root=(
                            self.workspace_root / relative
                            if self.workspace_root is not None
                            else None
                        ),
                    ).run()
                    first_result = first_result or result
                    row = self._prefix_evidence(result.rows[0], relative)
                    if row.status == "error":
                        category = str(
                            row.verifier.get("failure_category")
                            or "unknown_infrastructure_error"
                        )
                        stop_reason = (
                            f"infrastructure error after {row.task_id}: {category}"
                        )
                rows.append(row)
                RawRowWriter(self.output_root / "rows.jsonl").append(row)
        if first_result is None:
            raise RuntimeError("Polyglot campaign produced no executable attempts")
        source_after = collect_source_provenance(self.repo_root)
        result = self._result(
            rows,
            first_result,
            planned,
            budget_evidence,
            source,
            source_after,
        )
        result.write(self.output_root / "results.json")
        return result

    def _skipped_row(self, task_id, repetition, relative, reason):
        instance = next(
            item for item in self.instances if item.runner.task_id == task_id
        )
        path = self.output_root / relative / "skip.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"status": "skipped", "reason": reason}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return EvaluationRow(
            task_id=task_id,
            variant="repoagent-harness",
            repetition=repetition,
            status="skipped",
            verifier={
                "passed": False,
                "failure_category": "campaign_aborted",
                "pairing_identity": polyglot_task_pairing_identity(instance),
            },
            evidence={
                "skip": path.relative_to(self.output_root).as_posix(),
                "skip_sha256": sha256_file(path),
            },
            error=reason,
        )

    @staticmethod
    def _prefix_evidence(row, relative):
        evidence = {
            key: (
                value if key.endswith("sha256") else (relative / str(value)).as_posix()
            )
            for key, value in row.evidence.items()
        }
        return EvaluationRow(
            task_id=row.task_id,
            variant=row.variant,
            repetition=row.repetition,
            status=row.status,
            metrics=row.metrics,
            verifier=row.verifier,
            evidence=evidence,
            error=row.error,
        )

    def _result(
        self,
        rows,
        first,
        planned,
        budget_evidence,
        source,
        source_after,
    ):
        passed = sum(row.status == "pass" for row in rows)
        executed = sum(row.status != "skipped" for row in rows)
        code_passes = sum(bool(row.verifier.get("code_passed")) for row in rows)
        converged = sum(bool(row.verifier.get("turn_converged")) for row in rows)
        call_counts = [
            int(row.metrics.get("call_efficiency", {}).get("call_count", 0))
            for row in rows
            if row.status != "skipped"
        ]
        partial_cost = sum(
            float(
                row.metrics.get("call_efficiency", {}).get(
                    "partial_estimated_cost_usd", 0.0
                )
            )
            for row in rows
            if row.status != "skipped"
        )
        cost_complete = all(
            bool(row.metrics.get("call_efficiency", {}).get("cost_complete", False))
            for row in rows
            if row.status != "skipped"
        )
        source_stable = source_after == source
        per_attempt_calls_ok = not self.budget or all(
            count <= self.budget.max_provider_calls_per_attempt for count in call_counts
        )
        actual_cost_ok = not self.budget or (
            cost_complete and partial_cost <= self.budget.hard_cost_cap_usd
        )
        model = dict(first.model)
        model["provider_preflight_cache_reused"] = any(
            bool(row.metrics.get("provider_preflight_cache_hit")) for row in rows
        )
        return EvaluationResult(
            experiment=new_experiment("aider-polyglot-campaign"),
            source=source,
            environment=collect_environment_provenance(self.repo_root),
            benchmark={
                **self.benchmark,
                "unique_tasks": len(self.instances),
                "selected_task_ids": [item.runner.task_id for item in self.instances],
            },
            model=model,
            design={
                "variants": ["repoagent-harness"],
                "repetitions": self.repetitions,
                "paired": False,
                "planned_attempts": planned,
                "budget": budget_evidence or {"status": "not_configured"},
                "pairing_identity": dict(first.design["pairing_identity"]),
            },
            rows=rows,
            aggregates={
                "effective_n": len(self.instances),
                "run_n": len(rows),
                "planned_run_n": planned,
                "executed_run_n": executed,
                "skipped_run_n": len(rows) - executed,
                "passes": passed,
                "pass_rate": passed / len(rows),
                "pass_rate_wilson_95": wilson_interval(passed, len(rows)),
                "code_passes": code_passes,
                "converged_turns": converged,
                "provider_call_count": sum(call_counts),
                "max_provider_calls_in_attempt": max(call_counts, default=0),
                "partial_estimated_cost_usd": partial_cost,
                "cost_complete": cost_complete,
            },
            gates=[
                {
                    "id": "planned_denominator_complete",
                    "status": "pass" if len(rows) == planned else "fail",
                    "observed": f"{len(rows)}/{planned}",
                    "threshold": f"{planned}/{planned}",
                },
                {
                    "id": "budget_admission",
                    "status": "pass" if budget_evidence else "not_run",
                    "observed": (
                        str(budget_evidence["estimated_worst_case_usd"])
                        if budget_evidence
                        else "not configured"
                    ),
                    "threshold": (
                        str(budget_evidence["hard_cost_cap_usd"])
                        if budget_evidence
                        else "not configured"
                    ),
                },
                {
                    "id": "per_attempt_call_budget",
                    "status": (
                        "pass"
                        if self.budget and per_attempt_calls_ok
                        else "fail"
                        if self.budget
                        else "not_run"
                    ),
                    "observed": str(max(call_counts, default=0)),
                    "threshold": (
                        str(self.budget.max_provider_calls_per_attempt)
                        if self.budget
                        else "not configured"
                    ),
                },
                {
                    "id": "actual_cost_cap",
                    "status": (
                        "pass"
                        if self.budget and actual_cost_ok
                        else "fail"
                        if self.budget
                        else "not_run"
                    ),
                    "observed": (
                        f"{partial_cost:.8f} USD; complete={str(cost_complete).lower()}"
                    ),
                    "threshold": (
                        f"<= {self.budget.hard_cost_cap_usd:.8f} USD with complete pricing"
                        if self.budget
                        else "not configured"
                    ),
                },
                {
                    "id": "source_integrity",
                    "status": "pass" if source_stable else "fail",
                    "observed": "stable"
                    if source_stable
                    else "changed during campaign",
                    "threshold": "stable",
                },
            ],
            limitations=(
                [
                    "Rows marked skipped remain in the planned denominator.",
                    "A pre-run worst-case budget is not an account-level spend reservation.",
                    "Incomplete provider pricing fails the actual-cost gate instead of assuming zero cost.",
                ]
                + (
                    [
                        "Scripted campaigns validate orchestration and evidence, not model coding quality."
                    ]
                    if model.get("run_kind") == "scripted"
                    else []
                )
            ),
        )


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


__all__ = ["CampaignBudget", "PolyglotCampaign"]
