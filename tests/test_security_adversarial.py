from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext


def build_agent(tmp_path, **kwargs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return RepoAgent(
        model_client=FakeModelClient(()),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        **kwargs,
    )


def test_shell_command_injection_cannot_bypass_approval(tmp_path):
    agent = build_agent(tmp_path, approval_policy="never")
    marker = tmp_path / "owned.txt"

    result = agent.execute_tool(
        "run_shell", {"command": f"echo owned > {marker}", "timeout": 2}
    )

    assert result.status == "rejected"
    assert result.error_code == "approval_denied"
    assert not marker.exists()


def test_direct_shell_does_not_receive_secret_environment(tmp_path, monkeypatch):
    secret = "repoagent-adversarial-secret"
    monkeypatch.setenv("REPOAGENT_PRIVATE_TOKEN", secret)
    agent = build_agent(tmp_path, approval_policy="auto")

    result = agent.execute_tool(
        "run_shell",
        {
            "command": (
                "python3 -c \"import os; "
                "print(os.getenv('REPOAGENT_PRIVATE_TOKEN', 'missing'))\""
            ),
            "timeout": 2,
        },
    )

    assert secret not in result.content
    assert "missing" in result.content


def test_managed_worktree_symlink_escape_is_rejected(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside-worktrees"
    outside.mkdir()
    state = tmp_path / ".repoagent"
    state.mkdir(exist_ok=True)
    (state / "worktrees").symlink_to(outside, target_is_directory=True)
    agent = build_agent(tmp_path, approval_policy="auto")

    result = agent.execute_tool(
        "git_worktree_create", {"name": "escaped"}
    )

    assert result.status == "rejected"
    assert result.error_code == "invalid_arguments"
    assert "path escapes workspace" in result.content
    assert list(outside.iterdir()) == []


def test_trace_redacts_secret_shaped_mcp_output(tmp_path, monkeypatch):
    secret = "mcp-secret-value"
    monkeypatch.setenv("REMOTE_API_KEY", secret)

    class Client:
        def list_tools(self):
            return [
                {
                    "name": "read",
                    "description": "Read remote data.",
                    "input_schema": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                    "effect": "read",
                }
            ]

        def call_tool(self, name, arguments, *, control):
            return secret

    agent = build_agent(
        tmp_path,
        approval_policy="auto",
        mcp_servers={"remote": Client()},
    )
    result = agent.execute_tool("mcp_remote_read", {})

    redacted = agent.redact_artifact(result.to_dict())
    assert secret not in str(redacted)
    assert "<redacted>" in str(redacted)
