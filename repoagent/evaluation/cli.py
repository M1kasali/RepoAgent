"""Standalone evaluation-platform CLI; unified product routing belongs to P8."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .campaigns import RuntimeContractCampaign
from .release import ReleaseEvidenceBuilder, compare_results
from .schema import validate_result_payload


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Run and validate RepoAgent evaluations.")
    commands = parser.add_subparsers(dest="command", required=True)

    contract = commands.add_parser("contract", help="Run the deterministic runtime-contract campaign.")
    contract.add_argument("--repo-root", default=".")
    contract.add_argument("--benchmark", default="benchmarks/coding_tasks.json")
    contract.add_argument("--output", required=True)

    validate = commands.add_parser("validate", help="Validate an evaluation-result artifact.")
    validate.add_argument("result")
    validate.add_argument("--require-evidence", action="store_true")

    compare = commands.add_parser("compare", help="Compare candidate results with a baseline.")
    compare.add_argument("baseline")
    compare.add_argument("candidate")
    compare.add_argument("--max-pass-rate-drop", type=float, default=0.0)

    release = commands.add_parser("release", help="Build a self-contained release evidence directory.")
    release.add_argument("result")
    release.add_argument("destination")
    release.add_argument("--tag", required=True)
    release.add_argument("--repo-root", default=".")
    release.add_argument("--benchmark", default=None)
    return parser


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    if args.command == "contract":
        result = RuntimeContractCampaign(
            repo_root=args.repo_root,
            benchmark_path=args.benchmark,
            output_root=args.output,
        ).run()
        payload = result.to_dict()
    elif args.command == "validate":
        payload = validate_result_payload(
            _load(args.result), require_evidence=args.require_evidence
        )
    elif args.command == "compare":
        payload = compare_results(
            _load(args.baseline),
            _load(args.candidate),
            max_pass_rate_drop=args.max_pass_rate_drop,
        )
    else:
        path = ReleaseEvidenceBuilder().build(
            args.result,
            args.destination,
            require_clean=True,
            release_tag=args.tag,
            repo_root=args.repo_root,
            benchmark_path=args.benchmark,
        )
        payload = {"status": "created", "path": str(path)}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
