#!/usr/bin/env python3
"""Run offline context and memory visibility acceptance."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repoagent.evaluation.context_memory import run_context_memory_experiment  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    result = run_context_memory_experiment(parser.parse_args().output)
    print(f"passed={result['passed']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
