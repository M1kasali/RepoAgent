"""Runner protocol and terminal outcome for a single Turn."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from .ids import RequestId, SessionId, TurnId
from .turn import TurnRequest, TurnState


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    source: str = "missing"
    input_token_semantics: str = "ambiguous"
    model_call_count: int = 0
    source_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        numeric = (
            self.prompt_tokens,
            self.completion_tokens,
            self.total_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
            self.model_call_count,
        )
        if any(value < 0 for value in numeric):
            raise ValueError("Turn usage values must not be negative")
        if self.source not in {"actual", "estimated", "missing", "mixed"}:
            raise ValueError(f"invalid Turn usage source: {self.source}")
        if self.input_token_semantics not in {
            "fresh",
            "total",
            "ambiguous",
        }:
            raise ValueError(
                "invalid Turn input token semantics: "
                f"{self.input_token_semantics}"
            )
        counts = {
            source: int(dict(self.source_counts).get(source, 0))
            for source in ("actual", "estimated", "missing", "mixed")
        }
        if any(value < 0 for value in counts.values()):
            raise ValueError("Turn usage source counts must not be negative")
        if counts and sum(counts.values()) not in {0, self.model_call_count}:
            raise ValueError(
                "Turn usage source counts must equal model_call_count"
            )
        object.__setattr__(self, "source_counts", MappingProxyType(counts))

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "usage_source": self.source,
            "input_token_semantics": self.input_token_semantics,
            "model_call_count": self.model_call_count,
            "usage_source_counts": dict(self.source_counts),
        }


@dataclass(frozen=True)
class Text:
    content: str


RunnerEvent = Text
Emit = Callable[[RunnerEvent], Awaitable[None]]
Drain = Callable[[], list[TurnRequest]]


@dataclass(frozen=True)
class TurnOutcome:
    turn_id: TurnId
    request_id: RequestId
    session_id: SessionId
    state: TurnState
    final_answer: str = ""
    usage: Usage = field(default_factory=Usage)
    call_efficiency: Mapping[str, object] = field(default_factory=dict)
    explicit_reply: bool = False
    tool_calls: int = 0
    tool_failures: int = 0
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "call_efficiency",
            MappingProxyType(dict(self.call_efficiency)),
        )

    @property
    def terminal(self) -> bool:
        return self.state in {
            TurnState.COMPLETED,
            TurnState.FAILED,
            TurnState.CANCELLED,
        }

    def to_dict(self) -> dict:
        return {
            "turn_id": str(self.turn_id),
            "request_id": str(self.request_id),
            "session_id": str(self.session_id),
            "state": self.state.value,
            "final_answer": self.final_answer,
            "usage": self.usage.to_dict(),
            "call_efficiency": dict(self.call_efficiency),
            "explicit_reply": self.explicit_reply,
            "tool_calls": self.tool_calls,
            "tool_failures": self.tool_failures,
            "error": self.error,
        }


@runtime_checkable
class TurnRunner(Protocol):
    async def run(
        self, request: TurnRequest, emit: Emit, drain: Drain
    ) -> TurnOutcome: ...
