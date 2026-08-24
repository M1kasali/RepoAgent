"""Model provider adapters."""

from .base import (
    CancellationToken,
    InputTokenSemantics,
    ModelEvent,
    ModelProvider,
    ModelRequest,
    ModelResult,
    ModelTool,
    ModelUsage,
    ModelUsageAggregate,
    ProviderCancelledError,
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
from .fallback import FallbackModelClient, ProviderAttempt, ProviderFallbackExhaustedError
from .profiles import BUILTIN_MODEL_PROFILES, ModelProfile, get_model_profile

__all__ = [
    "AnthropicCompatibleModelClient",
    "BUILTIN_MODEL_PROFILES",
    "CancellationToken",
    "FakeModelClient",
    "FallbackModelClient",
    "InputTokenSemantics",
    "ModelEvent",
    "ModelProfile",
    "ModelProvider",
    "ModelRequest",
    "ModelResult",
    "ModelTool",
    "ModelUsage",
    "ModelUsageAggregate",
    "OllamaModelClient",
    "OpenAICompatibleModelClient",
    "ProviderCancelledError",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderAttempt",
    "ProviderFallbackExhaustedError",
    "ProviderProtocolError",
    "ProviderTimeoutError",
    "ToolCall",
    "UsageSource",
    "generate_model",
    "get_model_profile",
    "model_tools_from_registry",
    "stream_model",
]
