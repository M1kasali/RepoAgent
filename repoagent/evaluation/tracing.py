"""Deterministic local microbenchmark for tracing overhead."""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path

from ..run_store import RunStore


TRACING_EXPERIMENT_SCHEMA = "repoagent.tracing-overhead/v1"


def _percentile(values, fraction):
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def measure_tracing_overhead(*, event_count=500, payload_chars=128, output_path=None):
    if event_count < 10 or payload_chars < 0:
        raise ValueError("event_count must be >= 10 and payload_chars non-negative")
    payloads = [
        {"event": "benchmark_event", "sequence": index, "content": "x" * payload_chars}
        for index in range(event_count)
    ]

    baseline_samples = []
    for payload in payloads:
        started = time.perf_counter_ns()
        json.dumps(payload, sort_keys=True)
        baseline_samples.append((time.perf_counter_ns() - started) / 1_000_000)

    with tempfile.TemporaryDirectory(prefix="repoagent-tracing-") as directory:
        store = RunStore(Path(directory))
        traced_samples = []
        for payload in payloads:
            started = time.perf_counter_ns()
            store.append_trace("overhead", payload)
            traced_samples.append((time.perf_counter_ns() - started) / 1_000_000)
        storage_bytes = store.trace_path("overhead").stat().st_size

    result = {
        "schema": TRACING_EXPERIMENT_SCHEMA,
        "design": {
            "event_count": event_count,
            "payload_chars": payload_chars,
            "clock": "perf_counter_ns",
        },
        "baseline": {
            "median_ms": statistics.median(baseline_samples),
            "p95_ms": _percentile(baseline_samples, 0.95),
        },
        "tracing": {
            "median_ms": statistics.median(traced_samples),
            "p95_ms": _percentile(traced_samples, 0.95),
            "storage_bytes": storage_bytes,
            "bytes_per_event": storage_bytes / event_count,
        },
        "overhead": {
            "median_ms_per_event": statistics.median(traced_samples)
            - statistics.median(baseline_samples),
            "p95_ms_per_event": _percentile(traced_samples, 0.95)
            - _percentile(baseline_samples, 0.95),
        },
    }
    if output_path is not None:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


__all__ = ["TRACING_EXPERIMENT_SCHEMA", "measure_tracing_overhead"]
