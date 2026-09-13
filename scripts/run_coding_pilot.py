#!/usr/bin/env python3
"""Run an explicitly acknowledged fixed-pair pilot. This can incur API charges."""

import argparse
import importlib
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repoagent.evolver.pilot_runner import run_frozen_pilot  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--frozen", required=True)
    parser.add_argument(
        "--factory",
        required=True,
        help="trusted host module:function returning a fresh budgeted gateway",
    )
    parser.add_argument("--approve-preflight-digest", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--docker", default="docker")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", args.factory):
        parser.error("--factory must be module:function")
    module, name = args.factory.split(":")

    def factory():
        # Import only after frozen-input verification, acknowledgement and fencing.
        return getattr(importlib.import_module(module), name)()

    result = run_frozen_pilot(
        repo_root=args.repo,
        frozen_root=args.frozen,
        client_factory=factory,
        approved_preflight_digest=args.approve_preflight_digest,
        actor=args.actor,
        executable=args.docker,
    )
    print(
        f"status={result['status']} completed_trials={result['completed_trials']} estimated_usd={result['known_estimated_cost_usd']}"
    )
    print("automatic_promotion=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
