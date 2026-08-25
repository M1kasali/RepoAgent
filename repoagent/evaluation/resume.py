"""Resume-safe claim generation from verified tagged release evidence only."""

from __future__ import annotations

import json
from pathlib import Path

from .release import verify_release_bundle


def resume_claims_from_release(path):
    root = Path(path).resolve()
    manifest = verify_release_bundle(root, require_tag=True)
    result = json.loads((root / "results.json").read_text(encoding="utf-8"))
    aggregates = result["aggregates"]
    return {
        "schema": "repoagent.resume-claims/v1",
        "release_tag": manifest["release_tag"],
        "commit_sha": manifest["commit_sha"],
        "workload": {
            "benchmark_id": result["benchmark"]["id"],
            "benchmark_digest": result["benchmark"]["definition_digest"],
            "effective_n": aggregates["effective_n"],
            "run_n": aggregates["run_n"],
        },
        "model": {
            "run_kind": result["model"]["run_kind"],
            "provider": result["model"].get("provider", ""),
            "model": result["model"].get("model", ""),
        },
        "metrics": {
            key: value
            for key, value in aggregates.items()
            if key not in {"effective_n", "run_n"}
        },
        "limitations": list(result.get("limitations", [])),
    }


def render_resume_claims_markdown(claims):
    workload = claims["workload"]
    model = claims["model"]
    metrics = claims["metrics"]
    lines = [
        "# Release-backed Resume Claims",
        "",
        f"- Release: `{claims['release_tag']}`",
        f"- Commit: `{claims['commit_sha']}`",
        f"- Workload: `{workload['benchmark_id']}` "
        f"({workload['effective_n']} unique tasks, {workload['run_n']} runs)",
        f"- Model: `{model['run_kind']}/{model['provider']}/{model['model']}`",
        f"- Metrics: `{json.dumps(metrics, sort_keys=True)}`",
        "- Limitations: " + "; ".join(claims["limitations"]),
    ]
    return "\n".join(lines)


__all__ = ["render_resume_claims_markdown", "resume_claims_from_release"]
