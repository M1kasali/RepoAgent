"""Local verification bundles for development and evidence preparation."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .evaluation.schema import collect_environment_provenance, collect_source_provenance
from .evidence import sha256_file


@dataclass(frozen=True)
class VerificationCommand:
    name: str
    argv: tuple[str, ...]

    def __post_init__(self):
        if not self.name or not self.argv:
            raise ValueError("verification command name and argv must not be empty")
        if re.fullmatch(r"[a-z0-9][a-z0-9-]*", self.name) is None:
            raise ValueError("verification command name must be a safe lowercase identifier")
        if any(not isinstance(item, str) or not item for item in self.argv):
            raise ValueError("verification command argv must contain non-empty strings")


class VerificationRecorder:
    def __init__(self, *, repo_root, output_root):
        self.repo_root = Path(repo_root).resolve()
        self.output_root = Path(output_root).resolve()

    def run(self, commands):
        commands = tuple(commands)
        if not commands:
            raise ValueError("verification bundle requires at least one command")
        if len({command.name for command in commands}) != len(commands):
            raise ValueError("verification command names must be unique")
        if self.output_root.exists():
            raise FileExistsError(f"verification output already exists: {self.output_root}")
        temporary = self.output_root.with_name(f".{self.output_root.name}.tmp-{os.getpid()}")
        if temporary.exists():
            raise FileExistsError(f"verification temporary output already exists: {temporary}")
        temporary.mkdir(parents=True)
        try:
            rows = [self._run_one(command, temporary) for command in commands]
            files = []
            for artifact in sorted(item for item in temporary.rglob("*") if item.is_file()):
                files.append(
                    {
                        "path": artifact.relative_to(temporary).as_posix(),
                        "sha256": sha256_file(artifact),
                        "size_bytes": artifact.stat().st_size,
                    }
                )
            manifest = {
                "schema": "repoagent.verification-bundle/v1",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "source": collect_source_provenance(self.repo_root),
                "environment": collect_environment_provenance(self.repo_root),
                "status": "pass" if all(row["exit_code"] == 0 for row in rows) else "fail",
                "commands": rows,
                "files": files,
                "publishable": False,
                "limitations": [
                    "Local verification output may contain machine paths or command output.",
                    "Resume claims require a separate clean-tag release evidence bundle.",
                ],
            }
            (temporary / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.output_root.parent.mkdir(parents=True, exist_ok=True)
            temporary.replace(self.output_root)
            return self.output_root / "manifest.json"
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def _run_one(self, command, temporary):
        if not isinstance(command, VerificationCommand):
            raise TypeError("verification recorder requires VerificationCommand entries")
        argv = tuple(item.replace("{output}", str(temporary)) for item in command.argv)
        started = time.perf_counter()
        result = subprocess.run(
            argv,
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        duration = time.perf_counter() - started
        stdout_path = temporary / f"{command.name}.stdout.txt"
        stderr_path = temporary / f"{command.name}.stderr.txt"
        stdout_path.write_text(result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        return {
            "name": command.name,
            "argv": list(command.argv),
            "exit_code": result.returncode,
            "duration_seconds": round(duration, 6),
            "stdout": stdout_path.name,
            "stderr": stderr_path.name,
        }


__all__ = ["VerificationCommand", "VerificationRecorder"]
