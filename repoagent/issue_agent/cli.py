"""Issue application CLI, separate from the general coding interface."""

import json
from pathlib import Path
import sys

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
    export = sub.add_parser(
        "export-feedback", help="Freeze minimal failure facts for operator review."
    )
    export.add_argument("case_id")
    export.add_argument("--store", default=".repoagent/issues")
    export.add_argument("--output", required=True)
    export.add_argument("--task-id", required=True)
    export.add_argument("--family", required=True)
    export.add_argument("--split", choices=("training", "sealed"), required=True)
    export.set_defaults(output_format="json")
    reviewed = sub.add_parser(
        "training-evidence", help="Validate an operator review and emit Evolver evidence."
    )
    reviewed.add_argument("case_id")
    reviewed.add_argument("--store", default=".repoagent/issues")
    reviewed.add_argument("--observation", required=True)
    reviewed.add_argument("--review", required=True)
    reviewed.set_defaults(output_format="json")
    for command in (intake, show, investigate, fix):
        command.add_argument(
            "--format",
            choices=("json", "text"),
            default="json",
            dest="output_format",
            help="JSON for scripts (default), or a concise terminal summary with progress.",
        )


def _visible(value):
    text = str(value)
    text = text[:1000] + ("..." if len(text) > 1000 else "")
    return "".join(
        c if c.isprintable() else c.encode("unicode_escape").decode("ascii")
        for c in text
    )


def print_progress(stage):
    messages = {
        "baseline": "Running the baseline probe in isolation...",
        "agent": "Running Agent tools in isolation; model calls may take several minutes...",
        "candidate": "Applying the patch to a clean export and verifying...",
        "failed": "Execution failed; inspect the saved case and report.",
    }
    message = messages.get(stage, "Finished: " + stage.removeprefix("finished:"))
    try:
        print("[issue] " + _visible(message), file=sys.stderr, flush=True)
    except OSError:
        # A disconnected progress display must not change the case outcome.
        pass


def format_summary(state, store_root):
    lines = [
        "RepoAgent Issue",
        f"Case: {_visible(state['case_id'])}",
        f"Issue: {_visible(state['issue']['url'])}",
        f"Title: {_visible(state['issue'].get('title', ''))}",
        f"Status: {_visible(state['status'])}",
        f"Base: {_visible(state['repository']['base_revision'])}",
    ]
    for run in state.get("runs", []):
        lines.append(f"\nPhase: {_visible(run['phase'])} ({_visible(run['status'])})")
        for name, result in run.get("verification", {}).items():
            outcome = (
                "passed"
                if result.get("passed")
                else "reproduced"
                if result.get("reproduced")
                else "not passed"
            )
            lines.append(
                f"  {_visible(name.capitalize())}: {outcome} (exit {_visible(result.get('exit_code', 'unknown'))})"
            )
        agent = run.get("agent", {})
        if agent:
            lines.append(
                f"  Agent: {_visible(agent.get('worker', {}).get('agent_status', 'unknown'))}"
            )
            lines.append(
                f"  Model calls: {_visible(agent.get('model', {}).get('calls_reserved', 'not recorded'))}"
            )
            names = list(agent.get("changes", {}))
            lines.append(
                "  Changed files: "
                + (", ".join(_visible(p) for p in names[:20]) if names else "none")
            )
        if run.get("reason"):
            lines.append("  Reason: " + _visible(run["reason"]))
        if run.get("error_type"):
            lines.append("  Error: " + _visible(run["error_type"]))
    directory = CaseStore(store_root).directory(state["case_id"])
    lines.append("\nEvidence: " + _visible(directory))
    for label, name in (("Report", "report.md"), ("Patch", "fix/candidate.patch")):
        path = directory / name
        if path.is_file():
            lines.append(label + ": " + _visible(path))
    lines.append(
        "No external publication. A candidate still requires maintainer review."
    )
    return "\n".join(lines)


def run(args):
    store = CaseStore(args.store)
    if args.issue_command in {"export-feedback", "training-evidence"}:
        from .training_evidence import freeze_observation, reviewed_failure

        with store.locked(args.case_id) as state:
            if args.issue_command == "export-feedback":
                return freeze_observation(
                    state, output=args.output, task_id=args.task_id,
                    family=args.family, split=args.split,
                )
            values = []
            for name in (args.observation, args.review):
                with Path(name).open("rb") as stream:
                    raw = stream.read(64001)
                if len(raw) > 64000:
                    raise ValueError("review input is too large")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("review input must be a JSON object")
                values.append(value)
            return reviewed_failure(state, *values).to_dict()
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
        return execute_case(
            store,
            args.case_id,
            args.issue_command,
            config=config,
            on_progress=print_progress if args.output_format == "text" else None,
        )
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
