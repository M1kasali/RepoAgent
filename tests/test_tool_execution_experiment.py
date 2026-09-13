import pytest

from repoagent.evaluation.tool_execution import (
    TOOL_EXECUTION_EXPERIMENT_SCHEMA,
    ToolExecutionExperimentConfig,
    run_tool_execution_experiment,
)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"repetitions": 0}, "repetitions"),
        ({"tool_calls": 1}, "tool_calls"),
        ({"delay_ms": 0}, "delay_ms"),
        ({"max_parallel": 0}, "max_parallel"),
        ({"delay_ms": float("nan")}, "delay_ms"),
        ({"delay_ms": float("inf")}, "delay_ms"),
        ({"workload": "unknown"}, "workload"),
    ],
)
def test_tool_execution_experiment_validates_config(kwargs, message):
    with pytest.raises(ValueError, match=message):
        ToolExecutionExperimentConfig(**kwargs)


def test_eight_read_experiment_proves_bounded_parallelism_and_order():
    payload = run_tool_execution_experiment(
        ToolExecutionExperimentConfig(
            repetitions=3,
            tool_calls=8,
            delay_ms=8,
            max_parallel=4,
        )
    )

    assert payload["schema"] == TOOL_EXECUTION_EXPERIMENT_SCHEMA
    assert payload["positive_claim_eligible"] is False
    assert payload["passed"] is True
    assert payload["summary"]["correctness_passed"] is True
    assert payload["summary"]["serial_peak_concurrency"] == 1
    assert payload["summary"]["capability_parallel_peak_concurrency"] == 4
    assert payload["summary"]["median_latency_reduction_percent"] > 0
    expected = [f"file-{index}.txt" for index in range(8)]
    for row in payload["repetitions"]:
        assert row["serial"]["outputs"] == expected
        assert row["capability_parallel"]["outputs"] == expected


def test_local_read_uses_real_file_contents():
    payload = run_tool_execution_experiment(
        ToolExecutionExperimentConfig(repetitions=2, workload="local_read")
    )
    assert payload["passed"] is True
    assert payload["evidence_scope"] == "warm_local_file_gateway_microbenchmark"
    assert payload["positive_claim_eligible"] is False
    for row in payload["repetitions"]:
        assert row["serial"]["outputs"] == row["capability_parallel"]["outputs"]
        assert row["serial"]["outputs"][0] == "# file-0.txt\n   1: value-0"


def test_slower_parallel_arm_does_not_fail_correctness(monkeypatch):
    def run_arm(config, *, parallel):
        return {"elapsed_ms": 20 if parallel else 10,
                "peak_concurrency": 1, "correct": True}

    monkeypatch.setattr("repoagent.evaluation.tool_execution._run_arm", run_arm)
    payload = run_tool_execution_experiment()
    assert payload["passed"] is True
    assert payload["summary"]["latency_improved"] is False
    assert payload["summary"]["median_latency_reduction_percent"] == -100
