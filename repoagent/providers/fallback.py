"""Explicit provider fallback without mixing partial response streams."""

from dataclasses import dataclass, replace
import time
from typing import Any

from .base import (
    ModelEvent,
    ModelRequest,
    ModelResult,
    ProviderCancelledError,
    ProviderError,
    ProviderProtocolError,
    generate_model,
)


def _identity(provider: Any) -> tuple[str, str]:
    return type(provider).__name__, str(getattr(provider, "model", ""))


@dataclass(frozen=True)
class ProviderAttempt:
    index: int
    provider: str
    model: str
    status: str
    category: str = ""
    status_code: int | None = None
    duration_ms: int = 0

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("provider attempt index must not be negative")
        if self.duration_ms < 0:
            raise ValueError("provider attempt duration must not be negative")
        if self.status not in {"completed", "failed"}:
            raise ValueError(f"invalid provider attempt status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "category": self.category,
            "status_code": self.status_code,
            "duration_ms": self.duration_ms,
        }


class ProviderFallbackExhaustedError(ProviderError):
    """The configured chain failed after at least one provider switch."""

    def __init__(
        self, last_error: ProviderError, attempts: tuple[ProviderAttempt, ...]
    ) -> None:
        super().__init__(
            "provider fallback chain exhausted",
            category=last_error.category,
            provider=last_error.provider,
            retryable=last_error.retryable,
            should_fallback=False,
            should_compress=last_error.should_compress,
            status_code=last_error.status_code,
        )
        self.attempts = attempts

    def to_dict(self) -> dict[str, Any]:
        return {
            **super().to_dict(),
            "fallback_exhausted": True,
            "fallback_attempts": [attempt.to_dict() for attempt in self.attempts],
        }


class FallbackModelClient:
    """Try an ordered provider chain only on explicitly fallback-safe errors."""

    def __init__(self, providers) -> None:
        self.providers = tuple(providers)
        if not self.providers:
            raise ValueError("provider fallback chain must not be empty")
        self.model = str(getattr(self.providers[0], "model", ""))
        self.supports_prompt_cache = all(
            bool(getattr(provider, "supports_prompt_cache", False))
            for provider in self.providers
        )
        self.supports_structured_messages = all(
            bool(getattr(provider, "supports_structured_messages", False))
            for provider in self.providers
        )

    def _failure(
        self,
        index: int,
        provider: Any,
        error: ProviderError,
        started_at: float,
    ) -> ProviderAttempt:
        provider_name, model = _identity(provider)
        return ProviderAttempt(
            index=index,
            provider=error.provider or provider_name,
            model=model,
            status="failed",
            category=error.category,
            status_code=error.status_code,
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )

    def _result(
        self,
        result: ModelResult,
        index: int,
        provider: Any,
        failures: list[ProviderAttempt],
        started_at: float,
    ) -> ModelResult:
        provider_name, model = _identity(provider)
        selected_provider = result.provider or provider_name
        selected_model = result.model or model
        attempts = [
            *(attempt.to_dict() for attempt in failures),
            ProviderAttempt(
                index=index,
                provider=selected_provider,
                model=selected_model,
                status="completed",
                duration_ms=int((time.monotonic() - started_at) * 1000),
            ).to_dict(),
        ]
        metadata = {
            **dict(result.metadata),
            "fallback": {
                "used": index > 0,
                "selected_index": index,
                "selected_provider": selected_provider,
                "selected_model": selected_model,
                "attempts": attempts,
            },
        }
        return replace(result, metadata=metadata)

    def _raise_or_continue(
        self,
        *,
        index: int,
        error: ProviderError,
        attempts: list[ProviderAttempt],
        emitted: bool,
    ) -> None:
        has_next = index + 1 < len(self.providers)
        if not emitted and error.should_fallback and has_next:
            return
        if len(attempts) > 1:
            raise ProviderFallbackExhaustedError(error, tuple(attempts)) from error
        raise error

    def generate(self, request: ModelRequest) -> ModelResult:
        failures: list[ProviderAttempt] = []
        for index, provider in enumerate(self.providers):
            started_at = time.monotonic()
            try:
                result = generate_model(provider, request)
            except ProviderCancelledError:
                raise
            except ProviderError as error:
                failures.append(self._failure(index, provider, error, started_at))
                self._raise_or_continue(
                    index=index,
                    error=error,
                    attempts=failures,
                    emitted=False,
                )
                continue
            return self._result(result, index, provider, failures, started_at)
        raise AssertionError("provider fallback chain ended without an outcome")

    def stream(self, request: ModelRequest):
        failures: list[ProviderAttempt] = []
        for index, provider in enumerate(self.providers):
            started_at = time.monotonic()
            stream = getattr(provider, "stream", None)
            if not callable(stream):
                try:
                    raw_result = generate_model(provider, request)
                except ProviderCancelledError:
                    raise
                except ProviderError as error:
                    failures.append(self._failure(index, provider, error, started_at))
                    self._raise_or_continue(
                        index=index,
                        error=error,
                        attempts=failures,
                        emitted=False,
                    )
                    continue
                result = self._result(raw_result, index, provider, failures, started_at)
                if result.text:
                    yield ModelEvent(kind="text_delta", text=result.text)
                for tool_call in result.tool_calls:
                    yield ModelEvent(kind="tool_call", tool_call=tool_call)
                yield ModelEvent(kind="completed", result=result)
                return

            emitted = False
            completed = False
            try:
                for event in stream(request):
                    if not isinstance(event, ModelEvent):
                        raise ProviderProtocolError(
                            "provider stream yielded an invalid event",
                            provider=type(provider).__name__,
                        )
                    if event.kind == "completed":
                        if event.result is None:
                            raise ProviderProtocolError(
                                "completed model event is missing its result",
                                provider=type(provider).__name__,
                            )
                        completed = True
                        yield replace(
                            event,
                            result=self._result(
                                event.result,
                                index,
                                provider,
                                failures,
                                started_at,
                            ),
                        )
                    else:
                        emitted = True
                        yield event
                if not completed:
                    raise ProviderProtocolError(
                        "provider stream ended without a completed event",
                        provider=type(provider).__name__,
                    )
            except ProviderCancelledError:
                raise
            except ProviderError as error:
                failures.append(self._failure(index, provider, error, started_at))
                self._raise_or_continue(
                    index=index,
                    error=error,
                    attempts=failures,
                    emitted=emitted,
                )
                continue
            return
        raise AssertionError("provider fallback chain ended without an outcome")


__all__ = [
    "FallbackModelClient",
    "ProviderAttempt",
    "ProviderFallbackExhaustedError",
]
