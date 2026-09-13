#!/usr/bin/env python3
"""Run scripted provider accounting acceptance without credentials."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repoagent.evaluation.accounting import run_accounting_experiment  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    result = run_accounting_experiment(parser.parse_args().output)
    print(f"passed={result['passed']} cases={len(result['cases'])}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
