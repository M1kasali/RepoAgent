import subprocess
import sys

import pytest

from repoagent import (
    DirectSandboxAdapter,
    FakeModelClient,
    IsolatedSandboxAdapter,
    RepoAgent,
    SandboxConfigurationError,
    SessionStore,
    WorkspaceContext,
)
from repoagent.sandbox import DockerSandboxAdapter, build_sandbox_adapter
from repoagent.tool_execution import ToolExecutionControl
from repoagent.tool_execution import ProcessOutcome


def build_agent(tmp_path, **kwargs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return RepoAgent(
        model_client=FakeModelClient(()),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
        **kwargs,
    )


class FakeIsolatedBackend:
    is_isolated = True

    def __init__(self):
        self.calls = []

    def execute(self, command, *, cwd, env, control):
        self.calls.append((command, cwd, env, control.status()))
        return ProcessOutcome("completed", 0, "isolated output\n", "", 16, 0, False)


def test_direct_adapter_is_explicitly_not_isolated(tmp_path):
    agent = build_agent(tmp_path)
    result = agent.execute_tool(
        "run_shell",
        {"command": f'{sys.executable} -c "print(123)"', "timeout": 2},
    )

    assert isinstance(agent.sandbox_adapter, DirectSandboxAdapter)
    assert agent.sandbox_adapter.is_isolated is False
    assert result.status == "ok"
    assert result.metadata["sandbox_identity"] == "direct_host"
    assert result.metadata["sandbox_isolated"] is False


def test_isolated_adapter_routes_shell_and_records_identity(tmp_path):
    backend = FakeIsolatedBackend()
    adapter = IsolatedSandboxAdapter(backend, identity="test_vm")
    agent = build_agent(tmp_path, sandbox_adapter=adapter, require_isolation=True)

    result = agent.execute_tool("run_shell", {"command": "echo isolated", "timeout": 2})

    assert result.status == "ok"
    assert "isolated output" in result.content
    assert result.metadata["sandbox_identity"] == "test_vm"
    assert result.metadata["sandbox_isolated"] is True
    assert backend.calls[0][0] == "echo isolated"


def test_task_required_isolation_fails_closed_on_direct_host(tmp_path):
    agent = build_agent(tmp_path, require_isolation=True)

    result = agent.execute_tool(
        "run_shell", {"command": "echo must-not-run", "timeout": 2}
    )

    assert result.status == "rejected"
    assert result.error_code == "sandbox_required"
    assert result.metadata["requires_isolation"] is True
    assert result.metadata["security_event_type"] == "sandbox_required"


def test_tool_declared_isolation_fails_closed_before_mcp_call(tmp_path):
    class Client:
        def __init__(self):
            self.called = False

        def list_tools(self):
            return [
                {
                    "name": "dangerous",
                    "description": "Dangerous external action.",
                    "input_schema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                    "effect": "external",
                    "requires_isolation": True,
                }
            ]

        def call_tool(self, name, arguments, *, control):
            self.called = True
            return "should not execute"

    client = Client()
    agent = build_agent(tmp_path, mcp_servers={"remote": client})

    result = agent.execute_tool("mcp_remote_dangerous", {})

    assert result.error_code == "sandbox_required"
    assert client.called is False


def test_isolated_adapter_rejects_backend_without_capability_claim():
    class Backend:
        def execute(self, command, **kwargs):
            return None

    with pytest.raises(SandboxConfigurationError, match="is_isolated=True"):
        IsolatedSandboxAdapter(Backend())


def test_docker_adapter_builds_fail_closed_resource_bounded_command(tmp_path):
    calls = []
    cleanup = []

    def process_runner(command, **kwargs):
        calls.append((command, kwargs))
        return ProcessOutcome("completed", 0, "ok\n", "", 3, 0, False)

    def cleanup_runner(command, **kwargs):
        cleanup.append((command, kwargs))

    adapter = DockerSandboxAdapter(
        tmp_path,
        image="python:3.12-slim",
        memory="1g",
        cpus=1.5,
        pids_limit=64,
        process_runner=process_runner,
        cleanup_runner=cleanup_runner,
    )
    control = ToolExecutionControl(timeout_seconds=5, max_output_chars=100)

    result = adapter.execute(
        "printf ok",
        cwd=tmp_path,
        env={"LANG": "C.UTF-8", "API_KEY": "must-not-cross"},
        control=control,
    )

    assert result.stdout == "ok\n"
    command, kwargs = calls[0]
    assert command[:2] == ["docker", "run"]
    assert "--network" in command and command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--memory") + 1] == "1g"
    assert command[command.index("--cpus") + 1] == "1.5"
    assert command[command.index("--pids-limit") + 1] == "64"
    assert "ALL" in command
    assert "no-new-privileges" in command
    assert "LANG=C.UTF-8" in command
    assert not any("API_KEY" in item for item in command)
    assert kwargs["shell"] is False
    assert cleanup[0][0][:3] == ["docker", "rm", "--force"]


def test_docker_adapter_projects_workspace_path_for_external_cli(tmp_path):
    commands = []

    def process_runner(command, **kwargs):
        commands.append(command)
        return ProcessOutcome("completed", 0, "ok\n", "", 3, 0, False)

    adapter = DockerSandboxAdapter(
        tmp_path,
        workspace_path_converter=lambda path: r"C:\\wsl\\workspace",
        process_runner=process_runner,
        cleanup_runner=lambda *args, **kwargs: None,
    )

    adapter.execute(
        "pwd",
        cwd=tmp_path,
        env={},
        control=ToolExecutionControl(timeout_seconds=5, max_output_chars=100),
    )

    mount = commands[0][commands[0].index("--mount") + 1]
    assert mount == r"type=bind,source=C:\\wsl\\workspace,target=/workspace"


def test_docker_adapter_rejects_cwd_escape(tmp_path):
    adapter = DockerSandboxAdapter(tmp_path)
    control = ToolExecutionControl(timeout_seconds=5, max_output_chars=100)

    with pytest.raises(SandboxConfigurationError, match="inside"):
        adapter.execute("pwd", cwd=tmp_path.parent, env={}, control=control)


def test_sandbox_factory_never_falls_back_from_requested_docker(tmp_path):
    docker = build_sandbox_adapter(
        "docker", tmp_path, docker_executable="podman", docker_image="eval:1"
    )

    assert isinstance(docker, DockerSandboxAdapter)
    assert docker.is_isolated is True
    assert docker.executable == "podman"
    assert docker.identity == "docker:eval:1"
    with pytest.raises(SandboxConfigurationError, match="unsupported"):
        build_sandbox_adapter("missing", tmp_path)


def test_docker_adapter_probe_fails_before_runtime_without_daemon(
    tmp_path, monkeypatch
):
    adapter = DockerSandboxAdapter(tmp_path)

    def unavailable(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="daemon unavailable"
        )

    monkeypatch.setattr(subprocess, "run", unavailable)

    with pytest.raises(SandboxConfigurationError, match="daemon unavailable"):
        adapter.verify_available()
