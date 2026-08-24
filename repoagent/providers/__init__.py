"""Model provider adapters."""

from .base import (
    ModelEvent,
    ModelProvider,
    ModelRequest,
    ModelResult,
    ModelTool,
    ModelUsage,
    ProviderConnectionError,
    ProviderError,
    ProviderProtocolError,
    ProviderTimeoutError,
    ToolCall,
    UsageSource,
    generate_model,
    stream_model,
)
from .tool_schema import model_tools_from_registry
from .clients import AnthropicCompatibleModelClient, FakeModelClient, OllamaModelClient, OpenAICompatibleModelClient

__all__ = [
    "AnthropicCompatibleModelClient",
    "FakeModelClient",
    "ModelEvent",
    "ModelProvider",
    "ModelRequest",
    "ModelResult",
    "ModelTool",
    "ModelUsage",
    "OllamaModelClient",
    "OpenAICompatibleModelClient",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderProtocolError",
    "ProviderTimeoutError",
    "ToolCall",
    "UsageSource",
    "generate_model",
    "model_tools_from_registry",
    "stream_model",
]
