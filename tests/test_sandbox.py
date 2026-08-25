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
        return ProcessOutcome(
            "completed", 0, "isolated output\n", "", 16, 0, False
        )


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

    result = agent.execute_tool(
        "run_shell", {"command": "echo isolated", "timeout": 2}
    )

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
