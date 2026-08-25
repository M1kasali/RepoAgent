from unittest.mock import patch

import pytest

from repoagent import (
    CapabilityAuthority,
    FakeModelClient,
    RepoAgent,
    SessionStore,
    ToolEffect,
    WorkspaceContext,
    capability_token_digest,
)


def authority(clock):
    return CapabilityAuthority(
        secret=b"0123456789abcdef0123456789abcdef",
        issuer_id="test-authority",
        clock=lambda: clock[0],
    )


def issue_root(value, **overrides):
    arguments = {
        "subject_id": "agent-1",
        "session_id": "session-1",
        "effects": (ToolEffect.READ, ToolEffect.WRITE),
        "tools": ("read_file", "write_file"),
        "ttl_seconds": 60,
    }
    arguments.update(overrides)
    return value.issue(**arguments)


def test_capability_roundtrip_and_digest_do_not_expose_wire_token():
    clock = [1000]
    value = authority(clock)
    token = issue_root(value)

    claims = value.verify(token)

    assert claims is not None
    assert claims.subject_id == "agent-1"
    assert claims.effects == (ToolEffect.READ, ToolEffect.WRITE)
    assert claims.tools == ("read_file", "write_file")
    assert claims.expires_at == 1060
    assert capability_token_digest(token).startswith("sha256:")
    assert token not in capability_token_digest(token)


def test_capability_verification_fails_closed_for_tampering_expiry_and_malformed():
    clock = [1000]
    value = authority(clock)
    token = issue_root(value)
    body, signature = token.split(".", 1)
    replacement = "A" if signature[-1] != "A" else "B"
    tampered = f"{body}.{signature[:-1]}{replacement}"

    assert value.verify(tampered) is None
    assert value.verify("not-a-token") is None
    assert value.verify("nonascii-\u975e.signature") is None
    clock[0] = 1060
    assert value.verify(token) is None


def test_child_capability_can_only_attenuate_parent_scope():
    clock = [1000]
    value = authority(clock)
    parent = issue_root(value)

    child = value.issue(
        subject_id="agent-child",
        session_id="session-child",
        effects=(ToolEffect.READ,),
        tools=("read_file",),
        parent_token=parent,
        ttl_seconds=120,
    )
    child_claims = value.verify(child)
    parent_claims = value.verify(parent)

    assert child_claims is not None and parent_claims is not None
    assert child_claims.parent_token_id == parent_claims.token_id
    assert child_claims.expires_at == parent_claims.expires_at
    with pytest.raises(ValueError, match="expand effects"):
        value.issue(
            subject_id="agent-child",
            session_id="session-child",
            effects=(ToolEffect.EXECUTE,),
            tools=("read_file",),
            parent_token=parent,
        )
    with pytest.raises(ValueError, match="expand tools"):
        value.issue(
            subject_id="agent-child",
            session_id="session-child",
            effects=(ToolEffect.READ,),
            tools=("run_shell",),
            parent_token=parent,
        )


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"subject_id": "agent-2"}, "subject_mismatch"),
        ({"session_id": "session-2"}, "session_mismatch"),
        ({"tool_name": "run_shell"}, "tool_not_granted"),
        ({"effect": ToolEffect.EXECUTE}, "effect_not_granted"),
    ],
)
def test_capability_authorization_binds_every_scope_axis(overrides, reason):
    clock = [1000]
    value = authority(clock)
    token = issue_root(value)
    arguments = {
        "subject_id": "agent-1",
        "session_id": "session-1",
        "tool_name": "read_file",
        "effect": ToolEffect.READ,
    }
    arguments.update(overrides)

    decision = value.authorize(token, **arguments)

    assert decision.allowed is False
    assert decision.reason == reason
    assert decision.token_digest == capability_token_digest(token)


def test_delegate_receives_an_attenuated_read_only_token(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    agent = RepoAgent(
        model_client=FakeModelClient([]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )
    children = []

    def capture(child, task):
        children.append(child)
        return "inspected"

    with patch.object(RepoAgent, "ask", autospec=True, side_effect=capture):
        result = agent.spawn_delegate({"task": "inspect", "max_steps": 2})

    child = children[0]
    parent_claims = agent.capability_authority.verify(agent.capability_token)
    child_claims = child.capability_authority.verify(child.capability_token)
    assert result == "delegate_result:\ninspected"
    assert parent_claims is not None and child_claims is not None
    assert child.capability_authority is agent.capability_authority
    assert child_claims.parent_token_id == parent_claims.token_id
    assert child_claims.effects == (ToolEffect.READ,)
    assert set(child_claims.tools) == {
        "list_files",
        "read_file",
        "search",
        "git_status",
        "git_diff",
        "git_worktree_list",
    }
    assert child.read_only is True
    assert child.context_manager.token_counter is agent.context_manager.token_counter
    assert (
        child.context_manager.total_token_budget
        == agent.context_manager.total_token_budget
    )
    assert (
        child.context_manager.segment_token_budgets
        == agent.context_manager.segment_token_budgets
    )
