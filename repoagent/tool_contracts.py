"""Provider-neutral contracts for the Tool Gateway seam."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any


TOOL_CONTRACT_FORMAT_VERSION = 1
TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
TOOL_ORIGINS = frozenset({"model", "internal", "delegate", "mcp"})
TOOL_RESULT_STATUSES = frozenset(
    {"ok", "error", "rejected", "partial_success", "cancelled", "timeout"}
)


class ToolEffect(str, Enum):
    UNKNOWN = "unknown"
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    EXTERNAL = "external"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("tool contract object keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(
        f"tool contract values must be JSON-compatible, got {type(value).__name__}"
    )


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _validate_tool_name(name: Any) -> None:
    if not isinstance(name, str) or not TOOL_NAME_PATTERN.fullmatch(name):
        raise ValueError(f"invalid tool name: {name!r}")


def _validate_requested_tool_name(name: Any) -> None:
    if (
        not isinstance(name, str)
        or not name
        or name != name.strip()
        or len(name) > 256
        or any(ord(character) < 32 for character in name)
    ):
        raise ValueError("requested tool name must be a safe non-empty string")


def _validate_parameters_schema(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("tool parameters must be a JSON Schema object")
    schema = dict(value)
    if schema.get("type") != "object":
        raise ValueError("tool parameters schema type must be object")
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, Mapping):
        raise ValueError("tool parameters properties must be an object")
    if not isinstance(required, (list, tuple)) or any(
        not isinstance(name, str) for name in required
    ):
        raise ValueError("tool parameters required must be a string array")
    if len(set(required)) != len(required):
        raise ValueError("tool parameters required must not contain duplicates")
    if not set(required).issubset(properties):
        raise ValueError("required tool parameters must exist in properties")
    if schema.get("additionalProperties") is not False:
        raise ValueError("tool parameters must reject additional properties")
    for name, property_schema in properties.items():
        if not isinstance(name, str) or not isinstance(property_schema, Mapping):
            raise ValueError("tool parameter properties must be schema objects")
        expected = property_schema.get("type")
        if expected not in {
            "string",
            "integer",
            "number",
            "boolean",
            "array",
            "object",
        }:
            raise ValueError(f"unsupported JSON Schema type for {name}: {expected!r}")
        if "minimum" in property_schema and "maximum" in property_schema:
            if property_schema["minimum"] > property_schema["maximum"]:
                raise ValueError(f"invalid numeric range for tool parameter {name}")
        if "minLength" in property_schema and "maxLength" in property_schema:
            if property_schema["minLength"] > property_schema["maxLength"]:
                raise ValueError(f"invalid string length range for {name}")
        if "default" in property_schema:
            _validate_argument_value(
                f"default for {name}",
                property_schema["default"],
                property_schema,
            )


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, (list, tuple))
    if expected == "object":
        return isinstance(value, Mapping)
    return False


def _validate_argument_value(path: str, value: Any, schema: Mapping) -> Any:
    expected = schema.get("type")
    if expected and not _matches_json_type(value, expected):
        raise ValueError(f"{path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} must be one of {list(schema['enum'])!r}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueError(f"{path} is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"{path} is longer than maxLength")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path} is above maximum")
    if isinstance(value, (list, tuple)):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            return [
                _validate_argument_value(f"{path}[{index}]", item, item_schema)
                for index, item in enumerate(value)
            ]
        return list(value)
    if isinstance(value, Mapping):
        return _validate_argument_object(path, value, schema)
    return value


def _validate_argument_object(
    path: str, arguments: Mapping[str, Any], schema: Mapping[str, Any]
) -> dict[str, Any]:
    properties = dict(schema.get("properties") or {})
    required = tuple(schema.get("required") or ())
    unknown = sorted(set(arguments) - set(properties))
    if unknown and schema.get("additionalProperties") is False:
        raise ValueError(f"{path} contains unknown fields: {', '.join(unknown)}")
    missing = [name for name in required if name not in arguments]
    if missing:
        raise ValueError(f"{path} is missing required fields: {', '.join(missing)}")
    normalized = {}
    for name, property_schema in properties.items():
        if name in arguments:
            normalized[name] = _validate_argument_value(
                f"{path}.{name}", arguments[name], property_schema
            )
        elif "default" in property_schema:
            normalized[name] = _thaw(_freeze(property_schema["default"]))
    return normalized


def validate_tool_arguments(
    definition: "ToolDefinition", arguments: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate and default one argument object from its ToolDefinition."""

    if not isinstance(definition, ToolDefinition):
        raise TypeError("definition must be ToolDefinition")
    if not isinstance(arguments, Mapping):
        raise ValueError("tool arguments must be an object")
    frozen = _freeze(arguments)
    return _validate_argument_object(
        f"arguments for {definition.name}", frozen, definition.parameters
    )


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, Any]
    effect: ToolEffect
    concurrency_safe: bool = False
    requires_approval: bool = False
    timeout_seconds: float = 30.0
    max_output_chars: int = 4000
    requires_isolation: bool = False

    def __post_init__(self) -> None:
        _validate_tool_name(self.name)
        if (
            not isinstance(self.description, str)
            or not self.description.strip()
            or self.description != self.description.strip()
        ):
            raise ValueError("tool description must be a non-empty trimmed string")
        _validate_parameters_schema(self.parameters)
        if not isinstance(self.effect, ToolEffect):
            raise ValueError("tool effect must be ToolEffect")
        if not isinstance(self.concurrency_safe, bool):
            raise ValueError("tool concurrency_safe must be boolean")
        if not isinstance(self.requires_approval, bool):
            raise ValueError("tool requires_approval must be boolean")
        if not isinstance(self.requires_isolation, bool):
            raise ValueError("tool requires_isolation must be boolean")
        if self.concurrency_safe and self.effect is not ToolEffect.READ:
            raise ValueError("only read-effect tools may be concurrency safe")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("tool timeout_seconds must be positive")
        if (
            isinstance(self.max_output_chars, bool)
            or not isinstance(self.max_output_chars, int)
            or self.max_output_chars <= 0
        ):
            raise ValueError("tool max_output_chars must be positive")
        object.__setattr__(self, "parameters", _freeze(self.parameters))

    @property
    def definition_id(self) -> str:
        payload = json.dumps(
            self.to_dict(include_definition_id=False),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    def to_dict(self, *, include_definition_id: bool = True) -> dict[str, Any]:
        value = {
            "format_version": TOOL_CONTRACT_FORMAT_VERSION,
            "name": self.name,
            "description": self.description,
            "parameters": _thaw(self.parameters),
            "effect": self.effect.value,
            "concurrency_safe": self.concurrency_safe,
            "requires_approval": self.requires_approval,
            "timeout_seconds": self.timeout_seconds,
            "max_output_chars": self.max_output_chars,
            "requires_isolation": self.requires_isolation,
        }
        if include_definition_id:
            value["definition_id"] = self.definition_id
        return value


@dataclass(frozen=True)
class ToolRequest:
    call_id: str
    name: str
    arguments: Mapping[str, Any]
    turn_id: str = ""
    request_id: str = ""
    session_id: str = ""
    origin: str = "model"
    parent_call_id: str = ""
    capability_token: str = field(default="", repr=False)
    timeout_seconds: float | None = None
    max_output_chars: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.call_id, str)
            or not self.call_id
            or self.call_id != self.call_id.strip()
        ):
            raise ValueError("tool call_id must be a non-empty trimmed string")
        _validate_requested_tool_name(self.name)
        if not isinstance(self.arguments, Mapping):
            raise ValueError("tool arguments must be an object")
        if self.origin not in TOOL_ORIGINS:
            raise ValueError(f"invalid tool origin: {self.origin!r}")
        for name, value in (
            ("turn_id", self.turn_id),
            ("request_id", self.request_id),
            ("session_id", self.session_id),
            ("parent_call_id", self.parent_call_id),
        ):
            if not isinstance(value, str):
                raise ValueError(f"tool {name} must be a string")
            if value and value != value.strip():
                raise ValueError(f"tool {name} must be trimmed")
        if self.parent_call_id and self.parent_call_id == self.call_id:
            raise ValueError("tool call cannot be its own parent")
        if not isinstance(self.capability_token, str):
            raise ValueError("tool capability_token must be a string")
        if self.timeout_seconds is not None and (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("tool request timeout_seconds must be positive")
        if self.max_output_chars is not None and (
            isinstance(self.max_output_chars, bool)
            or not isinstance(self.max_output_chars, int)
            or self.max_output_chars <= 0
        ):
            raise ValueError("tool request max_output_chars must be positive")
        object.__setattr__(self, "arguments", _freeze(self.arguments))

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": TOOL_CONTRACT_FORMAT_VERSION,
            "call_id": self.call_id,
            "name": self.name,
            "arguments": _thaw(self.arguments),
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "origin": self.origin,
            "parent_call_id": self.parent_call_id,
            "capability_token_present": bool(self.capability_token),
            "capability_token_digest": (
                "sha256:"
                + hashlib.sha256(self.capability_token.encode("utf-8")).hexdigest()
                if self.capability_token
                else ""
            ),
            "timeout_seconds": self.timeout_seconds,
            "max_output_chars": self.max_output_chars,
        }


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    status: str
    effect: ToolEffect
    content: str
    duration_ms: float = 0
    error_code: str = ""
    affected_paths: tuple[str, ...] = ()
    workspace_changed: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.call_id, str)
            or not self.call_id
            or self.call_id != self.call_id.strip()
        ):
            raise ValueError("tool result call_id must be a non-empty trimmed string")
        _validate_requested_tool_name(self.name)
        if self.status not in TOOL_RESULT_STATUSES:
            raise ValueError(f"invalid tool result status: {self.status!r}")
        if not isinstance(self.effect, ToolEffect):
            raise ValueError("tool result effect must be ToolEffect")
        if not isinstance(self.content, str):
            raise ValueError("tool result content must be a string")
        if (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, (int, float))
            or not math.isfinite(float(self.duration_ms))
            or self.duration_ms < 0
        ):
            raise ValueError("tool result duration_ms must be non-negative")
        if self.status == "ok" and self.error_code:
            raise ValueError("successful tool result must not have an error code")
        if self.status != "ok" and not self.error_code:
            raise ValueError("non-success tool result must have an error code")
        if not isinstance(self.workspace_changed, bool):
            raise ValueError("tool result workspace_changed must be boolean")
        paths = tuple(self.affected_paths)
        if any(not isinstance(path, str) or not path for path in paths):
            raise ValueError("tool result affected paths must be non-empty strings")
        if len(set(paths)) != len(paths):
            raise ValueError("tool result affected paths must not contain duplicates")
        if paths and not self.workspace_changed:
            raise ValueError("affected paths require workspace_changed")
        if self.workspace_changed and self.effect not in {
            ToolEffect.WRITE,
            ToolEffect.EXECUTE,
        }:
            raise ValueError("tool effect cannot change the workspace")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("tool result metadata must be an object")
        object.__setattr__(self, "affected_paths", paths)
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    @property
    def succeeded(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": TOOL_CONTRACT_FORMAT_VERSION,
            "call_id": self.call_id,
            "name": self.name,
            "status": self.status,
            "effect": self.effect.value,
            "content": self.content,
            "duration_ms": self.duration_ms,
            "error_code": self.error_code,
            "affected_paths": list(self.affected_paths),
            "workspace_changed": self.workspace_changed,
            "metadata": _thaw(self.metadata),
        }


__all__ = [
    "TOOL_CONTRACT_FORMAT_VERSION",
    "ToolDefinition",
    "ToolEffect",
    "ToolRequest",
    "ToolResult",
    "validate_tool_arguments",
]
