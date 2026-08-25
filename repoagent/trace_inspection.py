"""Local, read-only trace inspection command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .evidence import EvidenceBundleBuilder
from .run_store import RunStore


def inspect_trace(store, run_id, *, events=(), stages=(), limit=None):
    export = store.export_trace(run_id)
    if events or stages or limit is not None:
        export["trace"] = store.query_trace(
            run_id, events=events, stages=stages, limit=limit
        )
    return export


def render_trace_summary(payload):
    lines = [
        f"run: {payload['run_id']}",
        f"turn events: {len(payload['turn_events'])}",
        f"runtime events: {len(payload['trace'])}",
        f"provider calls: {len(payload['model_calls'])}",
    ]
    for row in payload["trace"]:
        event = row.get("event", "unknown")
        stage = row.get("stage", "unknown")
        call_id = row.get("provider_call_id") or row.get("tool_call_id") or ""
        suffix = f" [{call_id}]" if call_id else ""
        lines.append(f"{row.get('created_at', '')} {stage:>9} {event}{suffix}")
    return "\n".join(lines)


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Inspect RepoAgent Turn evidence.")
    parser.add_argument("run_id", help="Turn/run identifier.")
    parser.add_argument("--root", required=True, help="RunStore root directory.")
    parser.add_argument("--event", action="append", default=[], help="Filter runtime event name.")
    parser.add_argument("--stage", action="append", default=[], help="Filter trace stage.")
    parser.add_argument("--limit", type=int, default=None, help="Keep the latest N runtime events.")
    parser.add_argument("--json", action="store_true", help="Print structured JSON.")
    parser.add_argument("--bundle", default=None, help="Write a checksummed evidence bundle directory.")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    store = RunStore(Path(args.root))
    payload = inspect_trace(
        store,
        args.run_id,
        events=args.event,
        stages=args.stage,
        limit=args.limit,
    )
    if args.bundle:
        EvidenceBundleBuilder(store).build(args.run_id, args.bundle)
    print(json.dumps(payload, indent=2, sort_keys=True) if args.json else render_trace_summary(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
