"""Deterministic local microbenchmark for tracing overhead."""

from __future__ import annotations

import json
import hashlib
import math
import statistics
import tempfile
import time
from pathlib import Path

from ..run_store import RunStore


TRACING_EXPERIMENT_SCHEMA = "repoagent.tracing-overhead/v2"


def _percentile(values, fraction):
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def measure_tracing_overhead(
    *, event_count=500, payload_chars=128, repetitions=3, output_path=None,
):
    if type(event_count) is not int or type(payload_chars) is not int:
        raise ValueError("event_count and payload_chars must be integers")
    if event_count < 10 or payload_chars < 0:
        raise ValueError("event_count must be >= 10 and payload_chars non-negative")
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError("repetitions must be a positive integer")
    payloads = [
        {"event": "benchmark_event", "sequence": index, "content": "x" * payload_chars}
        for index in range(event_count)
    ]

    baseline_samples = []
    traced_samples = []
    pairs = []
    for repetition in range(repetitions):
        order = ["baseline", "tracing"] if repetition % 2 == 0 else ["tracing", "baseline"]
        samples = {"baseline": [], "tracing": []}
        with tempfile.TemporaryDirectory(prefix="repoagent-tracing-") as directory:
            store = RunStore(Path(directory))
            for arm in order:
                for payload in payloads:
                    started = time.perf_counter_ns()
                    if arm == "tracing":
                        store.append_trace("overhead", payload)
                    else:
                        (json.dumps(payload, sort_keys=True, ensure_ascii=True) + "\n").encode("utf-8")
                    samples[arm].append((time.perf_counter_ns() - started) / 1_000_000)
            trace_bytes = store.trace_path("overhead").read_bytes()
        trace_text = trace_bytes.decode("utf-8")
        recorded = [json.loads(line) for line in trace_text.splitlines()]
        pairs.append({
            "repetition": repetition, "arm_order": order,
            "correct": recorded == payloads,
            "storage_bytes": len(trace_bytes),
            "trace_sha256": hashlib.sha256(trace_bytes).hexdigest(),
            "trace_jsonl": trace_text,
            "samples": [
                {"sequence": i, "baseline_ms": baseline, "tracing_ms": traced,
                 "delta_ms": traced - baseline}
                for i, (baseline, traced) in enumerate(zip(samples["baseline"], samples["tracing"]))
            ],
        })
        baseline_samples.extend(samples["baseline"])
        traced_samples.extend(samples["tracing"])
    storage_bytes = sum(pair["storage_bytes"] for pair in pairs)
    deltas = [row["delta_ms"] for pair in pairs for row in pair["samples"]]

    result = {
        "schema": TRACING_EXPERIMENT_SCHEMA,
        "evidence_scope": "local_run_store_append_microbenchmark",
        "positive_claim_eligible": False,
        "passed": all(pair["correct"] for pair in pairs),
        "pairs": pairs,
        "design": {
            "event_count": event_count,
            "payload_chars": payload_chars,
            "repetitions": repetitions,
            "clock": "perf_counter_ns",
            "percentile": "nearest_rank_pooled_event_samples",
            "baseline": "serialize identical JSONL bytes without persistence",
            "tracing": "RunStore.append_trace including lock, prefix validation and fsync",
        },
        "baseline": {
            "median_ms": statistics.median(baseline_samples),
            "p95_ms": _percentile(baseline_samples, 0.95),
        },
        "tracing": {
            "median_ms": statistics.median(traced_samples),
            "p95_ms": _percentile(traced_samples, 0.95),
            "storage_bytes": storage_bytes,
            "bytes_per_event": storage_bytes / (event_count * repetitions),
        },
        "overhead": {
            "median_ms_per_event": statistics.median(traced_samples)
            - statistics.median(baseline_samples),
            "p95_ms_per_event": _percentile(traced_samples, 0.95)
            - _percentile(baseline_samples, 0.95),
            "paired_delta_median_ms": statistics.median(deltas),
            "paired_delta_p95_ms": _percentile(deltas, 0.95),
        },
        "limitations": [
            "No Agent request, context propagation or model/tool latency is measured.",
            "overhead.p95_ms_per_event is a difference of quantiles, not delta P95.",
            "Pairs match repetition and event sequence; log growth affects append cost.",
            "Storage counts trace JSONL only, excluding lock files and filesystem allocation.",
            "Default temporary filesystem; durability after power loss is not validated.",
        ],
    }
    if output_path is not None:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


__all__ = ["TRACING_EXPERIMENT_SCHEMA", "measure_tracing_overhead"]
