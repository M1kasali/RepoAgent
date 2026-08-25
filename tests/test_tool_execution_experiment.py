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
