"""Semantic tracing contracts shared by runtime stages and evidence tooling."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


TRACE_FORMAT_VERSION = 1
TRACE_STAGES = frozenset(
    {"scheduler", "runtime", "provider", "tool", "memory", "delivery"}
)


@dataclass(frozen=True)
class EventDefinition:
    name: str
    stage: str
    required_attributes: tuple[str, ...] = ()
    terminal: bool = False

    def __post_init__(self) -> None:
        if not self.name or "." not in self.name:
            raise ValueError("semantic event names must be namespaced")
        if self.stage not in TRACE_STAGES:
            raise ValueError(f"unsupported trace stage: {self.stage}")


SEMANTIC_EVENTS = MappingProxyType(
    {
        definition.name: definition
        for definition in (
            EventDefinition("turn.accepted", "scheduler", ("work_class",)),
            EventDefinition("turn.started", "runtime"),
            EventDefinition("memory.recall.completed", "memory", ("hit_count",)),
            EventDefinition("memory.store.completed", "memory", ("message_count",)),
            EventDefinition("provider.call.completed", "provider", ("provider_call_id", "status")),
            EventDefinition("tool.call.completed", "tool", ("tool_call_id", "status")),
            EventDefinition("delivery.chunk", "delivery", ("content",)),
            EventDefinition("turn.completed", "delivery", terminal=True),
            EventDefinition("turn.failed", "delivery", terminal=True),
            EventDefinition("turn.cancelled", "delivery", terminal=True),
        )
    }
)


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    parent_span_id: str = ""
    stage: str = "runtime"
    format_version: int = TRACE_FORMAT_VERSION

    def __post_init__(self) -> None:
        if not self.trace_id.startswith("trace_"):
            raise ValueError("trace_id must use the trace_ prefix")
        if not self.span_id.startswith("span_"):
            raise ValueError("span_id must use the span_ prefix")
        if self.parent_span_id and not self.parent_span_id.startswith("span_"):
            raise ValueError("parent_span_id must be empty or use the span_ prefix")
        if self.stage not in TRACE_STAGES:
            raise ValueError(f"unsupported trace stage: {self.stage}")

    @classmethod
    def create(cls, *, stage: str = "scheduler") -> "TraceContext":
        return cls(
            trace_id="trace_" + uuid4().hex,
            span_id="span_" + uuid4().hex,
            stage=stage,
        )

    def child(self, stage: str) -> "TraceContext":
        return TraceContext(
            trace_id=self.trace_id,
            span_id="span_" + uuid4().hex,
            parent_span_id=self.span_id,
            stage=stage,
        )

    def for_stage(self, stage: str) -> "TraceContext":
        return replace(self, stage=stage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "stage": self.stage,
        }


_CURRENT_TRACE: ContextVar[TraceContext | None] = ContextVar(
    "repoagent_trace_context", default=None
)


def current_trace_context() -> TraceContext | None:
    return _CURRENT_TRACE.get()


def bind_trace_context(context: TraceContext):
    return _CURRENT_TRACE.set(context)


def reset_trace_context(token) -> None:
    _CURRENT_TRACE.reset(token)


def validate_semantic_event(name: str, attributes: Mapping[str, Any]) -> EventDefinition:
    try:
        definition = SEMANTIC_EVENTS[name]
    except KeyError as exc:
        raise ValueError(f"unknown semantic event: {name}") from exc
    missing = [
        key
        for key in definition.required_attributes
        if key not in attributes or attributes[key] in {None, ""}
    ]
    if missing:
        raise ValueError(
            f"semantic event {name} is missing required attributes: {', '.join(missing)}"
        )
    return definition


def semantic_event_definition(name: str) -> EventDefinition | None:
    return SEMANTIC_EVENTS.get(name)


def trace_attributes(context: TraceContext | None) -> dict[str, Any]:
    return context.to_dict() if context is not None else {}


def infer_trace_stage(event_name: str) -> str:
    name = str(event_name)
    if name.startswith(("model_", "provider.")) or name == "model_call_accounted":
        return "provider"
    if name.startswith(("tool_", "tool.")):
        return "tool"
    if name.startswith(("memory_", "memory.")):
        return "memory"
    if name.startswith(("runner.", "delivery.")) or name == "run_finished":
        return "delivery"
    if name == "turn.accepted":
        return "scheduler"
    return "runtime"


__all__ = [
    "EventDefinition",
    "SEMANTIC_EVENTS",
    "TRACE_FORMAT_VERSION",
    "TRACE_STAGES",
    "TraceContext",
    "bind_trace_context",
    "current_trace_context",
    "infer_trace_stage",
    "reset_trace_context",
    "semantic_event_definition",
    "trace_attributes",
    "validate_semantic_event",
]
