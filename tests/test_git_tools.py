import subprocess

from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext


def build_git_agent(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "repoagent@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "RepoAgent Tests"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / ".gitignore").write_text(".repoagent/\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    return RepoAgent(
        model_client=FakeModelClient(()),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )


def test_git_status_and_diff_are_structured_read_tools(tmp_path):
    agent = build_git_agent(tmp_path)
    (tmp_path / "README.md").write_text("changed\n", encoding="utf-8")

    status = agent.execute_tool("git_status", {})
    diff = agent.execute_tool("git_diff", {"path": "README.md"})

    assert status.status == "ok"
    assert "README.md" in status.content
    assert status.metadata["git_operation"] == "status"
    assert diff.status == "ok"
    assert "-original" in diff.content
    assert "+changed" in diff.content
    assert diff.effect.value == "read"


def test_git_worktree_create_list_and_remove_use_managed_location(tmp_path):
    agent = build_git_agent(tmp_path)

    created = agent.execute_tool("git_worktree_create", {"name": "feature-one"})
    listed = agent.execute_tool("git_worktree_list", {})
    removed = agent.execute_tool("git_worktree_remove", {"name": "feature-one"})

    target = tmp_path / ".repoagent" / "worktrees" / "feature-one"
    assert created.status == "ok"
    assert "repoagent/feature-one" in listed.content
    assert removed.status == "ok"
    assert not target.exists()
    assert created.metadata["approval"]["required"] is True


def test_git_worktree_name_rejects_argument_injection(tmp_path):
    agent = build_git_agent(tmp_path)

    result = agent.execute_tool(
        "git_worktree_create", {"name": "bad;touch-owned"}
    )

    assert result.status == "rejected"
    assert result.error_code == "invalid_arguments"
    assert not (tmp_path / "owned").exists()
