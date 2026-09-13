import pytest

from repoagent.evaluation.scheduling import (
    SchedulingExperimentConfig,
    run_scheduling_experiment,
)


@pytest.mark.parametrize("kwargs", [
    {"sessions": 0}, {"capacity": True}, {"turns_per_session": 1.5},
    {"repetitions": -1}, {"delay_ms": float("nan")},
    {"timeout_seconds": float("inf")},
])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        SchedulingExperimentConfig(**kwargs)


def test_paired_scheduler_conserves_requests_and_order():
    result = run_scheduling_experiment(SchedulingExperimentConfig(
        repetitions=2, sessions=3, turns_per_session=3, capacity=2, delay_ms=1,
    ))
    assert result["passed"] is True
    assert result["positive_claim_eligible"] is False
    assert result["pairs"][0]["arm_order"] == ["serial", "parallel"]
    assert result["pairs"][1]["arm_order"] == ["parallel", "serial"]
    for pair in result["pairs"]:
        for arm in ("serial", "parallel"):
            assert pair[arm]["request_count"] == 9
            assert pair[arm]["session_peak"] == 1
            assert all(row["latency_ms"] >= row["queue_ms"] >= 0
                       for row in pair[arm]["rows"])


def test_single_session_stays_serial_despite_extra_capacity():
    result = run_scheduling_experiment(SchedulingExperimentConfig(
        repetitions=1, sessions=1, turns_per_session=2, capacity=4, delay_ms=1,
    ))
    assert result["passed"] is True
    assert result["pairs"][0]["parallel"]["peak_concurrency"] == 1
