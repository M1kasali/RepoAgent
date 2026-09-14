import os
from pathlib import Path
import subprocess

import pytest

from repoagent.issue_agent.cases import bind_repository
from repoagent.issue_agent.execution import (
    collect_changes,
    export_revision,
    inventory,
    make_patch,
    verify,
)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "module.py").write_text("VALUE = False\n")

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True
        ).strip()

    git("init", "-q")
    git("add", ".")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "base",
    )
    git("remote", "add", "origin", "https://github.com/example/project.git")
    return bind_repository(root, git("rev-parse", "HEAD"), "example/project")


@pytest.mark.parametrize(
    "before,after",
    [
        ("before", "after"),
        ("before\n", "after"),
        ("before", "after\n"),
        ("a\r\nb\r\n", "a\r\nc\r\n"),
    ],
)
def test_deliverable_patch_applies_exact_bytes(tmp_path, before, after):
    path = tmp_path / "main.py"
    path.write_bytes(before.encode())
    patch = make_patch(tmp_path, {"main.py": after})
    subprocess.run(
        ["git", "apply", "--no-index", "-"],
        cwd=tmp_path,
        input=patch.encode(),
        capture_output=True,
        check=True,
    )
    assert path.read_bytes() == after.encode()


def test_export_uses_pinned_commit_not_local_modifications(source, tmp_path):
    (Path(source["path"]) / "module.py").write_text("uncommitted")
    export_revision(source, tmp_path / "export")
    assert (tmp_path / "export/module.py").read_text() == "VALUE = False\n"


def test_candidate_symlink_rejected(tmp_path):
    (tmp_path / "link").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="symlink"):
        inventory(tmp_path)


def test_wrong_tree_rejected(source, tmp_path):
    with pytest.raises(ValueError, match="tree changed"):
        export_revision(dict(source, tree="0" * 40), tmp_path / "export")


@pytest.mark.parametrize(
    "operation", ["edit-protected", "new", "delete", "investigation-edit"]
)
def test_out_of_scope_candidate_is_rejected(tmp_path, operation):
    (tmp_path / "main.py").write_text("original")
    (tmp_path / "test.py").write_text("trusted test")
    before = inventory(tmp_path)
    allowed = ["main.py"]
    if operation == "edit-protected":
        (tmp_path / "test.py").write_text("pass")
    elif operation == "new":
        (tmp_path / "extra.py").write_text("new")
    elif operation == "delete":
        (tmp_path / "main.py").unlink()
    else:
        allowed = []
        (tmp_path / "main.py").write_text("changed")
    with pytest.raises(ValueError, match="scope"):
        collect_changes(before, tmp_path, allowed)


def test_allowed_edit_and_scratch_script(tmp_path):
    (tmp_path / "main.py").write_text("original")
    before = inventory(tmp_path)
    (tmp_path / "main.py").write_text("changed")
    (tmp_path / ".issue").mkdir()
    (tmp_path / ".issue/repro.py").write_text("check")
    assert collect_changes(before, tmp_path, ["main.py"]) == {"main.py": "changed"}


@pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"), reason="opt-in Docker integration"
)
def test_real_docker_clean_verification_and_readonly_probe(source, tmp_path):
    config = {
        "image": "python@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17",
        "pythonpath": "src",
        "mutable_paths": ["module.py"],
        "failure_marker": "BUG",
        "probe": "import runpy\nassert runpy.run_path('module.py')['VALUE'], 'BUG'",
    }
    before = verify(tmp_path / "before", source, config)
    after = verify(tmp_path / "after", source, config, {"module.py": "VALUE = True\n"})
    assert before["reproduced"] and not before["passed"]
    assert after["passed"] and not after["reproduced"]
    assert (Path(source["path"]) / "module.py").read_text() == "VALUE = False\n"
    config["probe"] = "from pathlib import Path\nPath(__file__).write_text('tampered')"
    tampered = verify(tmp_path / "tampered", source, config)
    assert not tampered["passed"] and "Read-only file system" in tampered["stderr"]
    config["probe"] = "import os\nos._exit(0)"
    assert not verify(tmp_path / "early-exit", source, config)["passed"]


@pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"), reason="opt-in Docker integration"
)
def test_native_worker_keeps_secret_and_oracle_on_host(source, tmp_path, monkeypatch):
    from repoagent.evolver.model_budget import (
        BudgetedEvaluationClient,
        EvaluationModelLimits,
    )
    from repoagent.issue_agent.execution import run_agent
    from repoagent.pricing import ModelPricing
    from repoagent.providers.base import ModelResult, ModelUsage, ToolCall, UsageSource

    monkeypatch.setenv("ISSUE_DEMO_SECRET", "must-not-enter-worker")

    class Leaf:
        model = "fixture"
        calls = 0

        def generate(self, request):
            assert "PRIVATE_ORACLE_SENTINEL" not in request.prompt
            assert "must-not-enter-worker" not in request.prompt
            self.calls += 1
            usage = ModelUsage(
                input_tokens=10,
                output_tokens=10,
                total_tokens=20,
                source=UsageSource.ACTUAL,
            )
            if self.calls == 1:
                return ModelResult(
                    tool_calls=(
                        ToolCall(
                            "check",
                            "run_shell",
                            {
                                "command": "python -c \"import os; assert 'ISSUE_DEMO_SECRET' not in os.environ; print('ISOLATION_OK')\""
                            },
                        ),
                    ),
                    usage=usage,
                    model=self.model,
                )
            assert any(
                "ISOLATION_OK" in m.content
                for m in request.messages
                if m.role == "tool"
            )
            return ModelResult(text="Isolation checked.", usage=usage, model=self.model)

    client = BudgetedEvaluationClient(
        Leaf(),
        limits=EvaluationModelLimits(
            max_calls=3, max_input_tokens=128000, max_output_tokens=4096
        ),
        pricing=ModelPricing(
            0.3, 1.2, "fixture", cache_read_per_1m_usd=0.006, cache_write_per_1m_usd=0.3
        ),
        request_token_counter=lambda request: 100,
        counter_identity="fixture",
    )
    directory = tmp_path / "native"
    directory.mkdir()
    result = run_agent(
        directory,
        {"repository": source, "issue": {"title": "fixture", "body": "inspect"}},
        {
            "image": "python@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17",
            "pythonpath": "src",
            "mutable_paths": ["module.py"],
            "probe": "PRIVATE_ORACLE_SENTINEL",
        },
        client,
        "investigate",
    )
    assert result["worker"]["agent_status"] == "completed"
    assert result["model"]["calls_reserved"] == 2
    assert result["changes"] == {}
