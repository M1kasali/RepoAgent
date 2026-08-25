"""Release source preflight shared by local tooling and CI."""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by the Python 3.10 CI job
    import tomli as tomllib


SUPPORTED_PYTHON = ("3.10", "3.11", "3.12")
SUPPORTED_OS = ("ubuntu-latest", "windows-latest")


class ReleaseCheckError(RuntimeError):
    pass


def _git(root, *args):
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise ReleaseCheckError(result.stderr.strip() or "git release check failed")
    return result.stdout.strip()


def inspect_release_source(repo_root, *, tag=None):
    root = Path(repo_root).resolve()
    commit_sha = _git(root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit_sha):
        raise ReleaseCheckError("release source has no exact commit identity")
    status = _git(root, "status", "--porcelain", "--untracked-files=all")
    if status:
        raise ReleaseCheckError("release source tree must be clean")
    lock_path = root / "uv.lock"
    if not lock_path.is_file():
        raise ReleaseCheckError("release source is missing uv.lock")
    tracked = _git(root, "ls-files", "--error-unmatch", "uv.lock")
    if tracked != "uv.lock":
        raise ReleaseCheckError("uv.lock must be tracked")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    version = str(project["version"])
    expected_tag = f"v{version}"
    if tag is not None:
        tag = str(tag)
        if tag != expected_tag:
            raise ReleaseCheckError(
                f"release tag {tag!r} must match package version {expected_tag!r}"
            )
        tagged_commit = _git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}")
        if tagged_commit != commit_sha:
            raise ReleaseCheckError("release tag does not point to HEAD")
    return {
        "schema": "repoagent.release-source/v1",
        "status": "pass",
        "commit_sha": commit_sha,
        "tag": str(tag or ""),
        "package": str(project["name"]),
        "version": version,
        "python": list(SUPPORTED_PYTHON),
        "os": list(SUPPORTED_OS),
    }


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Validate RepoAgent release source.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--tag", default=None)
    args = parser.parse_args(argv)
    try:
        payload = inspect_release_source(args.repo_root, tag=args.tag)
    except (OSError, ValueError, ReleaseCheckError) as exc:
        print(str(exc))
        return 2
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


__all__ = [
    "SUPPORTED_OS",
    "SUPPORTED_PYTHON",
    "ReleaseCheckError",
    "inspect_release_source",
    "main",
]
