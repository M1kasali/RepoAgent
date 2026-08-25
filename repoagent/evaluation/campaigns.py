"""Campaign adapters that emit the unified evaluation-result schema."""

from __future__ import annotations

from pathlib import Path

from ..evidence import EvidenceBundleBuilder, sha256_file
from ..run_store import RunStore
from .evaluator import BenchmarkEvaluator
from .schema import (
    EvaluationResult,
    EvaluationRow,
    collect_environment_provenance,
    collect_source_provenance,
    digest_path,
    new_experiment,
)
from .statistics import (
    exact_mcnemar,
    paired_bootstrap_interval,
    paired_win_tie_loss,
    wilson_interval,
)
from .workspace import RawRowWriter


PAIRED_CAMPAIGN_KINDS = ("context", "memory", "cost", "recovery")


class RuntimeContractCampaign:
    def __init__(self, *, repo_root, benchmark_path, output_root, model_client_factory=None):
        self.repo_root = Path(repo_root).resolve()
        self.benchmark_path = Path(benchmark_path).resolve()
        self.output_root = Path(output_root).resolve()
        self.model_client_factory = model_client_factory

    def run(self) -> EvaluationResult:
        self.output_root.mkdir(parents=True, exist_ok=True)
        workspace_root = self.output_root / "workspaces"
        legacy_artifact = self.output_root / "contract-source.json"
        artifact = BenchmarkEvaluator(
            benchmark_path=self.benchmark_path,
            artifact_path=legacy_artifact,
            workspace_root=workspace_root,
            model_client_factory=self.model_client_factory,
        ).run()
        rows = []
        writer = RawRowWriter(self.output_root / "rows.jsonl")
        for source_row in artifact["rows"]:
            run_dir = workspace_root / source_row["run_dir_relpath"]
            bundle = self.output_root / "evidence" / source_row["id"]
            EvidenceBundleBuilder(RunStore(run_dir.parent)).build(
                source_row["run_id"], bundle
            )
            row = EvaluationRow(
                task_id=source_row["id"],
                variant="runtime-contract",
                repetition=0,
                status="pass" if source_row["passed"] else "fail",
                metrics={
                    "tool_steps": source_row["tool_steps"],
                    "attempts": source_row["attempts"],
                    "within_budget": source_row["within_budget"],
                },
                verifier={
                    "passed": source_row["verifier_passed"],
                    "exit_code": source_row["verifier_exit_code"],
                    "failure_category": source_row["failure_category"],
                },
                evidence={
                    "bundle": bundle.relative_to(self.output_root).as_posix(),
                    "manifest_sha256": sha256_file(bundle / "manifest.json"),
                },
            )
            writer.append(row)
            rows.append(row)
        passed = sum(row.status == "pass" for row in rows)
        result = EvaluationResult(
            experiment=new_experiment("runtime-contract"),
            source=collect_source_provenance(self.repo_root),
            environment=collect_environment_provenance(self.repo_root),
            benchmark={
                "id": "repoagent-runtime-contract",
                "version": artifact.get("schema_version", 1),
                "definition_digest": digest_path(self.benchmark_path),
                "unique_tasks": len(rows),
            },
            model={
                "run_kind": "scripted" if self.model_client_factory is None else "live",
                "provider": "scripted" if self.model_client_factory is None else "external",
                "model": artifact["reproducibility"]["model_name"],
                "temperature": artifact["reproducibility"]["decoding"]["temperature"],
            },
            design={"variants": ["runtime-contract"], "repetitions": 1, "paired": False},
            rows=rows,
            aggregates={
                "effective_n": len(rows),
                "run_n": len(rows),
                "passes": passed,
                "pass_rate": passed / len(rows),
                "pass_rate_wilson_95": wilson_interval(passed, len(rows)),
            },
            gates=[
                {
                    "id": "all_contracts_pass",
                    "status": "pass" if passed == len(rows) else "fail",
                    "observed": f"{passed}/{len(rows)}",
                    "threshold": f"{len(rows)}/{len(rows)}",
                }
            ],
            limitations=["Scripted runtime-contract campaign; not a coding-quality claim."],
        )
        result.validate(require_evidence=True)
        result.write(self.output_root / "results.json")
        return result


