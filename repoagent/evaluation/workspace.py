"""Isolated trial workspaces and append-only raw row persistence."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..atomic_io import append_jsonl
from .schema import EvaluationRow


@dataclass
class TrialWorkspace:
    root: Path
    task_id: str
    variant: str
    repetition: int
    _temporary: tempfile.TemporaryDirectory | None = None

    @classmethod
    def create(cls, fixture, *, task_id, variant, repetition, parent=None):
        fixture = Path(fixture).resolve()
        if not fixture.is_dir():
            raise FileNotFoundError(f"trial fixture does not exist: {fixture}")
        temporary = None
        if parent is None:
            temporary = tempfile.TemporaryDirectory(prefix="repoagent-trial-")
            base = Path(temporary.name)
        else:
            base = Path(parent)
            base.mkdir(parents=True, exist_ok=True)
        root = base / str(task_id) / str(variant) / f"repeat-{int(repetition)}"
        if root.exists():
            raise FileExistsError(f"trial workspace already exists: {root}")
        root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(fixture, root)
        return cls(root, str(task_id), str(variant), int(repetition), temporary)

    def close(self):
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


class RawRowWriter:
    def __init__(self, path):
        self.path = Path(path)

    def append(self, row: EvaluationRow):
        if not isinstance(row, EvaluationRow):
            raise TypeError("raw row writer requires EvaluationRow")
        append_jsonl(self.path, row.to_dict())

    def load(self):
        if not self.path.exists():
            return []
        return [
            EvaluationRow(**json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


__all__ = ["RawRowWriter", "TrialWorkspace"]
