"""Runner protocol and terminal outcome for a single Turn."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .ids import RequestId, SessionId, TurnId
from .turn import TurnRequest, TurnState


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


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
    explicit_reply: bool = False
    tool_calls: int = 0
    tool_failures: int = 0
    error: str | None = None

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
            "usage": {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
                "total_tokens": self.usage.total_tokens,
            },
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
