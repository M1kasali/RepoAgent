import json
import sys

import pytest

from repoagent.verification import VerificationCommand, VerificationRecorder


def test_verification_recorder_retains_passes_failures_and_checksums(tmp_path):
    output = tmp_path / "bundle"
    manifest_path = VerificationRecorder(repo_root=".", output_root=output).run(
        (
            VerificationCommand("pass", (sys.executable, "-c", "print('ok')")),
            VerificationCommand(
                "fail",
                (sys.executable, "-c", "import sys; print('bad', file=sys.stderr); sys.exit(3)"),
            ),
        )
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "fail"
    assert manifest["publishable"] is False
    assert [row["exit_code"] for row in manifest["commands"]] == [0, 3]
    assert (output / "pass.stdout.txt").read_text(encoding="utf-8") == "ok\n"
    assert (output / "fail.stderr.txt").read_text(encoding="utf-8") == "bad\n"
    assert {item["path"] for item in manifest["files"]} == {
        "fail.stderr.txt",
        "fail.stdout.txt",
        "pass.stderr.txt",
        "pass.stdout.txt",
    }
    assert all(item["sha256"].startswith("sha256:") for item in manifest["files"])


def test_verification_recorder_rejects_ambiguous_or_existing_outputs(tmp_path):
    command = VerificationCommand("same", (sys.executable, "-c", "pass"))
    recorder = VerificationRecorder(repo_root=".", output_root=tmp_path / "bundle")
    with pytest.raises(ValueError, match="unique"):
        recorder.run((command, command))
    (tmp_path / "bundle").mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        recorder.run((command,))
    with pytest.raises(ValueError, match="safe lowercase"):
        VerificationCommand("../escape", (sys.executable, "-c", "pass"))
