from pathlib import Path

import pytest

from repoagent.evaluation.container import (
    ContainerConfigurationError,
    DockerContainerRunner,
    is_immutable_container_image,
    wsl_windows_path,
)
from repoagent.tool_execution import ProcessOutcome, ToolExecutionControl


class FakeProcessRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, command, *, cwd, env, shell, control):
        self.calls.append((command, cwd, env, shell, control))
        mount = command[command.index("--mount") + 1]
        source = Path(mount.split("source=", 1)[1].split(",target=", 1)[0])
        assert mount.endswith(",target=/workspace/workspace")
        assert (source / "solution.py").read_text(encoding="utf-8") == "pass\n"
        assert not (source / ".env").exists()
        assert not (source / ".repoagent").exists()
        return ProcessOutcome("completed", 0, "tests passed\n", "", 13, 0, False)


class FakeCleanupRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))


def test_container_runner_stages_filtered_workspace_and_applies_security_flags(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "solution.py").write_text("pass\n", encoding="utf-8")
    (workspace / ".env").write_text("SECRET=value\n", encoding="utf-8")
    (workspace / ".repoagent").mkdir()
    (workspace / ".repoagent" / "state.json").write_text("{}\n", encoding="utf-8")
    process = FakeProcessRunner()
    cleanup = FakeCleanupRunner()
    runner = DockerContainerRunner(
        docker_executable="docker-test",
        image="benchmark@sha256:" + "a" * 64,
        staging_root=tmp_path / "staging",
        process_runner=process,
        cleanup_runner=cleanup,
    )
    (tmp_path / "staging").mkdir()
    outcome = runner.execute(
        "python -m pytest",
        cwd=workspace,
        env={"LANG": "C.UTF-8"},
        control=ToolExecutionControl(timeout_seconds=10, max_output_chars=1000),
    )
    command = process.calls[0][0]
    assert outcome.exit_code == 0
    assert runner.is_isolated is True
    assert runner.identity == "docker:benchmark@sha256:" + "a" * 64
    assert runner.image_is_immutable is True
    assert command[:2] == ["docker-test", "run"]
    for expected in ("--network", "--read-only", "--cap-drop", "--pids-limit", "--memory", "--cpus"):
        assert expected in command
    assert command[-3:] == ["sh", "-lc", "python -m pytest"]
    assert command[command.index("--workdir") + 1] == "/workspace/workspace"
    assert cleanup.calls[0][0][:3] == ["docker-test", "rm", "--force"]
    assert cleanup.calls[1][0][:3] == ["docker-test", "run", "--rm"]
    assert "chmod -R u+w /workspace/workspace" in cleanup.calls[1][0][-1]


def test_container_runner_rejects_symlinks_and_invalid_limits(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "outside").write_text("x", encoding="utf-8")
    (workspace / "linked").symlink_to(workspace / "outside")
    runner = DockerContainerRunner(process_runner=FakeProcessRunner())
    with pytest.raises(ValueError, match="symlinks"):
        runner.execute(
            "true",
            cwd=workspace,
            env={},
            control=ToolExecutionControl(timeout_seconds=1, max_output_chars=10),
        )
    with pytest.raises(ContainerConfigurationError, match="memory"):
        DockerContainerRunner(memory="unbounded")
    with pytest.raises(ContainerConfigurationError, match="CPUs"):
        DockerContainerRunner(cpus=0)
    with pytest.raises(ContainerConfigurationError, match="basename"):
        DockerContainerRunner._container_workspace("unsafe workspace")


def test_wsl_windows_path_returns_nonempty_windows_path(tmp_path):
    converted = wsl_windows_path(tmp_path)
    assert converted.startswith("\\\\wsl.localhost\\") or ":\\" in converted


def test_formal_container_image_identity_requires_digest_reference():
    assert is_immutable_container_image("repoagent-polyglot@sha256:" + "a" * 64)
    assert not is_immutable_container_image("repoagent-polyglot:latest")
    assert not is_immutable_container_image("repoagent-polyglot@sha256:short")
