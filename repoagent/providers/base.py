"""Provider-neutral model request, result, usage, event, and error contracts."""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable


class UsageSource(str, Enum):
    ACTUAL = "actual"
    ESTIMATED = "estimated"
    MISSING = "missing"


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    source: UsageSource = UsageSource.MISSING

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any] | None) -> "ModelUsage":
        values = dict(metadata or {})
        input_tokens = _non_negative_int(
            values.get("input_tokens", values.get("prompt_tokens", 0))
        )
        output_tokens = _non_negative_int(
            values.get("output_tokens", values.get("completion_tokens", 0))
        )
        total_value = values.get("total_tokens")
        total_tokens = (
            _non_negative_int(total_value)
            if total_value is not None
            else input_tokens + output_tokens
        )
        has_usage = any(
            key in values
            for key in (
                "input_tokens",
                "prompt_tokens",
                "output_tokens",
                "completion_tokens",
                "total_tokens",
            )
        )
        source_value = values.get("usage_source")
        source = UsageSource(source_value) if source_value else (
            UsageSource.ACTUAL if has_usage else UsageSource.MISSING
        )
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cache_read_tokens=_non_negative_int(
                values.get("cache_read_tokens", values.get("cached_tokens", 0))
            ),
            cache_write_tokens=_non_negative_int(
                values.get("cache_write_tokens", 0)
            ),
            source=source,
        )

    def to_metadata(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "usage_source": self.source.value,
        }


def _non_negative_int(value: Any) -> int:
    try:
        result = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid token usage value: {value!r}") from exc
    if result < 0:
        raise ValueError(f"token usage must not be negative: {result}")
    return result


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("tool call id and name must not be empty")
        object.__setattr__(self, "arguments", MappingProxyType(dict(self.arguments)))


@dataclass(frozen=True)
class ModelTool:
    name: str
    description: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("model tool name must not be empty")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))


@dataclass(frozen=True)
class ModelRequest:
    prompt: str
    max_output_tokens: int
    prompt_cache_key: str | None = None
    prompt_cache_retention: str | None = None
    timeout_seconds: float | None = None
    turn_id: str = ""
    session_id: str = ""
    request_id: str = ""
    attempt: int = 1
    tools: tuple[ModelTool, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str) or not self.prompt:
            raise ValueError("model prompt must not be empty")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        object.__setattr__(self, "tools", tuple(self.tools))


@dataclass(frozen=True)
class ModelResult:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: str = "stop"
    usage: ModelUsage = field(default_factory=ModelUsage)
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.latency_ms < 0:
            raise ValueError("latency_ms must not be negative")
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def completion_metadata(self) -> dict[str, Any]:
        return {
            **dict(self.metadata),
            **self.usage.to_metadata(),
            "provider": self.provider,
            "model": self.model,
            "finish_reason": self.finish_reason,
            "latency_ms": self.latency_ms,
        }


@dataclass(frozen=True)
class ModelEvent:
    kind: str
    text: str = ""
    tool_call: ToolCall | None = None
    result: ModelResult | None = None


class ProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        category: str,
        provider: str = "",
        retryable: bool = False,
        should_fallback: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.provider = provider
        self.retryable = retryable
        self.should_fallback = should_fallback
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "provider": self.provider,
            "retryable": self.retryable,
            "should_fallback": self.should_fallback,
            "status_code": self.status_code,
        }


class ProviderProtocolError(ProviderError):
    def __init__(self, message: str, *, provider: str = "") -> None:
        super().__init__(message, category="protocol", provider=provider)


class ProviderConnectionError(ProviderError):
    def __init__(self, message: str, *, provider: str = "") -> None:
        super().__init__(
            message,
            category="connection",
            provider=provider,
            retryable=True,
            should_fallback=True,
        )


class ProviderTimeoutError(ProviderError):
    def __init__(self, message: str, *, provider: str = "") -> None:
        super().__init__(
            message,
            category="timeout",
            provider=provider,
            retryable=True,
            should_fallback=True,
        )


@runtime_checkable
class ModelProvider(Protocol):
    def generate(self, request: ModelRequest) -> ModelResult: ...

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]: ...


def generate_model(client: Any, request: ModelRequest) -> ModelResult:
    """Invoke a typed provider or adapt one legacy complete-only client."""
    generate = getattr(client, "generate", None)
    if callable(generate):
        result = generate(request)
    else:
        complete = getattr(client, "complete", None)
        if not callable(complete):
            raise ProviderProtocolError(
                "model client implements neither generate() nor complete()",
                provider=type(client).__name__,
            )
        text = complete(
            request.prompt,
            request.max_output_tokens,
            prompt_cache_key=request.prompt_cache_key,
            prompt_cache_retention=request.prompt_cache_retention,
        )
        metadata = dict(getattr(client, "last_completion_metadata", {}) or {})
        result = ModelResult(
            text=str(text),
            usage=ModelUsage.from_metadata(metadata),
            provider=type(client).__name__,
            model=str(getattr(client, "model", "")),
            metadata=metadata,
        )
    if not isinstance(result, ModelResult):
        raise ProviderProtocolError(
            f"generate() returned {type(result).__name__}, expected ModelResult",
            provider=type(client).__name__,
        )
    return result


def stream_model(
    client: Any,
    request: ModelRequest,
    on_event: Callable[[ModelEvent], None] | None = None,
) -> ModelResult:
    """Consume normalized streaming events and require one terminal result."""
    stream = getattr(client, "stream", None)
    if not callable(stream):
        return generate_model(client, request)

    terminal: ModelResult | None = None
    for event in stream(request):
        if not isinstance(event, ModelEvent):
            raise ProviderProtocolError(
                f"stream() yielded {type(event).__name__}, expected ModelEvent",
                provider=type(client).__name__,
            )
        if terminal is not None:
            raise ProviderProtocolError(
                "stream() yielded an event after completion",
                provider=type(client).__name__,
            )
        if on_event is not None:
            on_event(event)
        if event.kind == "completed":
            if event.result is None:
                raise ProviderProtocolError(
                    "completed model event is missing its result",
                    provider=type(client).__name__,
                )
            terminal = event.result
    if terminal is None:
        raise ProviderProtocolError(
            "stream() ended without a completed model event",
            provider=type(client).__name__,
        )
    return terminal


__all__ = [
    "ModelEvent",
    "ModelProvider",
    "ModelRequest",
    "ModelResult",
    "ModelTool",
    "ModelUsage",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderProtocolError",
    "ProviderTimeoutError",
    "ToolCall",
    "UsageSource",
    "generate_model",
    "stream_model",
]