def paired_campaign_result(
    paired_artifact,
    *,
    repo_root,
    benchmark_id,
    benchmark_digest,
    model,
    experiment_id="",
):
    rows = [
        EvaluationRow(
            task_id=row["task_id"],
            variant=row["variant"],
            repetition=int(row["repetition"]),
            status="pass" if row["passed"] else "fail",
            metrics={"score": row["score"], **dict(row.get("metrics", {}))},
            verifier={"passed": row["passed"], "reason": row.get("reason", "")},
        )
        for row in paired_artifact["rows"]
    ]
    control_name = paired_artifact["design"]["control"]
    treatment_name = paired_artifact["design"]["treatment"]
    control = [row for row in rows if row.variant == control_name]
    treatment = [row for row in rows if row.variant == treatment_name]
    result = EvaluationResult(
        experiment=new_experiment("paired", experiment_id),
        source=collect_source_provenance(repo_root),
        environment=collect_environment_provenance(repo_root),
        benchmark={
            "id": benchmark_id,
            "version": 1,
            "definition_digest": benchmark_digest,
            "unique_tasks": len({row.task_id for row in rows}),
        },
        model=dict(model),
        design={**paired_artifact["design"], "paired": True},
        rows=rows,
        aggregates={
            "effective_n": len({row.task_id for row in rows}),
            "run_n": len(rows),
            "pair_n": len(control),
            "score": paired_win_tie_loss(
                [row.metrics["score"] for row in control],
                [row.metrics["score"] for row in treatment],
            ),
            "score_delta_bootstrap_95": paired_bootstrap_interval(
                [row.metrics["score"] for row in control],
                [row.metrics["score"] for row in treatment],
            ),
            "pass_mcnemar": exact_mcnemar(
                [row.status == "pass" for row in control],
                [row.status == "pass" for row in treatment],
            ),
        },
        limitations=["Paired result quality is bounded by the supplied isolated grader."],
    )
    result.validate()
    return result


def paired_campaign_matrix(
    artifacts,
    *,
    repo_root,
    benchmark_digests,
    model,
):
    missing = [kind for kind in PAIRED_CAMPAIGN_KINDS if kind not in artifacts]
    if missing:
        raise ValueError(f"paired campaign matrix is missing: {', '.join(missing)}")
    return {
        kind: paired_campaign_result(
            artifacts[kind],
            repo_root=repo_root,
            benchmark_id=f"repoagent-{kind}-paired",
            benchmark_digest=benchmark_digests[kind],
            model=model,
            experiment_id=f"{kind}-paired",
        )
        for kind in PAIRED_CAMPAIGN_KINDS
    }


def fault_campaign_result(
    fault_rows,
    *,
    repo_root,
    benchmark_digest,
    model,
):
    rows = [
        EvaluationRow(
            task_id=str(row["boundary"]),
            variant="fault-injected",
            repetition=0,
            status=str(row["status"]),
            metrics={
                "detected": bool(row.get("detected", False)),
                "action": str(row.get("action", "")),
            },
            verifier={"passed": row["status"] == "pass"},
            error=str(row.get("error", "")),
        )
        for row in fault_rows
    ]
    passed = sum(row.status == "pass" for row in rows)
    result = EvaluationResult(
        experiment=new_experiment("fault-injection"),
        source=collect_source_provenance(repo_root),
        environment=collect_environment_provenance(repo_root),
        benchmark={
            "id": "repoagent-fault-matrix",
            "version": 1,
            "definition_digest": benchmark_digest,
            "unique_tasks": len(rows),
        },
        model=model,
        design={"variants": ["fault-injected"], "repetitions": 1, "paired": False},
        rows=rows,
        aggregates={
            "effective_n": len(rows),
            "run_n": len(rows),
            "detected": passed,
            "detection_rate_wilson_95": wilson_interval(passed, len(rows)),
        },
        gates=[
            {
                "id": "all_faults_detected",
                "status": "pass" if passed == len(rows) else "fail",
                "observed": f"{passed}/{len(rows)}",
                "threshold": f"{len(rows)}/{len(rows)}",
            }
        ],
        limitations=["Deterministic boundary fault injection; not a production incident rate."],
    )
    result.validate()
    return result


__all__ = [
    "PAIRED_CAMPAIGN_KINDS",
    "RuntimeContractCampaign",
    "fault_campaign_result",
    "paired_campaign_matrix",
    "paired_campaign_result",
]
