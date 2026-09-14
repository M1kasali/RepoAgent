"""Bounded read-only issue intake and host-owned case persistence."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from uuid import uuid4

import httpx

from ..atomic_io import atomic_replace_unlocked, file_lock

_ISSUE = re.compile(
    r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/([1-9][0-9]*)/?$"
)
_CASE = re.compile(r"issue_[a-f0-9]{24}$")
MAX_ISSUE_BYTES = 128000


def issue_identity(url):
    match = _ISSUE.fullmatch(str(url))
    if not match or any(part in {".", ".."} for part in match.groups()[:2]):
        raise ValueError(
            "expected a public https://github.com/OWNER/REPO/issues/NUMBER URL"
        )
    owner, repo, number = match.groups()
    return {
        "url": f"https://github.com/{owner}/{repo}/issues/{number}",
        "repository": f"{owner}/{repo}",
        "number": int(number),
    }


def normalize_issue(data, url):
    identity = issue_identity(url)
    if not isinstance(data, dict) or data.get("pull_request") is not None:
        raise ValueError("input must be an issue, not a pull request")
    if issue_identity(data.get("html_url", data.get("url", ""))) != identity:
        raise ValueError("issue snapshot identity does not match requested URL")
    title, body = data.get("title"), data.get("body")
    if not isinstance(title, str) or not title.strip() or len(title) > 1000:
        raise ValueError("issue requires a bounded title")
    if body is None:
        body = ""
    if not isinstance(body, str) or len(body.encode()) > MAX_ISSUE_BYTES:
        raise ValueError("issue body is too large or invalid")
    # Deliberately exclude comments, linked PRs and development metadata.
    return {
        **identity,
        "title": title,
        "body": body,
        "input_scope": "title-and-body-only; comments and solution links not fetched",
    }


def fetch_issue(url, *, client=None):
    identity = issue_identity(url)
    api = f"https://api.github.com/repos/{identity['repository']}/issues/{identity['number']}"
    owned = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=False)
    try:
        with client.stream(
            "GET", api, headers={"Accept": "application/vnd.github+json"}
        ) as response:
            response.raise_for_status()
            if response.is_redirect:
                raise ValueError("issue redirects require an explicit updated URL")
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_ISSUE_BYTES:
                    raise ValueError("GitHub response exceeds intake limit")
        return normalize_issue(json.loads(raw), url)
    finally:
        if owned:
            client.close()


def read_snapshot(path, url):
    with Path(path).open("rb") as source:
        raw = source.read(MAX_ISSUE_BYTES + 1)
    if len(raw) > MAX_ISSUE_BYTES:
        raise ValueError("issue snapshot exceeds intake limit")
    return normalize_issue(json.loads(raw), url)


def git(root, *args):
    result = subprocess.run(
        ["git", "--no-pager", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise ValueError("Git operation failed: " + result.stderr[:1000])
    return result.stdout.strip()


def bind_repository(root, revision, repository):
    root = Path(root).expanduser().resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("base revision must be an exact 40-character commit SHA")
    if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("repository path must be its Git root")
    if git(root, "rev-parse", revision + "^{commit}") != revision:
        raise ValueError("base revision is not a commit")
    origin = git(root, "remote", "get-url", "origin")
    expected = {
        f"https://github.com/{repository}",
        f"https://github.com/{repository}.git",
        f"git@github.com:{repository}.git",
    }
    if origin not in expected:
        raise ValueError("local origin does not match the issue repository")
    return {
        "path": str(root),
        "base_revision": revision,
        "tree": git(root, "rev-parse", revision + "^{tree}"),
        "origin": origin,
    }


class CaseStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def directory(self, case_id):
        if not isinstance(case_id, str) or not _CASE.fullmatch(case_id):
            raise ValueError("invalid case id")
        path = self.root / case_id
        if path.is_symlink():
            raise ValueError("case directory cannot be a symlink")
        return path

    def create(self, issue, repository):
        case_id = "issue_" + uuid4().hex[:24]
        path = self.directory(case_id)
        repo = Path(repository["path"])
        if self.root == repo or self.root.is_relative_to(repo):
            raise ValueError("case storage must remain outside the target repository")
        path.mkdir(parents=True, mode=0o700)
        state = {
            "schema": "repoagent.issue-case/v1",
            "case_id": case_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "received",
            "issue": issue,
            "repository": repository,
            "runs": [],
        }
        self.save(state)
        return state

    def load(self, case_id):
        state = json.loads((self.directory(case_id) / "case.json").read_text())
        if (
            state.get("case_id") != case_id
            or state.get("schema") != "repoagent.issue-case/v1"
        ):
            raise ValueError("case identity or schema mismatch")
        return state

    def save(self, state):
        atomic_replace_unlocked(
            self.directory(state["case_id"]) / "case.json",
            json.dumps(state, indent=2, ensure_ascii=True, allow_nan=False) + "\n",
        )

    @contextmanager
    def locked(self, case_id):
        directory = self.directory(case_id)
        if not directory.is_dir():
            raise ValueError("unknown case")
        with file_lock(directory / ".lock", blocking=False):
            yield self.load(case_id)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
