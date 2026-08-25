"""Deterministic serial-versus-parallel Tool Gateway experiment."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
import threading
import time

from ..providers import FakeModelClient
from ..runtime import RepoAgent
from ..session_store import SessionStore
from ..workspace import WorkspaceContext


TOOL_EXECUTION_EXPERIMENT_SCHEMA = (
    "repoagent.evaluation.tool-execution-experiment.v1"
)


@dataclass(frozen=True)
class ToolExecutionExperimentConfig:
    repetitions: int = 7
    tool_calls: int = 8
    delay_ms: float = 20.0
    max_parallel: int = 4

    def __post_init__(self):
        if self.repetitions < 1:
            raise ValueError("repetitions must be positive")
        if self.tool_calls < 2:
            raise ValueError("tool_calls must be at least two")
        if self.delay_ms <= 0:
            raise ValueError("delay_ms must be positive")
        if self.max_parallel < 1:
            raise ValueError("max_parallel must be positive")


def run_tool_execution_experiment(config=None):
    config = config or ToolExecutionExperimentConfig()
    rows = []
    for repetition in range(config.repetitions):
        arm_order = (
            ("serial", "capability_parallel")
            if repetition % 2 == 0
            else ("capability_parallel", "serial")
        )
        arms = {
            arm: _run_arm(config, parallel=arm == "capability_parallel")
            for arm in arm_order
        }
        rows.append(
            {
                "repetition": repetition,
                "arm_order": list(arm_order),
                "serial": arms["serial"],
                "capability_parallel": arms["capability_parallel"],
            }
        )

    serial_values = [row["serial"]["elapsed_ms"] for row in rows]
    parallel_values = [
        row["capability_parallel"]["elapsed_ms"] for row in rows
    ]
    serial_median = median(serial_values)
    parallel_median = median(parallel_values)
    serial_peak = max(row["serial"]["peak_concurrency"] for row in rows)
    parallel_peak = max(
        row["capability_parallel"]["peak_concurrency"] for row in rows
    )
    expected_parallel_peak = min(config.tool_calls, config.max_parallel)
    correct = all(
        row[arm]["correct"]
        for row in rows
        for arm in ("serial", "capability_parallel")
    )
    correct = (
        correct
        and serial_peak == 1
        and parallel_peak == expected_parallel_peak
    )
    reduction = (
        (serial_median - parallel_median) / serial_median * 100
        if serial_median
        else 0.0
    )
    return {
        "schema": TOOL_EXECUTION_EXPERIMENT_SCHEMA,
        "evidence_scope": "synthetic_gateway_scheduler_microbenchmark",
        "positive_claim_eligible": False,
        "config": asdict(config),
        "repetitions": rows,
        "summary": {
            "correctness_passed": correct,
            "serial_median_ms": serial_median,
            "capability_parallel_median_ms": parallel_median,
            "median_latency_reduction_percent": reduction,
            "serial_peak_concurrency": serial_peak,
            "capability_parallel_peak_concurrency": parallel_peak,
        },
        "passed": bool(correct and reduction > 0),
    }


def _run_arm(config, *, parallel):
    with TemporaryDirectory(prefix="repoagent-tool-experiment-") as directory:
        root = Path(directory)
        for index in range(config.tool_calls):
            (root / f"file-{index}.txt").write_text(
                f"value-{index}\n", encoding="utf-8"
            )
        agent = RepoAgent(
            model_client=FakeModelClient(()),
            workspace=WorkspaceContext.build(root),
            session_store=SessionStore(root / ".state" / "sessions"),
            approval_policy="auto",
            max_parallel_tools=config.max_parallel,
            feature_flags={"memory": False},
        )
        lock = threading.Lock()
        state = {"active": 0, "peak": 0}

        def delayed_read(arguments, control):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            try:
                time.sleep(config.delay_ms / 1000)
                return arguments["path"]
            finally:
                with lock:
                    state["active"] -= 1

        agent.tools["read_file"]["run"] = delayed_read
        requests = tuple(
            agent.build_tool_request(
                "read_file",
                {"path": f"file-{index}.txt"},
                call_id=f"call-{index}",
            )
            for index in range(config.tool_calls)
        )
        started = time.perf_counter_ns()
        if parallel:
            results = tuple(
                result
                for batch in agent.tool_gateway.plan_batches(requests)
                for result in agent.execute_tool_batch(batch)
            )
        else:
            results = tuple(agent.execute_tool_request(item) for item in requests)
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        expected = [f"file-{index}.txt" for index in range(config.tool_calls)]
        outputs = [result.content for result in results]
        return {
            "elapsed_ms": elapsed_ms,
            "peak_concurrency": state["peak"],
            "outputs": outputs,
            "correct": outputs == expected
            and all(result.status == "ok" for result in results),
        }


__all__ = [
    "TOOL_EXECUTION_EXPERIMENT_SCHEMA",
    "ToolExecutionExperimentConfig",
    "run_tool_execution_experiment",
]
