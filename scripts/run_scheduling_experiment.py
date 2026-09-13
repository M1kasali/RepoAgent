#!/usr/bin/env python3
"""Run a local paired scheduler experiment without model calls."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repoagent.evaluation.scheduling import (  # noqa: E402
    SchedulingExperimentConfig,
    run_scheduling_experiment,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repetitions", type=int, default=6)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()
    result = run_scheduling_experiment(SchedulingExperimentConfig(repetitions=args.repetitions))
    path = Path(args.output_json)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
