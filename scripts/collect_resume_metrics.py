#!/usr/bin/env python3
"""Generate public claims exclusively from a verified tagged release bundle."""

import argparse
import json
from pathlib import Path

from repoagent.evaluation.resume import (
    render_resume_claims_markdown,
    resume_claims_from_release,
)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Generate resume claims from tagged RepoAgent release evidence."
    )
    parser.add_argument("--release-bundle", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-markdown", required=True)
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    claims = resume_claims_from_release(args.release_bundle)
    output_json = Path(args.output_json)
    output_markdown = Path(args.output_markdown)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(claims, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    output_markdown.write_text(
        render_resume_claims_markdown(claims) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
