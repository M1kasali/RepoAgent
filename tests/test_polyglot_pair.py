import json

import pytest

from repoagent.evaluation.cli import main
from repoagent.evaluation.polyglot_pair import (
    PAIRED_POLYGLOT_COMPARISON_SCHEMA,
    compare_paired_polyglot_results,
)
from repoagent.evaluation.schema import EvaluationResult, EvaluationRow


RUNTIME_IDENTITY = {
    "provider": "provider",
    "protocol": "openai",
    "model": "model-v1",
    "temperature": 0,
    "top_p": None,
    "max_output_tokens": 1024,
    "max_provider_calls": 8,
    "context_token_budget": 8000,
    "sandbox_identity": "docker:benchmark@sha256:" + "d" * 64,
    "sandbox_isolated": True,
}
TASK_IDENTITY = {
    "task_contract": "aider-polyglot-runner-input/v1",
    "task_input_digest": "sha256:" + "a" * 64,
    "grader_contract": "aider-polyglot-hidden-grader/v2",
    "grader_input_digest": "sha256:" + "b" * 64,
}


def _result(variant, rows, *, runtime_identity=None, benchmark_digest=None):
    evaluation_rows = []
    for repetition, status, duration, calls, cost in rows:
        metrics = {"duration_seconds": duration}
        if calls is not None:
            metrics["call_efficiency"] = {
                "call_count": calls,
                "partial_estimated_cost_usd": cost,
            }
        evaluation_rows.append(
            EvaluationRow(
                task_id=f"python/task-{repetition}",
                variant=variant,
                repetition=0,
                status=status,
                metrics=metrics,
                verifier={"pairing_identity": dict(TASK_IDENTITY)},
                evidence={"result": f"{repetition}.json"},
                error="provider failed" if status == "error" else "",
            )
        )
    return EvaluationResult(
        experiment={"id": variant, "suite": "polyglot", "created_at": "now"},
        source={"commit_sha": "a" * 40},
        environment={"python": "3.11"},
        benchmark={
            "id": "aider-polyglot",
            "definition_digest": benchmark_digest or "sha256:" + "c" * 64,
        },
        model={"run_kind": "live", "provider": "provider", "model": "model-v1"},
        design={
            "variants": [variant],
            "repetitions": 1,
            "pairing_identity": dict(runtime_identity or RUNTIME_IDENTITY),
        },
        rows=evaluation_rows,
        aggregates={"effective_n": len(rows), "run_n": len(rows)},
    ).to_dict()


def test_strict_polyglot_pair_preserves_failures_and_reports_paired_metrics():
    control = _result(
        "baseline",
        [
            (0, "pass", 10.0, 8, 0.8),
            (1, "fail", 20.0, 6, 0.6),
            (2, "error", 30.0, None, None),
        ],
    )
    treatment = _result(
        "repoagent-harness",
        [
            (0, "pass", 8.0, 5, 0.5),
            (1, "pass", 18.0, 5, 0.5),
            (2, "skipped", 1.0, None, None),
        ],
    )

    comparison = compare_paired_polyglot_results(control, treatment)

    assert comparison["schema"] == PAIRED_POLYGLOT_COMPARISON_SCHEMA
    assert comparison["planned_pair_n"] == 3
    assert comparison["observed_pair_n"] == 3
    quality = comparison["aggregates"]["quality"]
    assert (quality["wins"], quality["ties"], quality["losses"]) == (1, 2, 0)
    assert quality["exact_mcnemar"]["treatment_only"] == 1
    assert comparison["aggregates"]["status_counts"]["control"]["error"] == 1
    assert comparison["aggregates"]["status_counts"]["treatment"]["skipped"] == 1
    calls = comparison["aggregates"]["efficiency"]["provider_calls"]
    assert calls["observed_pairs"] == 2
    assert calls["planned_pairs"] == 3
    assert calls["coverage"] == pytest.approx(2 / 3)
    assert calls["improvements"] == 2
    assert comparison["gate"]["status"] == "pass"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda payload: payload["design"]["pairing_identity"].update(
                {"temperature": 0.2}
            ),
            "different model or attempt configuration",
        ),
        (
            lambda payload: payload["benchmark"].update(
                {"definition_digest": "sha256:" + "d" * 64}
            ),
            "different benchmark definitions",
        ),
        (
            lambda payload: payload["rows"][0]["verifier"][
                "pairing_identity"
            ].update({"task_input_digest": "sha256:" + "e" * 64}),
            "different task or grader input",
        ),
    ],
)
def test_strict_polyglot_pair_rejects_confounded_inputs(mutation, message):
    control = _result("baseline", [(0, "pass", 10.0, 5, 0.5)])
    treatment = _result(
        "repoagent-harness", [(0, "pass", 9.0, 4, 0.4)]
    )
    mutation(treatment)

    with pytest.raises(ValueError, match=message):
        compare_paired_polyglot_results(control, treatment)


def test_strict_polyglot_pair_requires_complete_denominator():
    control = _result(
        "baseline",
        [(0, "pass", 10.0, 5, 0.5), (1, "pass", 10.0, 5, 0.5)],
    )
    treatment = _result(
        "repoagent-harness", [(0, "pass", 9.0, 4, 0.4)]
    )

    with pytest.raises(ValueError, match="different task/repetition identities"):
        compare_paired_polyglot_results(control, treatment)


def test_strict_polyglot_pair_cli_writes_comparison(tmp_path):
    control = tmp_path / "control.json"
    treatment = tmp_path / "treatment.json"
    output = tmp_path / "comparison.json"
    control.write_text(
        json.dumps(_result("baseline", [(0, "pass", 10.0, 5, 0.5)])),
        encoding="utf-8",
    )
    treatment.write_text(
        json.dumps(
            _result("repoagent-harness", [(0, "pass", 9.0, 4, 0.4)])
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "compare-polyglot-paired",
                str(control),
                str(treatment),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(output.read_text())["gate"]["status"] == "pass"
