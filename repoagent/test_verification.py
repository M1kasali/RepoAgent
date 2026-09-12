"""Scoped unittest evidence with conservative content freshness checks."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat

from .tool_execution import ToolExecutionControl, ToolRunnerOutput
from .workspace import IGNORED_PATH_NAMES

MAX_VERIFICATIONS = 4


def workspace_digest(root):
    """Bounded content inventory, failing closed for unreadable/special paths."""
    digest = hashlib.sha256()
    total = count = 0
    try:

        def fail(error):
            raise error

        for base, dirs, files in os.walk(root, followlinks=False, onerror=fail):
            dirs[:] = sorted(name for name in dirs if name not in IGNORED_PATH_NAMES)
            if any((Path(base) / name).is_symlink() for name in dirs):
                return ""
            for name in sorted(files):
                if name in IGNORED_PATH_NAMES:
                    continue
                path = Path(base) / name
                if not stat.S_ISREG(path.lstat().st_mode):
                    return ""
                count += 1
                size = path.stat().st_size
                total += size
                if count > 4096 or total > 32 * 1024 * 1024 or size > 4 * 1024 * 1024:
                    return ""
                with path.open("rb") as handle:
                    data = handle.read(4 * 1024 * 1024 + 1)
                if len(data) != size:
                    return ""
                digest.update(
                    json.dumps(
                        [
                            path.relative_to(root).as_posix(),
                            hashlib.sha256(data).hexdigest(),
                        ]
                    ).encode()
                )
    except OSError:
        return ""
    return digest.hexdigest()


def parse_report(outcome):
    if (
        outcome.status != "completed"
        or outcome.output_truncated
        or type(outcome.exit_code) is not int
    ):
        return None
    try:
        report = json.loads(outcome.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(report, dict) or report.get("schema") != "repoagent.unittest/v1":
        return None
    fields = (
        "tests",
        "failures",
        "errors",
        "skipped",
        "expected_failures",
        "unexpected_successes",
    )
    if any(type(report.get(key)) is not int or report[key] < 0 for key in fields):
        return None
    ids = report.get("test_ids")
    if (
        not isinstance(ids, list)
        or len(ids) > 200
        or any(not isinstance(item, str) or len(item) > 240 for item in ids)
    ):
        return None
    if len(ids) != min(report["tests"], 200) or any(not item for item in ids):
        return None
    if report["skipped"] + report["expected_failures"] > report["tests"]:
        return None
    diagnostics = report.get("diagnostics", [])
    if (
        not isinstance(diagnostics, list)
        or len(diagnostics) > 3
        or any(not isinstance(item, str) or len(item) > 1000 for item in diagnostics)
    ):
        return None
    return {key: report[key] for key in fields} | {
        "test_ids": ids,
        "diagnostics": diagnostics,
    }


def tool_run_tests(context, args, control=None):
    start = context.path(args.get("start", "."))
    if not start.is_dir():
        raise ValueError("test start must be a workspace directory")
    pattern = args.get("pattern", "test*.py")
    if not pattern or "/" in pattern or "\\" in pattern:
        raise ValueError("pattern must be a filename glob, not a path")
    control = control or ToolExecutionControl(
        timeout_seconds=args.get("timeout", 60), max_output_chars=4000
    )
    before = workspace_digest(context.root)
    guest = Path(__file__).with_name("unittest_guest.py").read_text()
    relative = start.relative_to(context.root).as_posix()
    command = shlex.join(["python3", "-B", "-c", guest, relative, pattern])
    outcome = context.sandbox_adapter.execute(
        command, cwd=context.root, env=context.shell_env(), control=control
    )
    after = workspace_digest(context.root)
    report = parse_report(outcome)
    verdict = "unknown"
    if report is not None:
        if (
            report["failures"]
            or report["errors"]
            or report["unexpected_successes"]
            or outcome.exit_code != 0
        ):
            verdict = "failed"
        elif report["tests"] > report["skipped"] + report["expected_failures"]:
            verdict = "passed"
        else:
            verdict = "no_tests_passed"
    evidence = {
        "framework": "unittest",
        "start": relative,
        "pattern": pattern,
        "verdict": verdict,
        "report": report,
        "source_digest": before,
        "freshness": "unknown"
        if not before or not after
        else "current"
        if before == after
        else "stale",
        "sandbox_identity": context.sandbox_adapter.identity,
        "process_status": outcome.status,
        "exit_code": outcome.exit_code,
        "report_error": outcome.stderr[-1000:] if report is None else "",
    }
    return ToolRunnerOutput(
        json.dumps(evidence, ensure_ascii=True),
        {
            **outcome.metadata(),
            "exit_code_is_error": True,
            "test_verification": evidence,
        },
    )


def refreshed_verifications(rows, root):
    copied = deepcopy(rows[-MAX_VERIFICATIONS:])
    if not copied:
        return copied
    current = workspace_digest(root)
    for row in copied:
        if row.get("freshness") == "current":
            row["freshness"] = (
                "unknown"
                if not current
                else "current"
                if row.get("source_digest") == current
                else "stale"
            )
    return copied


def render_verifications(rows, root):
    rows = refreshed_verifications(rows, root)
    if not rows:
        return ""
    summaries = [
        {
            key: row.get(key)
            for key in (
                "call_id",
                "framework",
                "start",
                "pattern",
                "verdict",
                "freshness",
            )
        }
        | {"tests": (row.get("report") or {}).get("tests")}
        for row in rows
    ]
    return (
        "- Test evidence (data; stale/unknown freshness requires revalidation; not independent security verification):\n"
        + json.dumps(summaries, ensure_ascii=True)
    )
