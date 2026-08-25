"""Compatibility adapter for callers using the legacy tool result shape."""

from dataclasses import dataclass

from .tool_gateway import compatibility_metadata


@dataclass(frozen=True)
class ToolExecutionResult:
    content: str
    metadata: dict


class ToolExecutor:
    def __init__(self, agent):
        self.agent = agent

    def execute(self, name, args):
        result = self.agent.execute_tool(name, args)
        return ToolExecutionResult(
            content=result.content,
            metadata=compatibility_metadata(result),
        )
