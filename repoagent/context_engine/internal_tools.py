"""Private Curator tools: no filesystem/shell capabilities are exposed to the model."""
import asyncio

from .tool_base import Tool

__all__ = ["Tool", "ToolRegistry"]


class ToolRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        if tool.name in self.tools:
            raise ValueError("duplicate curator tool")
        self.tools[tool.name] = tool

    @property
    def tool_names(self):
        return list(self.tools)

    def get_definitions(self):
        return [tool.to_schema() for tool in self.tools.values()]

    async def execute(self, name, params, call_id=None):
        if name not in self.tools:
            return f"Error: Tool '{name}' not found."
        tool = self.tools[name]
        try:
            arguments = tool.cast_params(params)
            errors = tool.validate_params(arguments)
            if errors:
                return "Error: Invalid parameters for tool '" + name + "': " + "; ".join(errors)
            return await asyncio.wait_for(tool.execute(**arguments), timeout=60)
        except Exception as exc:
            return f"Error: {exc}"
