#!/usr/bin/env python3
"""Run the deterministic local tracing-overhead experiment."""

import argparse
import json

from repoagent.evaluation.tracing import measure_tracing_overhead


def main():
    parser = argparse.ArgumentParser(description="Measure RepoAgent tracing overhead.")
    parser.add_argument("--events", type=int, default=500)
    parser.add_argument("--payload-chars", type=int, default=128)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    result = measure_tracing_overhead(
        event_count=args.events,
        payload_chars=args.payload_chars,
        output_path=args.output,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
