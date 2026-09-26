"""Real AgentLoop, microVM tools and JSONL resume across OS processes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest


@pytest.mark.skipif(
    os.environ.get("REPOAGENT_BOXLITE_LIVE") != "1", reason="requires real BoxLite VM"
)
def test_coding_mcp_delegate_and_jsonl_resume_across_processes():
    worker = Path(__file__).parent / "fixtures/boxlite_e2e_worker.py"
    with tempfile.TemporaryDirectory(prefix="ra-e2e-", dir="/tmp") as directory:
        root = Path(directory)
        for phase in ("create", "resume"):
            result = subprocess.run(
                [sys.executable, str(worker), phase, directory],
                capture_output=True,
                text=True,
                timeout=420,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            assert result.returncode == 0, f"{phase}:\n{result.stdout}\n{result.stderr}"
            print(result.stdout)
            if phase == "create":
                before = json.loads((root / "create.json").read_text())
                session_before = (root / before["session_path"]).read_bytes()
        after = json.loads((root / "resume.json").read_text())
        assert before["pid"] != after["pid"]
        assert before["session_id"] == after["session_id"]
        assert after["history_count"] > before["history_count"]
        assert before["vm_cleanup_confirmed"] and after["vm_cleanup_confirmed"]
        assert (root / after["session_path"]).read_bytes().startswith(session_before)
