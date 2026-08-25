from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext
from repoagent.checkpoint import (
    CHECKPOINT_FULL_VALID_STATUS,
    CHECKPOINT_NONE_STATUS,
    CHECKPOINT_SCHEMA_MISMATCH_STATUS,
    CHECKPOINT_SCHEMA_VERSION,
    CHECKPOINT_WORKSPACE_MISMATCH_STATUS,
    current_runtime_identity,
    evaluate_resume_state,
)


def build_agent(tmp_path, outputs=None, **kwargs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    store = SessionStore(tmp_path / ".repoagent" / "sessions")
    return RepoAgent(
        model_client=FakeModelClient(outputs or []),
        workspace=workspace,
        session_store=store,
        approval_policy=kwargs.pop("approval_policy", "auto"),
        **kwargs,
    )


def test_current_runtime_identity_captures_execution_contract(tmp_path):
    agent = build_agent(
        tmp_path,
        max_steps=9,
        max_parallel_tools=3,
        max_new_tokens=1024,
        read_only=True,
    )

    identity = current_runtime_identity(agent)

    assert identity["session_id"] == agent.session["id"]
    assert identity["cwd"] == str(tmp_path)
    assert identity["read_only"] is True
    assert identity["max_steps"] == 9
    assert identity["max_parallel_tools"] == 3
    assert identity["mutation_conflict_policy"] == "serial"
    assert identity["sandbox_identity"] == "direct_host"
    assert identity["require_isolation"] is False
    assert identity["max_new_tokens"] == 1024
    assert identity["context_token_budget"] == 3000
    assert identity["configured_context_token_budget"] == 3000
    assert identity["context_window_tokens"] == 32768
    assert identity["context_window_source"] == "runtime-default"
    assert identity["reserved_output_tokens"] == 1024
    assert identity["segment_token_budgets"] == {
        "prefix": 900,
        "memory": 400,
        "relevant_memory": 300,
        "skills": 300,
        "history": 1300,
    }
    assert identity["token_counter"]["source"] == "estimated"
    assert identity["history_compaction_strategy"] == "deterministic-history-v1"
    assert identity["memory_backend"] == "LayeredMemory"
    assert identity["skill_catalog"] == []
    assert identity["workspace_fingerprint"] == agent.workspace.fingerprint()
    assert identity["tool_signature"] == agent.tool_signature()
    assert identity["capability_scope"]["valid"] is True
    assert identity["capability_scope"]["effects"] == ["read"]
    assert set(identity["capability_scope"]["tools"]) == {
        "list_files",
        "read_file",
        "search",
        "delegate",
        "git_status",
        "git_diff",
        "git_worktree_list",
    }


def test_evaluate_resume_state_distinguishes_no_checkpoint_full_valid_and_schema_mismatch(
    tmp_path,
):
    agent = build_agent(tmp_path)

    assert evaluate_resume_state(agent)["status"] == CHECKPOINT_NONE_STATUS

    identity = current_runtime_identity(agent)
    agent.session["checkpoints"] = {
        "current_id": "ckpt_valid",
        "items": {
            "ckpt_valid": {
                "checkpoint_id": "ckpt_valid",
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "key_files": [],
                "runtime_identity": identity,
            }
        },
    }
    assert evaluate_resume_state(agent)["status"] == CHECKPOINT_FULL_VALID_STATUS

    agent.context_manager.total_token_budget -= 1
    mismatch = evaluate_resume_state(agent)
    assert mismatch["status"] == CHECKPOINT_WORKSPACE_MISMATCH_STATUS
    assert mismatch["runtime_identity_mismatch_fields"] == [
        "context_token_budget"
    ]
    agent.context_manager.total_token_budget += 1

    agent.session["checkpoints"]["items"]["ckpt_valid"]["schema_version"] = "old"
    assert evaluate_resume_state(agent)["status"] == CHECKPOINT_SCHEMA_MISMATCH_STATUS
