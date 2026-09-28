"""Trial isolation including fields retained for historical EverOS campaigns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class TrialIsolation:
    root: Path
    workspace: Path
    pico_home: Path
    everos_root: Path
    session_root: Path
    trace_root: Path
    evidence_root: Path

    @classmethod
    def create(cls, root: Path, attempt_id: str) -> TrialIsolation:
        if not _SAFE_ID.fullmatch(attempt_id):
            raise ValueError(f"attempt_id must be a portable identifier: {attempt_id!r}")
        trial_root = Path(root) / attempt_id
        return cls(
            root=trial_root,
            workspace=trial_root / "workspace",
            pico_home=trial_root / "pico-home",
            everos_root=trial_root / "everos",
            session_root=trial_root / "sessions",
            trace_root=trial_root / "traces",
            evidence_root=trial_root / "evidence",
        )

    def prepare(self) -> None:
        for path in (
            self.workspace,
            self.pico_home,
            self.everos_root,
            self.session_root,
            self.trace_root,
            self.evidence_root,
        ):
            path.mkdir(parents=True, exist_ok=False)

    def child_environment(self) -> dict[str, str]:
        return {
            "REPOAGENT_HARNESS_HOME": str(self.pico_home),
            "EVEROS_ROOT": str(self.everos_root),
            "REPOAGENT_HARNESS_TRACE_ROOT": str(self.trace_root),
        }
