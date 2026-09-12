"""Verify a local smoke receipt and write a separate offline verdict/replay."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repoagent.evaluation.coding_smoke import (
    classify_coding_smoke,
    replay_history_retention,
)


def inspect(root):
    root = Path(root).resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    files = manifest["sha256"]
    for relative, digest in files.items():
        path = root / relative
        if (
            Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or any(p.is_symlink() for p in (path, *path.parents))
        ):
            raise ValueError("unsafe receipt path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("receipt digest mismatch")

    def read(relative):
        if relative not in files:
            raise ValueError("unverified receipt input")
        return json.loads((root / relative).read_text())

    reports = [
        name
        for name in files
        if name.startswith("workspace/.repoagent/runs/")
        and name.endswith("/report.json")
    ]
    if len(reports) != 1:
        raise ValueError("expected one Runtime report")
    report = read(reports[0])
    summary = read("summary.json")
    check = read("independent-tests.json")
    session_id = summary["session_id"]
    if "/" in session_id or "\\" in session_id or session_id in {".", ".."}:
        raise ValueError("invalid session identity")
    session = read(f"workspace/.repoagent/sessions/{session_id}.json")
    verdict = classify_coding_smoke(
        report,
        check_exit_code=check["exit_code"],
        tests_unchanged=summary["tests_preserved"],
        error=summary.get("error", ""),
    )
    return {
        "verified_files": len(files),
        "verdict": verdict,
        "replay": replay_history_retention(session["history"]),
        "source_manifest_sha256": hashlib.sha256(
            (root / "manifest.json").read_bytes()
        ).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = inspect(args.root)
    with Path(args.output).open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(result["verdict"], indent=2))


if __name__ == "__main__":
    main()
