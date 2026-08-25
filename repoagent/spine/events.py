"""Versioned, correlated events emitted by the runtime spine."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from .ids import RequestId, SessionId, TurnId
from ..tracing import TraceContext, semantic_event_definition, validate_semantic_event


EVENT_FORMAT_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RuntimeEvent:
    kind: str
    turn_id: TurnId
    session_id: SessionId
    request_id: RequestId
    sequence: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: "event_" + uuid4().hex)
    occurred_at: str = field(default_factory=_utc_now)
    format_version: int = EVENT_FORMAT_VERSION
    trace_context: TraceContext | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        definition = semantic_event_definition(self.kind)
        if definition is not None and self.trace_context is not None:
            validate_semantic_event(self.kind, self.payload)
            if self.trace_context is not None and self.trace_context.stage != definition.stage:
                raise ValueError(
                    f"semantic event {self.kind} requires trace stage {definition.stage}"
                )
        semantic_name = self.payload.get("semantic_event")
        if semantic_name:
            semantic = validate_semantic_event(str(semantic_name), self.payload)
            if self.trace_context is not None and self.trace_context.stage != semantic.stage:
                raise ValueError(
                    f"semantic event {semantic_name} requires trace stage {semantic.stage}"
                )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "format_version": self.format_version,
            "event_id": self.event_id,
            "kind": self.kind,
            "turn_id": str(self.turn_id),
            "session_id": str(self.session_id),
            "request_id": str(self.request_id),
            "sequence": self.sequence,
            "occurred_at": self.occurred_at,
            "payload": dict(self.payload),
        }
        if self.trace_context is not None:
            payload.update(self.trace_context.to_dict())
        return payload
