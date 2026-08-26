import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext
from repoagent.workspace_checkpoint import (
    WorkspaceCheckpointService,
    checkpoint_policy_active,
)


def _user_git_env():
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "user",
        "GIT_AUTHOR_EMAIL": "user@example.com",
        "GIT_COMMITTER_NAME": "user",
        "GIT_COMMITTER_EMAIL": "user@example.com",
    }


def _git(cwd, *args):
    return subprocess.run(
        ("git", *args),
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=_user_git_env(),
    ).stdout.strip()


def test_workspace_checkpoint_commits_changes_and_noops_when_unchanged(tmp_path):
    service = WorkspaceCheckpointService(
        tmp_path, state_root=tmp_path / ".repoagent"
    )
    (tmp_path / "a.py").write_text("print(1)\n", encoding="utf-8")

    first = service.commit_turn("turn one")
    second = service.commit_turn("turn two")

    assert first.status == "committed"
    assert first.checkpoint_id
    assert first.edited_files == ("a.py",)
    assert second.status == "unchanged"
    assert second.checkpoint_id == ""


def test_workspace_checkpoint_never_touches_user_git(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "--allow-empty", "-qm", "base")
    before_head = _git(tmp_path, "rev-parse", "HEAD")
    before_count = _git(tmp_path, "rev-list", "--count", "HEAD")
    service = WorkspaceCheckpointService(
        tmp_path, state_root=tmp_path / ".repoagent"
    )
    (tmp_path / "b.py").write_text("value = 1\n", encoding="utf-8")

    result = service.commit_turn("snapshot")

    assert result.status == "committed"
    assert result.edited_files == ("b.py",)
    assert _git(tmp_path, "rev-parse", "HEAD") == before_head
    assert _git(tmp_path, "rev-list", "--count", "HEAD") == before_count


def test_workspace_checkpoint_captures_deletion(tmp_path):
    service = WorkspaceCheckpointService(
        tmp_path, state_root=tmp_path / ".repoagent"
    )
    path = tmp_path / "deleted.py"
    path.write_text("old\n", encoding="utf-8")
    assert service.commit_turn("initial").status == "committed"

    path.unlink()
    deleted = service.commit_turn("delete")

    assert deleted.status == "committed"
    assert deleted.edited_files == ("deleted.py",)


