#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repoagent.evaluation.tool_execution import (  # noqa: E402
    ToolExecutionExperimentConfig,
    run_tool_execution_experiment,
)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Run the serial-versus-parallel Tool Gateway experiment."
    )
    parser.add_argument("--repetitions", type=int, default=7)
    parser.add_argument("--tool-calls", type=int, default=8)
    parser.add_argument("--delay-ms", type=float, default=20.0)
    parser.add_argument("--max-parallel", type=int, default=4)
    parser.add_argument("--output-json", default=None)
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    payload = run_tool_execution_experiment(
        ToolExecutionExperimentConfig(
            repetitions=args.repetitions,
            tool_calls=args.tool_calls,
            delay_ms=args.delay_ms,
            max_parallel=args.max_parallel,
        )
    )
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output_json:
        output = Path(args.output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
