"""Project the current tool registry into provider-neutral JSON schemas."""

import ast
from collections.abc import Mapping
from typing import Any

from .base import ModelTool


_JSON_TYPES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
}


def model_tools_from_registry(registry: Mapping[str, Mapping[str, Any]]):
    tools = []
    for name, definition in registry.items():
        properties = {}
        required = []
        for argument, declaration in dict(definition.get("schema") or {}).items():
            declaration = str(declaration)
            type_name, separator, default_text = declaration.partition("=")
            if type_name not in _JSON_TYPES:
                raise ValueError(
                    f"unsupported tool schema type {type_name!r} for {name}.{argument}"
                )
            property_schema = {"type": _JSON_TYPES[type_name]}
            if separator:
                try:
                    property_schema["default"] = ast.literal_eval(default_text)
                except (SyntaxError, ValueError) as exc:
                    raise ValueError(
                        f"invalid tool schema default for {name}.{argument}"
                    ) from exc
            else:
                required.append(str(argument))
            properties[str(argument)] = property_schema
        parameters = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }
        tools.append(
            ModelTool(
                name=str(name),
                description=str(definition.get("description") or ""),
                parameters=parameters,
            )
        )
    return tuple(tools)


__all__ = ["model_tools_from_registry"]
