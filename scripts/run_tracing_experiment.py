#!/usr/bin/env python3
"""Run the deterministic local tracing-overhead experiment."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repoagent.evaluation.tracing import measure_tracing_overhead  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Measure RepoAgent tracing overhead.")
    parser.add_argument("--events", type=int, default=500)
    parser.add_argument("--payload-chars", type=int, default=128)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    result = measure_tracing_overhead(
        event_count=args.events,
        payload_chars=args.payload_chars,
        repetitions=args.repetitions,
        output_path=args.output,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
