"""Effect-aware approval decisions for tool execution."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .tool_contracts import ToolDefinition, ToolEffect, ToolRequest


APPROVAL_MODES = frozenset({"ask", "auto", "never"})


@dataclass(frozen=True)
class ApprovalDecision:
    allowed: bool
    reason: str
    mode: str
    effect: ToolEffect
    required: bool

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "mode": self.mode,
            "effect": self.effect.value,
            "required": self.required,
        }


class EffectApprovalPolicy:
    """Apply immutable effect rules before consulting operator policy."""

    def __init__(
        self,
        mode: str,
        *,
        read_only: bool = False,
        prompt: Callable[[ToolDefinition, ToolRequest, Mapping], bool] | None = None,
    ) -> None:
        if mode not in APPROVAL_MODES:
            raise ValueError(f"invalid approval mode: {mode!r}")
        self.mode = mode
        self.read_only = bool(read_only)
        self._prompt = prompt

    def decide(
        self,
        definition: ToolDefinition,
        request: ToolRequest,
        arguments: Mapping,
    ) -> ApprovalDecision:
        if not isinstance(definition, ToolDefinition):
            raise TypeError("approval definition must be ToolDefinition")
        if not isinstance(request, ToolRequest):
            raise TypeError("approval request must be ToolRequest")
        effect = definition.effect
        required = definition.requires_approval or effect is not ToolEffect.READ
        if effect is ToolEffect.UNKNOWN:
            return ApprovalDecision(False, "unknown_effect", self.mode, effect, True)
        if self.read_only and effect is not ToolEffect.READ:
            return ApprovalDecision(False, "read_only", self.mode, effect, True)
        if not required:
            return ApprovalDecision(True, "safe_read", self.mode, effect, False)
        if self.mode == "auto":
            return ApprovalDecision(True, "policy_auto", self.mode, effect, True)
        if self.mode == "never":
            return ApprovalDecision(False, "policy_never", self.mode, effect, True)
        allowed = bool(self._prompt and self._prompt(definition, request, arguments))
        return ApprovalDecision(
            allowed,
            "operator_approved" if allowed else "operator_denied",
            self.mode,
            effect,
            True,
        )


__all__ = ["APPROVAL_MODES", "ApprovalDecision", "EffectApprovalPolicy"]
