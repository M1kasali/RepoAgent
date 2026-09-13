"""Synthetic paired measurements of the production session scheduler."""

import asyncio
from collections import Counter
from dataclasses import asdict, dataclass
import math
from statistics import median
import time

from ..spine import Scheduler, TurnOutcome, TurnRequest, TurnState, Usage


@dataclass(frozen=True)
class SchedulingExperimentConfig:
    repetitions: int = 6
    sessions: int = 8
    turns_per_session: int = 4
    capacity: int = 4
    delay_ms: float = 5.0
    timeout_seconds: float = 30.0

    def __post_init__(self):
        for name in ("repetitions", "sessions", "turns_per_session", "capacity"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("delay_ms", "timeout_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")


class _Executor:
    def __init__(self, delay_ms):
        self.delay_ms = delay_ms
        self.accepted = []
        self.rows = []
        self.active = Counter()
        self.peak = 0
        self.session_peak = 0
        self.origin = time.perf_counter()

    def milliseconds(self):
        return (time.perf_counter() - self.origin) * 1000

    def accept(self, request):
        self.accepted.append(str(request.turn_id))

    async def run(self, request, emit, drain):
        session = str(request.session_id)
        row = {"turn_id": str(request.turn_id), "session": session,
               "sequence": int(request.text), "started_ms": self.milliseconds()}
        self.rows.append(row)
        self.active[session] += 1
        self.peak = max(self.peak, sum(self.active.values()))
        self.session_peak = max(self.session_peak, self.active[session])
        try:
            await asyncio.sleep(self.delay_ms / 1000)
            row["finished_ms"] = self.milliseconds()
            return TurnOutcome(
                turn_id=request.turn_id, request_id=request.request_id,
                session_id=request.session_id, state=TurnState.COMPLETED,
                usage=Usage(),
            )
        finally:
            self.active[session] -= 1

    async def cancel(self, request, reason):
        return TurnOutcome(
            turn_id=request.turn_id, request_id=request.request_id,
            session_id=request.session_id, state=TurnState.CANCELLED,
            usage=Usage(), error=reason,
        )


async def _run_arm(config, capacity):
    executor = _Executor(config.delay_ms)
    scheduler = Scheduler(executor, foreground_capacity=capacity)
    requests = [
        TurnRequest.create(session_id=f"session-{session}", text=str(sequence))
        for sequence in range(config.turns_per_session)
        for session in range(config.sessions)
    ]
    submitted = {}
    handles = []
    try:
        for request in requests:
            submitted[str(request.turn_id)] = executor.milliseconds()
            handles.append(scheduler.submit(request))
        outcomes = await asyncio.wait_for(
            asyncio.gather(*(handle.result() for handle in handles)),
            timeout=config.timeout_seconds,
        )
    finally:
        await scheduler.shutdown(grace=0)
    ids = [str(request.turn_id) for request in requests]
    executed = [row["turn_id"] for row in executor.rows]
    correct = (
        Counter(executor.accepted) == Counter(ids)
        and Counter(executed) == Counter(ids)
        and Counter(str(outcome.turn_id) for outcome in outcomes) == Counter(ids)
        and all(outcome.state is TurnState.COMPLETED for outcome in outcomes)
        and executor.session_peak == 1
        and 1 <= executor.peak <= min(capacity, config.sessions)
        and not any(executor.active.values())
        and all(not scheduler.has_work(f"session-{i}") for i in range(config.sessions))
        and all(
            [row["sequence"] for row in executor.rows
             if row["session"] == f"session-{i}"] == list(range(config.turns_per_session))
            for i in range(config.sessions)
        )
    )
    for row in executor.rows:
        row["submitted_ms"] = submitted[row["turn_id"]]
        row["queue_ms"] = row["started_ms"] - row["submitted_ms"]
        row["latency_ms"] = row["finished_ms"] - row["submitted_ms"]
    latencies = sorted(row["latency_ms"] for row in executor.rows)
    return {
        "correct": correct, "capacity": capacity, "request_count": len(ids),
        "peak_concurrency": executor.peak, "session_peak": executor.session_peak,
        "p50_ms": median(latencies),
        "p95_ms": latencies[math.ceil(0.95 * len(latencies)) - 1],
        "rows": executor.rows,
    }


def run_scheduling_experiment(config=None):
    config = config or SchedulingExperimentConfig()

    async def run():
        pairs = []
        for repetition in range(config.repetitions):
            order = ["serial", "parallel"]
            if repetition % 2:
                order.reverse()
            arms = {}
            for arm in order:
                arms[arm] = await _run_arm(config, 1 if arm == "serial" else config.capacity)
            pairs.append({"repetition": repetition, "arm_order": order, **arms})
        return pairs

    pairs = asyncio.run(run())
    return {
        "schema": "repoagent.evaluation.scheduling-experiment.v1",
        "evidence_scope": "synthetic_session_scheduler_microbenchmark",
        "positive_claim_eligible": False,
        "config": asdict(config), "pairs": pairs,
        "passed": all(pair[arm]["correct"] for pair in pairs
                      for arm in ("serial", "parallel")),
        "summary": {
            arm: {"median_p95_ms": median(pair[arm]["p95_ms"] for pair in pairs)}
            for arm in ("serial", "parallel")
        },
        "limitations": [
            "Synthetic async sleep executor; no model, tool, persistence or network cost.",
            "Only foreground capacity changes; this is not a pool isolation ablation.",
            "P95 uses nearest rank per arm; summary is median of repetition P95s.",
            "Latency gain is not a correctness gate or real-agent performance claim.",
        ],
    }
