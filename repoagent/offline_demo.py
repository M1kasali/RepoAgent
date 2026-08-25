"""Deterministic end-to-end harness demo without provider credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from .evaluation.campaigns import RuntimeContractCampaign
from .evidence import verify_evidence_bundle


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Run RepoAgent's offline E2E demo.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output", default=None)
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    temporary = None
    if args.output is None:
        temporary = tempfile.TemporaryDirectory(prefix="repoagent-demo-")
        output = Path(temporary.name)
    else:
        output = Path(args.output).resolve()
    result = RuntimeContractCampaign(
        repo_root=args.repo_root,
        benchmark_path=Path(args.repo_root) / "benchmarks" / "coding_tasks.json",
        output_root=output,
    ).run()
    for row in result.rows:
        verify_evidence_bundle(output / row.evidence["bundle"])
    summary = {
        "schema": "repoagent.offline-demo/v1",
        "status": "pass"
        if result.aggregates["passes"] == result.aggregates["effective_n"]
        else "fail",
        "passes": result.aggregates["passes"],
        "effective_n": result.aggregates["effective_n"],
        "evidence_bundles": len(result.rows),
        "output": str(output) if args.output is not None else "temporary",
        "limitation": "Scripted runtime contracts; not a coding-quality benchmark.",
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if temporary is not None:
        temporary.cleanup()
    return 0 if summary["status"] == "pass" else 1


__all__ = ["build_arg_parser", "main"]
