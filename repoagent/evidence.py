"""Self-contained, checksummed evidence bundles for one Turn."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .run_store import TERMINAL_EVENT_KINDS


EVIDENCE_BUNDLE_FORMAT = "repoagent.evidence-bundle/v1"
EVIDENCE_FILENAMES = (
    "turn.json",
    "turn_events.jsonl",
    "task_state.json",
    "trace.jsonl",
    "calls.jsonl",
    "report.json",
)


class IncompleteEvidenceError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


class EvidenceBundleBuilder:
    def __init__(self, run_store) -> None:
        self.run_store = run_store

    def build(self, run_id, destination) -> Path:
        run_id = str(run_id)
        source = self.run_store.run_dir(run_id)
        if not source.is_dir():
            raise FileNotFoundError(f"run evidence does not exist: {run_id}")
        turn_events = self.run_store.load_turn_events(run_id)
        if not turn_events or turn_events[-1].get("kind") not in TERMINAL_EVENT_KINDS:
            raise IncompleteEvidenceError(
                f"run {run_id} has no terminal Turn evidence"
            )

        destination = Path(destination)
        if destination.exists():
            raise FileExistsError(f"evidence destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".{destination.name}.tmp-{uuid4().hex}"
        temporary.mkdir()
        try:
            files = []
            for filename in EVIDENCE_FILENAMES:
                source_path = source / filename
                if not source_path.is_file():
                    continue
                target = temporary / filename
                shutil.copyfile(source_path, target)
                files.append(
                    {
                        "path": filename,
                        "sha256": sha256_file(target),
                        "size_bytes": target.stat().st_size,
                    }
                )
            manifest = {
                "schema": EVIDENCE_BUNDLE_FORMAT,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "run_id": run_id,
                "trace_id": str(turn_events[0].get("trace_id", "")),
                "terminal_kind": turn_events[-1]["kind"],
                "event_count": len(turn_events),
                "files": files,
            }
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True)
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(destination)
        except BaseException:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        return destination


def verify_evidence_bundle(path) -> dict:
    root = Path(path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != EVIDENCE_BUNDLE_FORMAT:
        raise ValueError("unsupported evidence bundle schema")
    for record in manifest.get("files", []):
        relative = Path(str(record.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("evidence manifest contains an unsafe path")
        artifact = root / relative
        if not artifact.is_file():
            raise ValueError(f"evidence artifact is missing: {relative.as_posix()}")
        if sha256_file(artifact) != record.get("sha256"):
            raise ValueError(f"evidence checksum mismatch: {relative.as_posix()}")
        if artifact.stat().st_size != int(record.get("size_bytes", -1)):
            raise ValueError(f"evidence size mismatch: {relative.as_posix()}")
    return manifest


__all__ = [
    "EVIDENCE_BUNDLE_FORMAT",
    "EvidenceBundleBuilder",
    "IncompleteEvidenceError",
    "sha256_file",
    "verify_evidence_bundle",
]
