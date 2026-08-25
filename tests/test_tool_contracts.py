from types import MappingProxyType

import pytest

from repoagent.tool_contracts import (
    TOOL_CONTRACT_FORMAT_VERSION,
    ToolDefinition,
    ToolEffect,
    ToolRequest,
    ToolResult,
    validate_tool_arguments,
)


def _schema():
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
        },
        "required": ["path"],
        "additionalProperties": False,
    }


def test_tool_definition_is_deeply_immutable_and_stably_identified():
    schema = _schema()
    definition = ToolDefinition(
        name="read_file",
        description="Read a file from the workspace.",
        parameters=schema,
        effect=ToolEffect.READ,
        concurrency_safe=True,
    )
    schema["properties"]["path"]["type"] = "integer"

    assert isinstance(definition.parameters, MappingProxyType)
    assert definition.parameters["properties"]["path"]["type"] == "string"
    assert (
        definition.definition_id
        == ToolDefinition(
            name="read_file",
            description="Read a file from the workspace.",
            parameters=_schema(),
            effect=ToolEffect.READ,
            concurrency_safe=True,
        ).definition_id
    )
    assert definition.to_dict()["format_version"] == TOOL_CONTRACT_FORMAT_VERSION
    assert definition.to_dict()["timeout_seconds"] == 30.0
    assert definition.to_dict()["max_output_chars"] == 4000
    with pytest.raises(TypeError):
        definition.parameters["new"] = {}


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"name": "Read File"}, "tool name"),
        ({"description": " "}, "description"),
        ({"parameters": {}}, "schema type"),
        (
            {
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "unsupported"}},
                    "required": [],
                    "additionalProperties": False,
                }
            },
            "unsupported JSON Schema type",
        ),
        (
            {
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": ["path"],
                    "additionalProperties": False,
                }
            },
            "must exist",
        ),
        ({"effect": "read"}, "ToolEffect"),
        ({"effect": ToolEffect.WRITE, "concurrency_safe": True}, "read-effect"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"max_output_chars": 0}, "max_output_chars"),
    ],
)
def test_tool_definition_rejects_invalid_contracts(overrides, message):
    values = {
        "name": "read_file",
        "description": "Read a file.",
        "parameters": _schema(),
        "effect": ToolEffect.READ,
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        ToolDefinition(**values)


def test_tool_request_copies_nested_arguments_and_preserves_correlation():
    arguments = {"path": "README.md", "options": {"lines": [1, 2]}}
    request = ToolRequest(
        call_id="call-1",
        name="read_file",
        arguments=arguments,
        turn_id="turn-1",
        request_id="request-1",
        session_id="session-1",
        origin="model",
        capability_token="opaque.signed-token",
    )
    arguments["options"]["lines"].append(3)

    assert request.arguments["options"]["lines"] == (1, 2)
    assert request.to_dict()["arguments"]["options"]["lines"] == [1, 2]
    assert request.to_dict()["turn_id"] == "turn-1"
    assert request.to_dict()["timeout_seconds"] is None
    assert request.to_dict()["max_output_chars"] is None
    assert request.to_dict()["capability_token_present"] is True
    assert request.to_dict()["capability_token_digest"].startswith("sha256:")
    assert "opaque.signed-token" not in str(request.to_dict())


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"call_id": ""}, "call_id"),
        ({"name": ""}, "requested tool name"),
        ({"arguments": []}, "arguments"),
        ({"arguments": {1: "value"}}, "keys must be strings"),
        ({"arguments": {"value": object()}}, "JSON-compatible"),
        ({"origin": "unknown"}, "origin"),
        ({"capability_token": object()}, "capability_token"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"max_output_chars": 0}, "max_output_chars"),
        ({"parent_call_id": "call-1"}, "own parent"),
    ],
)
def test_tool_request_rejects_invalid_envelopes(overrides, message):
    values = {"call_id": "call-1", "name": "read_file", "arguments": {}}
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        ToolRequest(**values)


def test_tool_result_exposes_structured_success_and_workspace_effect():
    result = ToolResult(
        call_id="call-1",
        name="write_file",
        status="ok",
        effect=ToolEffect.WRITE,
        content="wrote README.md",
        duration_ms=4.5,
        affected_paths=("README.md",),
        workspace_changed=True,
        metadata={"diff": {"added": 1}},
    )

    assert result.succeeded is True
    assert result.to_dict()["effect"] == "write"
    assert result.to_dict()["metadata"] == {"diff": {"added": 1}}


def test_tool_definition_drives_argument_validation_and_defaults():
    definition = ToolDefinition(
        name="read_file",
        description="Read a file.",
        parameters=_schema(),
        effect=ToolEffect.READ,
    )

    assert validate_tool_arguments(definition, {"path": "README.md"}) == {
        "path": "README.md",
        "limit": 20,
    }
    with pytest.raises(ValueError, match="must be integer"):
        validate_tool_arguments(definition, {"path": "README.md", "limit": "20"})
    with pytest.raises(ValueError, match="unknown fields"):
        validate_tool_arguments(definition, {"path": "README.md", "extra": True})


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"status": "unknown"}, "status"),
        ({"duration_ms": -1}, "duration"),
        ({"status": "ok", "error_code": "failed"}, "must not"),
        ({"status": "error", "error_code": ""}, "must have"),
        ({"affected_paths": ("README.md",)}, "workspace_changed"),
        (
            {"effect": ToolEffect.READ, "workspace_changed": True},
            "cannot change",
        ),
    ],
)
def test_tool_result_rejects_incoherent_evidence(overrides, message):
    values = {
        "call_id": "call-1",
        "name": "read_file",
        "status": "error",
        "effect": ToolEffect.READ,
        "content": "failed",
        "error_code": "tool_failed",
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=message):
        ToolResult(**values)
