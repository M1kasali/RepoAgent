"""Provider-neutral model request, result, usage, event, and error contracts."""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
import threading
from typing import Any, Protocol, runtime_checkable


class UsageSource(str, Enum):
    ACTUAL = "actual"
    ESTIMATED = "estimated"
    MISSING = "missing"
    MIXED = "mixed"


class InputTokenSemantics(str, Enum):
    FRESH = "fresh"
    TOTAL = "total"
    AMBIGUOUS = "ambiguous"


class CancellationToken:
    """Thread-safe cooperative cancellation with close-resource callbacks."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._next_callback_id = 0

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> bool:
        with self._lock:
            if self._cancelled.is_set():
                return False
            self._cancelled.set()
            callbacks = tuple(self._callbacks.values())
            self._callbacks.clear()
        for callback in callbacks:
            try:
                callback()
            except Exception:
                continue
        return True

    def add_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        with self._lock:
            if self._cancelled.is_set():
                invoke_now = True
                callback_id = -1
            else:
                invoke_now = False
                callback_id = self._next_callback_id
                self._next_callback_id += 1
                self._callbacks[callback_id] = callback
        if invoke_now:
            try:
                callback()
            except Exception:
                pass

        def remove() -> None:
            with self._lock:
                self._callbacks.pop(callback_id, None)

        return remove

    def raise_if_cancelled(self, *, provider: str = "") -> None:
        if self.cancelled:
            raise ProviderCancelledError(
                "model request was cancelled", provider=provider
            )


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    source: UsageSource = UsageSource.MISSING
    input_token_semantics: InputTokenSemantics = InputTokenSemantics.AMBIGUOUS

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
        source = (
            UsageSource(source_value)
            if source_value
            else (UsageSource.ACTUAL if has_usage else UsageSource.MISSING)
        )
        semantics_value = values.get("input_token_semantics")
        semantics = (
            InputTokenSemantics(semantics_value)
            if semantics_value
            else InputTokenSemantics.AMBIGUOUS
        )
        return cls(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cache_read_tokens=_non_negative_int(
                values.get("cache_read_tokens", values.get("cached_tokens", 0))
            ),
            cache_write_tokens=_non_negative_int(values.get("cache_write_tokens", 0)),
            source=source,
            input_token_semantics=semantics,
        )

    def to_metadata(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "usage_source": self.source.value,
            "input_token_semantics": self.input_token_semantics.value,
        }


@dataclass(frozen=True)
class ModelUsageAggregate:
    """Token totals plus explicit source completeness across model calls."""

    usage: ModelUsage = field(default_factory=ModelUsage)
    model_call_count: int = 0
    source_counts: Mapping[UsageSource, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.model_call_count < 0:
            raise ValueError("model_call_count must not be negative")
        counts = {
            source: int(dict(self.source_counts).get(source, 0))
            for source in UsageSource
        }
        if any(value < 0 for value in counts.values()):
            raise ValueError("usage source counts must not be negative")
        if sum(counts.values()) != self.model_call_count:
            raise ValueError("usage source counts must equal model_call_count")
        object.__setattr__(self, "source_counts", MappingProxyType(counts))

    @classmethod
    def from_usages(cls, usages) -> "ModelUsageAggregate":
        rows = tuple(usages)
        counts = {source: 0 for source in UsageSource}
        for row in rows:
            if not isinstance(row, ModelUsage):
                raise TypeError("usage aggregate accepts only ModelUsage rows")
            counts[row.source] += 1
        present_sources = {source for source, count in counts.items() if count}
        if not rows:
            source = UsageSource.MISSING
        elif len(present_sources) == 1 and UsageSource.MIXED not in present_sources:
            source = next(iter(present_sources))
        else:
            source = UsageSource.MIXED
        usage = ModelUsage(
            input_tokens=sum(row.input_tokens for row in rows),
            output_tokens=sum(row.output_tokens for row in rows),
            total_tokens=sum(row.total_tokens for row in rows),
            cache_read_tokens=sum(row.cache_read_tokens for row in rows),
            cache_write_tokens=sum(row.cache_write_tokens for row in rows),
            source=source,
            input_token_semantics=(
                next(iter({row.input_token_semantics for row in rows}))
                if rows and len({row.input_token_semantics for row in rows}) == 1
                else InputTokenSemantics.AMBIGUOUS
            ),
        )
        return cls(
            usage=usage,
            model_call_count=len(rows),
            source_counts=counts,
        )

    @property
    def complete(self) -> bool:
        return (
            self.model_call_count > 0
            and self.source_counts[UsageSource.MISSING] == 0
            and self.source_counts[UsageSource.MIXED] == 0
            and self.usage.source is not UsageSource.MIXED
        )

    def to_metadata(self) -> dict[str, Any]:
        return {
            **self.usage.to_metadata(),
            "model_call_count": self.model_call_count,
            "usage_source_counts": {
                source.value: self.source_counts[source] for source in UsageSource
            },
            "usage_complete": self.complete,
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
class ModelMessage:
    role: str
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str = ""
    name: str = ""
    reasoning_content: str = ""
    thinking_blocks: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if self.role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"unsupported model message role: {self.role!r}")
        object.__setattr__(self, "content", str(self.content))
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        object.__setattr__(self, "reasoning_content", str(self.reasoning_content))
        object.__setattr__(
            self,
            "thinking_blocks",
            tuple(MappingProxyType(dict(block)) for block in self.thinking_blocks),
        )
        if (
            self.role == "assistant"
            and not self.content
            and not self.tool_calls
            and not self.reasoning_content
            and not self.thinking_blocks
        ):
            raise ValueError(
                "assistant model message must contain text, reasoning, or tool calls"
            )
        if self.role != "assistant" and self.tool_calls:
            raise ValueError("only assistant model messages may contain tool calls")
        if self.role != "assistant" and (
            self.reasoning_content or self.thinking_blocks
        ):
            raise ValueError("only assistant model messages may contain reasoning")
        if self.role == "tool":
            if not self.tool_call_id or not self.name:
                raise ValueError("tool model message requires call id and name")
        elif self.tool_call_id or self.name:
            raise ValueError("only tool model messages may identify a tool call")


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
    messages: tuple[ModelMessage, ...] = ()
    cancellation_token: CancellationToken | None = None
    call_kind: str = "agent"

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, str) or not self.prompt:
            raise ValueError("model prompt must not be empty")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        if self.call_kind not in {"agent", "compaction"}:
            raise ValueError("call_kind must be agent or compaction")
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(self, "messages", tuple(self.messages))
        if any(not isinstance(message, ModelMessage) for message in self.messages):
            raise TypeError("model request messages must contain ModelMessage values")


@dataclass(frozen=True)
class ModelResult:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    reasoning_content: str = ""
    thinking_blocks: tuple[Mapping[str, Any], ...] = ()
    finish_reason: str = "stop"
    usage: ModelUsage = field(default_factory=ModelUsage)
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.latency_ms < 0:
            raise ValueError("latency_ms must not be negative")
        object.__setattr__(self, "reasoning_content", str(self.reasoning_content))
        object.__setattr__(
            self,
            "thinking_blocks",
            tuple(MappingProxyType(dict(block)) for block in self.thinking_blocks),
        )
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
        should_compress: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.provider = provider
        self.retryable = retryable
        self.should_fallback = should_fallback
        self.should_compress = should_compress
        self.status_code = status_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "provider": self.provider,
            "retryable": self.retryable,
            "should_fallback": self.should_fallback,
            "should_compress": self.should_compress,
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


class ProviderCancelledError(ProviderError):
    def __init__(self, message: str, *, provider: str = "") -> None:
        super().__init__(
            message,
            category="cancelled",
            provider=provider,
            retryable=False,
            should_fallback=False,
        )


@runtime_checkable
class ModelProvider(Protocol):
    def generate(self, request: ModelRequest) -> ModelResult: ...

    def stream(self, request: ModelRequest) -> Iterator[ModelEvent]: ...


def generate_model(client: Any, request: ModelRequest) -> ModelResult:
    """Invoke a typed provider or adapt one legacy complete-only client."""
    if request.cancellation_token is not None:
        request.cancellation_token.raise_if_cancelled(provider=type(client).__name__)
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
    if request.cancellation_token is not None:
        request.cancellation_token.raise_if_cancelled(provider=type(client).__name__)
    return result


def stream_model(
    client: Any,
    request: ModelRequest,
    on_event: Callable[[ModelEvent], None] | None = None,
) -> ModelResult:
    """Consume normalized streaming events and require one terminal result."""
    if request.cancellation_token is not None:
        request.cancellation_token.raise_if_cancelled(provider=type(client).__name__)
    stream = getattr(client, "stream", None)
    if not callable(stream):
        return generate_model(client, request)

    terminal: ModelResult | None = None
    for event in stream(request):
        if request.cancellation_token is not None:
            request.cancellation_token.raise_if_cancelled(
                provider=type(client).__name__
            )
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
    if request.cancellation_token is not None:
        request.cancellation_token.raise_if_cancelled(provider=type(client).__name__)
    return terminal


__all__ = [
    "CancellationToken",
    "ModelEvent",
    "ModelMessage",
    "ModelProvider",
    "ModelRequest",
    "ModelResult",
    "ModelTool",
    "ModelUsage",
    "ModelUsageAggregate",
    "ProviderCancelledError",
    "ProviderConnectionError",
    "ProviderError",
    "ProviderProtocolError",
    "ProviderTimeoutError",
    "ToolCall",
    "UsageSource",
    "generate_model",
    "stream_model",
]
