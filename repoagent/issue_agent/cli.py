"""Issue application CLI, separate from the general coding interface."""

import json
from pathlib import Path

import httpx

from .cases import (
    CaseStore,
    bind_repository,
    fetch_issue,
    issue_identity,
    read_snapshot,
)


def add_parser(commands):
    parser = commands.add_parser(
        "issue", help="Investigate explicitly selected GitHub issues."
    )
    sub = parser.add_subparsers(dest="issue_command", required=True)
    intake = sub.add_parser(
        "open", help="Snapshot an issue and bind a local repository revision."
    )
    intake.add_argument("url")
    intake.add_argument("--repo", required=True)
    intake.add_argument(
        "--revision",
        required=True,
        help="Exact buggy commit SHA; never a moving branch.",
    )
    intake.add_argument(
        "--snapshot", help="Optional local JSON with html_url, title and body."
    )
    intake.add_argument("--store", default=".repoagent/issues")
    show = sub.add_parser("show", help="Show a saved case and its execution state.")
    show.add_argument("case_id")
    show.add_argument("--store", default=".repoagent/issues")
    investigate = sub.add_parser(
        "investigate",
        help="Run an isolated investigation with a trusted verification config.",
    )
    investigate.add_argument("case_id")
    investigate.add_argument(
        "--config",
        required=True,
        help="Maintainer-owned execution config, never issue-supplied.",
    )
    investigate.add_argument("--store", default=".repoagent/issues")
    fix = sub.add_parser(
        "fix", help="Explicitly generate and independently verify a candidate repair."
    )
    fix.add_argument("case_id")
    fix.add_argument("--store", default=".repoagent/issues")


def run(args):
    store = CaseStore(args.store)
    if args.issue_command == "show":
        return store.load(args.case_id)
    if args.issue_command in {"investigate", "fix"}:
        from .workflow import execute_case

        config = None
        if args.issue_command == "investigate":
            with Path(args.config).open("rb") as source:
                raw = source.read(64001)
            if len(raw) > 64000:
                raise ValueError("execution config is too large")
            config = json.loads(raw)
        return execute_case(store, args.case_id, args.issue_command, config=config)
    identity = issue_identity(args.url)
    bound = bind_repository(args.repo, args.revision, identity["repository"])
    try:
        issue = (
            read_snapshot(args.snapshot, args.url)
            if args.snapshot
            else fetch_issue(args.url)
        )
    except httpx.HTTPError as exc:
        raise ValueError(
            "GitHub issue retrieval failed; check URL, connectivity or public rate limit"
        ) from exc
    return store.create(issue, bound)
