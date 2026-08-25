import pytest

from repoagent import EffectApprovalPolicy, ToolDefinition, ToolEffect, ToolRequest


def definition(effect, *, requires_approval=False):
    return ToolDefinition(
        name="sample_tool",
        description="Exercise approval policy.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        effect=effect,
        requires_approval=requires_approval,
    )


def request():
    return ToolRequest(
        call_id="call-1",
        name="sample_tool",
        arguments={},
        session_id="session-1",
    )


def test_safe_read_does_not_require_operator_approval():
    decision = EffectApprovalPolicy("never").decide(
        definition(ToolEffect.READ), request(), {}
    )

    assert decision.allowed is True
    assert decision.required is False
    assert decision.reason == "safe_read"


def test_side_effect_requires_approval_even_if_definition_flag_is_false():
    denied = EffectApprovalPolicy("never").decide(
        definition(ToolEffect.WRITE), request(), {}
    )
    allowed = EffectApprovalPolicy("auto").decide(
        definition(ToolEffect.WRITE), request(), {}
    )

    assert denied.allowed is False
    assert denied.required is True
    assert denied.reason == "policy_never"
    assert allowed.allowed is True
    assert allowed.reason == "policy_auto"


def test_read_only_is_an_immutable_effect_boundary():
    decision = EffectApprovalPolicy("auto", read_only=True).decide(
        definition(ToolEffect.EXECUTE), request(), {}
    )

    assert decision.allowed is False
    assert decision.reason == "read_only"


def test_ask_mode_records_operator_decision():
    calls = []
    policy = EffectApprovalPolicy(
        "ask",
        prompt=lambda tool, invocation, arguments: (
            calls.append((tool.name, invocation.call_id, dict(arguments))) or True
        ),
    )

    decision = policy.decide(definition(ToolEffect.WRITE), request(), {})

    assert decision.allowed is True
    assert decision.reason == "operator_approved"
    assert calls == [("sample_tool", "call-1", {})]


def test_invalid_approval_mode_is_rejected_at_assembly():
    with pytest.raises(ValueError, match="invalid approval mode"):
        EffectApprovalPolicy("sometimes")
