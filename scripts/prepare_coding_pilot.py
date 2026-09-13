#!/usr/bin/env python3
"""Validate and retain a private coding pilot without running model calls."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repoagent.evolver.pilot_protocol import freeze_pilot, verify_frozen_pilot  # noqa: E402
from repoagent.evolver.evaluation import payload_digest  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--config")
    parser.add_argument("--output", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        if args.config:
            parser.error("--verify does not accept --config")
        result = verify_frozen_pilot(args.output, repo_root=args.repo)
    else:
        if not args.config:
            parser.error("--config is required when preparing a pilot")
        result = freeze_pilot(args.config, repo_root=args.repo, output_root=args.output)
    print(
        f"status={result['status']} trials={result['reserved_trials']} max_calls={result['max_model_calls']} reserved_usd={result['reserved_cost_usd']}"
    )
    print("execution_authorized=false")
    print(f"preflight_digest={payload_digest(result)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