def test_workspace_checkpoint_honors_ignore_and_secret_defaults(tmp_path):
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    (tmp_path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    (tmp_path / "kept.txt").write_text("kept\n", encoding="utf-8")
    service = WorkspaceCheckpointService(
        tmp_path, state_root=tmp_path / ".repoagent"
    )

    result = service.commit_turn("filtered")

    assert result.status == "committed"
    assert result.edited_files == (".gitignore", "kept.txt")


def test_workspace_checkpoint_preserves_unicode_and_symlink_paths(tmp_path):
    target = tmp_path / "测试 文件.txt"
    target.write_text("content\n", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(target.name)
    service = WorkspaceCheckpointService(
        tmp_path, state_root=tmp_path / ".repoagent"
    )

    result = service.commit_turn("unicode")

    assert result.status == "committed"
    assert result.edited_files == ("link.txt", "测试 文件.txt")


def test_workspace_checkpoint_writes_notice_and_gc_configuration(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    service = WorkspaceCheckpointService(
        tmp_path, state_root=tmp_path / ".repoagent"
    )

    assert service.commit_turn("turn").status == "committed"

    notice = tmp_path / ".repoagent" / "NOTICE.txt"
    assert "RepoAgent" in notice.read_text(encoding="utf-8")
    assert service._git("config", "--get", "gc.auto").stdout.strip() == "256"


def test_workspace_checkpoint_runs_periodic_gc(tmp_path, monkeypatch):
    import repoagent.workspace_checkpoint as checkpoint_module

    monkeypatch.setattr(checkpoint_module, "_GC_EVERY_N_COMMITS", 3)
    service = WorkspaceCheckpointService(
        tmp_path, state_root=tmp_path / ".repoagent"
    )
    real_git = service._git
    gc_calls = []

    def recording_git(*args):
        if args[:2] == ("gc", "--auto"):
            gc_calls.append(args)
        return real_git(*args)

    monkeypatch.setattr(service, "_git", recording_git)
    for index in range(7):
        (tmp_path / f"f{index}.py").write_text(f"{index}\n", encoding="utf-8")
        assert service.commit_turn(f"turn {index}").status == "committed"

    assert gc_calls == [("gc", "--auto"), ("gc", "--auto")]


def test_workspace_checkpoint_degrades_when_git_is_missing(tmp_path, monkeypatch):
    service = WorkspaceCheckpointService(
        tmp_path, state_root=tmp_path / ".repoagent"
    )
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", missing)
    result = service.commit_turn("turn")

    assert result.status == "unavailable"
    assert result.checkpoint_id == ""
    assert result.error == "FileNotFoundError"


def test_workspace_checkpoint_does_not_create_missing_workspace(tmp_path):
    workspace = tmp_path / "missing"
    service = WorkspaceCheckpointService(
        workspace, state_root=workspace / ".repoagent"
    )

    result = service.commit_turn("turn")

    assert result.status == "unavailable"
    assert not workspace.exists()


def test_workspace_checkpoint_degrades_on_git_timeout(tmp_path, monkeypatch):
    service = WorkspaceCheckpointService(
        tmp_path, state_root=tmp_path / ".repoagent"
    )

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 30)

    monkeypatch.setattr(subprocess, "run", timeout)
    result = service.commit_turn("turn")

    assert result.status == "unavailable"
    assert result.error == "TimeoutExpired"


def test_workspace_checkpoint_serializes_same_service_commits(tmp_path):
    service = WorkspaceCheckpointService(
        tmp_path, state_root=tmp_path / ".repoagent"
    )
    (tmp_path / "seed.py").write_text("0\n", encoding="utf-8")
    assert service.commit_turn("baseline").status == "committed"
    (tmp_path / "a.py").write_text("1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("2\n", encoding="utf-8")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(service.commit_turn, ("one", "two")))

    assert {result.status for result in results} == {"committed", "unchanged"}
    committed = next(result for result in results if result.status == "committed")
    assert committed.edited_files == ("a.py", "b.py")


def test_workspace_checkpoint_rejects_escape_paths(tmp_path):
    for shadow_dir in ("", ".", "../shared.git", str(tmp_path / "absolute")):
        try:
            WorkspaceCheckpointService(
                tmp_path,
                state_root=tmp_path / ".repoagent",
                shadow_dir=shadow_dir,
            )
        except ValueError:
            continue
        raise AssertionError(f"accepted unsafe shadow_dir: {shadow_dir!r}")


def test_checkpoint_policy_matches_runtime_mode():
    assert checkpoint_policy_active("always", interactive=False) is True
    assert checkpoint_policy_active("interactive", interactive=True) is True
    assert checkpoint_policy_active("interactive", interactive=False) is False
    assert checkpoint_policy_active("never", interactive=True) is False


def test_agent_turn_persists_workspace_checkpoint_evidence(tmp_path):
    (tmp_path / "README.md").write_text("baseline\n", encoding="utf-8")
    model = FakeModelClient(
        [
            '<tool>{"name":"write_file","args":{"path":"result.txt","content":"done\\n"}}</tool>',
            "<final>Finished.</final>",
        ]
    )
    agent = RepoAgent(
        model_client=model,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
        checkpoint_policy="interactive",
        interactive=True,
    )

    assert agent.ask("Create result.txt") == "Finished."

    state = agent.current_task_state
    assert state.workspace_checkpoint_status == "committed"
    assert state.workspace_checkpoint_id
    assert state.edited_files == ["result.txt"]
    report = json.loads(
        agent.run_store.report_path(state).read_text(encoding="utf-8")
    )
    assert report["workspace_checkpoint"] == {
        "status": "committed",
        "checkpoint_id": state.workspace_checkpoint_id,
        "edited_files": ["result.txt"],
    }
    trace = [
        json.loads(line)
        for line in agent.run_store.trace_path(state)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    event = next(
        row for row in trace if row["event"] == "workspace_checkpoint_completed"
    )
    assert event["status"] == "committed"
    assert event["edited_files"] == ["result.txt"]


def test_interrupted_turn_exposes_workspace_recovery_in_next_prompt(tmp_path):
    (tmp_path / "README.md").write_text("baseline\n", encoding="utf-8")
    model = FakeModelClient(
        [
            '<tool>{"name":"write_file","args":{"path":"partial.txt","content":"partial\\n"}}</tool>',
            "<final>Continue later.</final>",
            "<final>Resumed.</final>",
        ]
    )
    agent = RepoAgent(
        model_client=model,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
        checkpoint_policy="always",
        max_steps=1,
    )

    assert agent.ask("Start the edit") == "Continue later."
    assert agent.current_task_state.stop_reason == "step_limit_reached"
    assert agent.current_task_state.edited_files == ["partial.txt"]
    assert agent.ask("Continue") == "Resumed."

    assert "Workspace files from previous turn: partial.txt" in model.prompts[-1]
    assert "Workspace checkpoint:" in model.prompts[-1]
