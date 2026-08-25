"""Turn request and lifecycle state for one accepted agent request."""

from dataclasses import dataclass, field
from enum import Enum

from .ids import RequestId, SessionId, TurnId, new_request_id, new_turn_id
from ..tracing import TraceContext


class TurnState(str, Enum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def __str__(self) -> str:
        return self.value


class WorkClass(str, Enum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"

    def __str__(self) -> str:
        return self.value


TERMINAL_TURN_STATES = frozenset(
    {TurnState.COMPLETED, TurnState.FAILED, TurnState.CANCELLED}
)

LEGAL_TURN_TRANSITIONS = {
    TurnState.ACCEPTED: frozenset({TurnState.RUNNING, TurnState.CANCELLED}),
    TurnState.RUNNING: frozenset(
        {TurnState.COMPLETED, TurnState.FAILED, TurnState.CANCELLED}
    ),
    TurnState.COMPLETED: frozenset(),
    TurnState.FAILED: frozenset(),
    TurnState.CANCELLED: frozenset(),
}


class IllegalTurnTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class TurnRequest:
    turn_id: TurnId
    session_id: SessionId
    request_id: RequestId
    text: str
    work_class: WorkClass = WorkClass.FOREGROUND
    trace_context: TraceContext = field(
        default_factory=lambda: TraceContext.create(stage="scheduler")
    )

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        text: str,
        turn_id: TurnId | None = None,
        request_id: RequestId | None = None,
        work_class: WorkClass = WorkClass.FOREGROUND,
        trace_context: TraceContext | None = None,
    ) -> "TurnRequest":
        return cls(
            turn_id=turn_id or new_turn_id(),
            session_id=SessionId(str(session_id)),
            request_id=request_id or new_request_id(),
            text=str(text),
            work_class=work_class,
            trace_context=trace_context or TraceContext.create(stage="scheduler"),
        )


@dataclass
class TurnLifecycle:
    request: TurnRequest
    state: TurnState = TurnState.ACCEPTED
    sequence: int = field(default=0, init=False)

    def transition(self, target: TurnState) -> TurnState:
        if target not in LEGAL_TURN_TRANSITIONS[self.state]:
            raise IllegalTurnTransition(
                f"illegal Turn transition: {self.state.value} -> {target.value}"
            )
        self.state = target
        return self.state

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence
