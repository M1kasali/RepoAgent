"""Stable request, lifecycle, event, and runner contracts."""

from .events import EVENT_FORMAT_VERSION, RuntimeEvent
from .ids import RequestId, SessionId, TurnId
from .runner import Drain, Emit, Text, TurnOutcome, TurnRunner, Usage
from .runtime import TurnRuntime
from .scheduler import Scheduler, SchedulerDrainingError, TurnHandle, WorkPools
from .turn import (
    LEGAL_TURN_TRANSITIONS,
    TERMINAL_TURN_STATES,
    IllegalTurnTransition,
    TurnLifecycle,
    TurnRequest,
    TurnState,
    WorkClass,
)

__all__ = [
    "Drain",
    "Emit",
    "EVENT_FORMAT_VERSION",
    "IllegalTurnTransition",
    "LEGAL_TURN_TRANSITIONS",
    "RequestId",
    "RuntimeEvent",
    "Scheduler",
    "SchedulerDrainingError",
    "SessionId",
    "TERMINAL_TURN_STATES",
    "Text",
    "TurnId",
    "TurnLifecycle",
    "TurnOutcome",
    "TurnRequest",
    "TurnRunner",
    "TurnRuntime",
    "TurnState",
    "Usage",
    "WorkClass",
    "WorkPools",
    "TurnHandle",
]
