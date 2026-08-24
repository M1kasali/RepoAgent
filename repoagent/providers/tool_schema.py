"""Project the current tool registry into provider-neutral JSON schemas."""

from collections.abc import Mapping
from typing import Any

from ..tool_contracts import ToolDefinition
from .base import ModelTool


def model_tools_from_registry(registry: Mapping[str, Mapping[str, Any]]):
    tools = []
    for name, definition in registry.items():
        tool_definition = definition.get("definition")
        if not isinstance(tool_definition, ToolDefinition):
            raise ValueError(f"tool registry entry {name!r} has no ToolDefinition")
        if tool_definition.name != name:
            raise ValueError(
                f"tool registry key {name!r} does not match definition name"
            )
        tools.append(
            ModelTool(
                name=tool_definition.name,
                description=tool_definition.description,
                parameters=tool_definition.to_dict()["parameters"],
            )
        )
    return tuple(tools)


__all__ = ["model_tools_from_registry"]
