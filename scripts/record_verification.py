#!/usr/bin/env python3
"""Run the standard local verification suite and retain a checksummed bundle."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from repoagent.verification import VerificationCommand, VerificationRecorder


def build_arg_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output", default=None)
    parser.add_argument("--polyglot-dataset", default=None)
    parser.add_argument("--polyglot-limit", type=int, default=24)
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    output = args.output or (
        "artifacts/verifications/"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    output = Path(output)
    if not output.is_absolute():
        output = repo_root / output
    python = sys.executable
    commands = [
        VerificationCommand(
            "pytest",
            (python, "-m", "pytest", "-q", "--junitxml={output}/pytest.xml"),
        ),
        VerificationCommand("ruff", (python, "-m", "ruff", "check", ".")),
        VerificationCommand("diff-check", ("git", "diff", "--check")),
        VerificationCommand(
            "evaluation-cli",
            (python, "-m", "repoagent.evaluation.cli", "--help"),
        ),
    ]
    if args.polyglot_dataset:
        commands.append(
            VerificationCommand(
                "polyglot-plan",
                (
                    python,
                    "-m",
                    "repoagent.evaluation.cli",
                    "polyglot-plan",
                    "--dataset",
                    str(Path(args.polyglot_dataset).resolve()),
                    "--output",
                    "{output}/polyglot-plan.json",
                    "--limit",
                    str(args.polyglot_limit),
                ),
            )
        )
    manifest_path = VerificationRecorder(
        repo_root=repo_root,
        output_root=output,
    ).run(commands)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(json.dumps({"path": str(manifest_path), "status": manifest["status"]}, sort_keys=True))
    return 0 if manifest["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
