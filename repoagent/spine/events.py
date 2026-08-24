"""Versioned, correlated events emitted by the runtime spine."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from .ids import RequestId, SessionId, TurnId


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    def to_dict(self) -> dict[str, Any]:
        return {
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
