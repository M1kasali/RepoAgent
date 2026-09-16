"""Materialize curated fixtures from the bound Git tree, not a repair workspace."""

from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from ..evolver.contracts import sha256_bytes
from .campaign import snapshot_task
from .execution import export_revision


def materialize_task(repository, specification):
    """Operator supplies prompt/checks and literal source paths, never source text.

    The caller must keep check programs and provenance outside model workspaces.
    Source extraction proves file identity, not that the checks cover the Issue.
    """
    if specification.get("base_revision") != repository["base_revision"]:
        raise ValueError("task and bound source revision differ")
    paths = specification.get("source_paths")
    if not isinstance(paths, list) or not paths or len(paths) > 100 or len(set(paths)) != len(paths):
        raise ValueError("declare unique bounded source paths")
    if "files" in specification:
        raise ValueError("source text must come from the pinned Git tree")
    for name in paths:
        path = PurePosixPath(name)
        if (not name or path.is_absolute() or path.as_posix() != name
                or any(p in {"..", ".git", ".repoagent"} for p in path.parts)
                or "\\" in name or "\0" in name):
            raise ValueError("source path must be canonical and relative")
    with TemporaryDirectory(prefix="repoagent-issue-source-") as directory:
        export_revision(repository, Path(directory))
        files = {}
        hashes = {}
        total = 0
        for name in paths:
            source = Path(directory) / name
            if not source.is_file():
                raise ValueError("declared source is not a regular file")
            content = source.read_bytes()
            total += len(content)
            if total > 1_000_000:
                raise ValueError("source fixtures exceed snapshot budget")
            files[name] = content.decode("utf-8")
            hashes[name] = sha256_bytes(content)
    row = {key: value for key, value in specification.items() if key != "source_paths"}
    row["files"] = files
    row["source_provenance"] = {
        "base_revision": repository["base_revision"], "tree": repository["tree"],
        "file_digests": hashes,
    }
    snapshot_task(row)
    return row
