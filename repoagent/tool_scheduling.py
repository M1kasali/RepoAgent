"""Immutable scheduling decisions for Tool Gateway batches."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .tool_contracts import ToolEffect, ToolRequest


class ToolBatchMode(str, Enum):
    SERIAL = "serial"
    PARALLEL = "parallel"


class MutationConflictPolicy(str, Enum):
    SERIAL = "serial"


@dataclass(frozen=True)
class ToolBatch:
    requests: tuple[ToolRequest, ...]
    mode: ToolBatchMode
    reason: str
    effects: tuple[ToolEffect, ...]
    mutation_conflict_policy: MutationConflictPolicy

    def __post_init__(self):
        requests = tuple(self.requests)
        effects = tuple(self.effects)
        if not requests:
            raise ValueError("tool batch must contain at least one request")
        if any(not isinstance(request, ToolRequest) for request in requests):
            raise ValueError("tool batch requests must be ToolRequest values")
        if not isinstance(self.mode, ToolBatchMode):
            raise ValueError("tool batch mode must be ToolBatchMode")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("tool batch reason must be non-empty")
        if len(effects) != len(requests) or any(
            not isinstance(effect, ToolEffect) for effect in effects
        ):
            raise ValueError("tool batch effects must align with requests")
        if not isinstance(self.mutation_conflict_policy, MutationConflictPolicy):
            raise ValueError("invalid mutation conflict policy")
        if self.mode is ToolBatchMode.PARALLEL:
            if len(requests) < 2:
                raise ValueError("parallel tool batch requires at least two requests")
            if any(effect is not ToolEffect.READ for effect in effects):
                raise ValueError("parallel tool batch may contain only read effects")
        if (
            self.mutation_conflict_policy is MutationConflictPolicy.SERIAL
            and any(effect is not ToolEffect.READ for effect in effects)
            and len(requests) != 1
        ):
            raise ValueError("serial mutation policy requires singleton side effects")
        object.__setattr__(self, "requests", requests)
        object.__setattr__(self, "effects", effects)

    def __iter__(self):
        return iter(self.requests)

    def __len__(self):
        return len(self.requests)

    def __getitem__(self, index):
        return self.requests[index]

    def to_dict(self):
        return {
            "mode": self.mode.value,
            "reason": self.reason,
            "effects": [effect.value for effect in self.effects],
            "mutation_conflict_policy": self.mutation_conflict_policy.value,
            "tool_call_ids": [request.call_id for request in self.requests],
        }


__all__ = [
    "MutationConflictPolicy",
    "ToolBatch",
    "ToolBatchMode",
]
