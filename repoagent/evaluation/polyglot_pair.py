"""Strict paired comparison for Polyglot harness results."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from .schema import sha256_bytes, validate_result_payload
from .statistics import exact_mcnemar, paired_bootstrap_interval, paired_win_tie_loss


PAIRED_POLYGLOT_COMPARISON_SCHEMA = "repoagent.polyglot-paired-comparison/v1"
_REQUIRED_RUNTIME_FIELDS = (
    "provider",
    "protocol",
    "model",
    "temperature",
    "top_p",
    "max_output_tokens",
    "max_provider_calls",
    "context_token_budget",
    "context_window_tokens",
    "context_window_source",
    "sandbox_identity",
    "sandbox_isolated",
)
_REQUIRED_TASK_FIELDS = (
    "task_contract",
    "task_input_digest",
    "grader_contract",
    "grader_input_digest",
)


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return sha256_bytes(encoded)


def polyglot_runtime_pairing_identity(agent) -> dict[str, Any]:
    """Describe model and attempt limits that must be frozen across harnesses."""

    client = getattr(agent, "model_client", None)
    profile = getattr(client, "profile", None)
    sandbox = getattr(agent, "sandbox_adapter", None)
    context_window = getattr(agent, "context_window_budget", None)
    return {
        "provider": str(getattr(profile, "provider", type(client).__name__)),
        "protocol": str(getattr(profile, "protocol", "scripted")),
        "model": str(getattr(profile, "model", getattr(client, "model", ""))),
        "temperature": getattr(profile, "temperature", None),
        "top_p": getattr(profile, "top_p", None),
        "max_output_tokens": int(getattr(agent, "max_new_tokens", 0)),
        "max_provider_calls": int(getattr(agent, "max_steps", 0)),
        "context_token_budget": int(
            getattr(getattr(agent, "context_manager", None), "total_token_budget", 0)
        ),
        "context_window_tokens": int(
            getattr(context_window, "context_window_tokens", 0)
        ),
        "context_window_source": str(
            getattr(context_window, "window_source", "unknown")
        ),
        "sandbox_identity": str(getattr(sandbox, "identity", "unknown")),
        "sandbox_isolated": bool(getattr(sandbox, "is_isolated", False)),
    }


def polyglot_task_pairing_identity(instance) -> dict[str, str]:
    """Bind one pair to the same public task input and hidden grader payload."""

    runner = instance.runner
    task_payload = {
        "task_id": runner.task_id,
        "language": runner.language,
        "exercise": runner.exercise,
        "instructions": runner.instructions,
        "solution_files": list(runner.solution_files),
    }
    grader_payload = instance.grader_payload()
    grader_payload.pop("exercise_root", None)
    return {
        "task_contract": "aider-polyglot-runner-input/v1",
        "task_input_digest": _canonical_digest(task_payload),
        "grader_contract": "aider-polyglot-hidden-grader/v2",
        "grader_input_digest": _canonical_digest(grader_payload),
    }


def _single_variant(payload: Mapping[str, Any], label: str) -> str:
    variants = {str(row["variant"]) for row in payload["rows"]}
    if len(variants) != 1:
        raise ValueError(f"{label} result must contain exactly one variant")
    return variants.pop()


def _runtime_identity(payload: Mapping[str, Any], label: str) -> dict[str, Any]:
    identity = payload["design"].get("pairing_identity")
    if not isinstance(identity, Mapping):
        raise ValueError(f"{label} result has no design.pairing_identity")
    missing = [field for field in _REQUIRED_RUNTIME_FIELDS if field not in identity]
    if missing:
        raise ValueError(
            f"{label} pairing identity is missing: {', '.join(missing)}"
        )
    return dict(identity)


def _task_identity(row: Mapping[str, Any], label: str) -> dict[str, Any]:
    identity = row["verifier"].get("pairing_identity")
    if not isinstance(identity, Mapping):
        raise ValueError(
            f"{label} row {row['task_id']} repeat {row['repetition']} "
            "has no verifier.pairing_identity"
        )
    missing = [field for field in _REQUIRED_TASK_FIELDS if field not in identity]
    if missing:
        raise ValueError(
            f"{label} row pairing identity is missing: {', '.join(missing)}"
        )
    return dict(identity)


def _rows_by_pair(payload: Mapping[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {
        (str(row["task_id"]), int(row["repetition"])): row
        for row in payload["rows"]
    }


def _nested_number(row: Mapping[str, Any], path: tuple[str, ...]):
    value: Any = row["metrics"]
    for key in path:
        if not isinstance(value, Mapping) or key not in value:
            return None
        value = value[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _lower_is_better_summary(control, treatment, *, planned_pairs: int):
    deltas = [right - left for left, right in zip(control, treatment, strict=True)]
    return {
        "observed_pairs": len(deltas),
        "planned_pairs": planned_pairs,
        "coverage": len(deltas) / planned_pairs,
        "lower_is_better": True,
        "improvements": sum(delta < 0 for delta in deltas),
        "ties": sum(delta == 0 for delta in deltas),
        "regressions": sum(delta > 0 for delta in deltas),
        "treatment_minus_control": paired_bootstrap_interval(control, treatment),
    }


def compare_paired_polyglot_results(control, treatment) -> dict[str, Any]:
    """Compare two complete single-variant results under a strict frozen identity."""

    control = validate_result_payload(control)
    treatment = validate_result_payload(treatment)
    if control["model"].get("run_kind") != "live" or treatment["model"].get(
        "run_kind"
    ) != "live":
        raise ValueError("strict Polyglot pairing requires two live results")
    control_digest = str(control["benchmark"].get("definition_digest", ""))
    treatment_digest = str(treatment["benchmark"].get("definition_digest", ""))
    if not control_digest or control_digest != treatment_digest:
        raise ValueError("paired results use different benchmark definitions")

    control_variant = _single_variant(control, "control")
    treatment_variant = _single_variant(treatment, "treatment")
    if control_variant == treatment_variant:
        raise ValueError("paired results must use distinct variant names")
    control_runtime = _runtime_identity(control, "control")
    treatment_runtime = _runtime_identity(treatment, "treatment")
    if control_runtime != treatment_runtime:
        raise ValueError("paired results use different model or attempt configuration")

    control_rows = _rows_by_pair(control)
    treatment_rows = _rows_by_pair(treatment)
    if control_rows.keys() != treatment_rows.keys():
        raise ValueError("paired results have different task/repetition identities")

    paired_rows = []
    control_passed = []
    treatment_passed = []
    numeric = {
        "duration_seconds": ("duration_seconds",),
        "provider_calls": ("call_efficiency", "call_count"),
        "estimated_cost_usd": (
            "call_efficiency",
            "partial_estimated_cost_usd",
        ),
    }
    samples = {name: ([], []) for name in numeric}
    for key in sorted(control_rows):
        left = control_rows[key]
        right = treatment_rows[key]
        left_task = _task_identity(left, "control")
        right_task = _task_identity(right, "treatment")
        if left_task != right_task:
            raise ValueError(
                f"paired row {key[0]} repeat {key[1]} uses different task or grader input"
            )
        left_pass = left["status"] == "pass"
        right_pass = right["status"] == "pass"
        control_passed.append(left_pass)
        treatment_passed.append(right_pass)
        metric_deltas = {}
        for name, path in numeric.items():
            left_value = _nested_number(left, path)
            right_value = _nested_number(right, path)
            if left_value is not None and right_value is not None:
                samples[name][0].append(left_value)
                samples[name][1].append(right_value)
                metric_deltas[name] = right_value - left_value
        paired_rows.append(
            {
                "task_id": key[0],
                "repetition": key[1],
                "control_status": left["status"],
                "treatment_status": right["status"],
                "quality_delta": int(right_pass) - int(left_pass),
                "metric_deltas": metric_deltas,
                "control_error": left.get("error", ""),
                "treatment_error": right.get("error", ""),
            }
        )

    planned = len(paired_rows)
    quality = paired_win_tie_loss(control_passed, treatment_passed)
    metrics = {
        name: _lower_is_better_summary(left, right, planned_pairs=planned)
        for name, (left, right) in samples.items()
        if left
    }
    return {
        "schema": PAIRED_POLYGLOT_COMPARISON_SCHEMA,
        "benchmark_digest": control_digest,
        "runtime_pairing_identity": control_runtime,
        "control": {
            "variant": control_variant,
            "result_digest": _canonical_digest(control),
        },
        "treatment": {
            "variant": treatment_variant,
            "result_digest": _canonical_digest(treatment),
        },
        "planned_pair_n": planned,
        "observed_pair_n": planned,
        "rows": paired_rows,
        "aggregates": {
            "quality": {
                **quality,
                "control_passes": sum(control_passed),
                "treatment_passes": sum(treatment_passed),
                "exact_mcnemar": exact_mcnemar(control_passed, treatment_passed),
            },
            "efficiency": metrics,
            "status_counts": {
                "control": {
                    status: sum(row["control_status"] == status for row in paired_rows)
                    for status in ("pass", "fail", "error", "skipped")
                },
                "treatment": {
                    status: sum(row["treatment_status"] == status for row in paired_rows)
                    for status in ("pass", "fail", "error", "skipped")
                },
            },
        },
        "gate": {
            "id": "quality_noninferiority",
            "status": "pass" if not quality["losses"] else "fail",
            "observed": f"{quality['wins']}W/{quality['ties']}T/{quality['losses']}L",
            "threshold": "0 paired quality regressions",
        },
        "limitations": [
            "Efficiency summaries include only pairs where both variants reported the metric.",
            "A passing comparison establishes only this frozen workload and configuration.",
        ],
    }


def write_paired_polyglot_comparison(control, treatment, output) -> Path:
    payload = compare_paired_polyglot_results(control, treatment)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return target


__all__ = [
    "PAIRED_POLYGLOT_COMPARISON_SCHEMA",
    "compare_paired_polyglot_results",
    "polyglot_runtime_pairing_identity",
    "polyglot_task_pairing_identity",
    "write_paired_polyglot_comparison",
]
