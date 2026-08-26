"""Bounded per-turn recovery decisions for empty model responses."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


_THINK_TAG_RE = re.compile(r"<think>|<thinking>|<reasoning>", re.IGNORECASE)
_CLOSED_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)

POST_TOOL_NUDGE = (
    "You executed tool calls but returned an empty response. Use the tool "
    "results above to continue the task, or give your final answer now."
)


class RecoveryAction(str, Enum):
    COMPLETE = "complete"
    PREFILL = "prefill"
    NUDGE = "nudge"
    RETRY = "retry"


@dataclass(frozen=True)
class RecoveryLimits:
    enabled: bool = True
    post_tool_empty_max_nudges: int = 1
    thinking_prefill_max_retries: int = 2
    empty_content_max_retries: int = 3

    def __post_init__(self) -> None:
        for name in (
            "post_tool_empty_max_nudges",
            "thinking_prefill_max_retries",
            "empty_content_max_retries",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


def has_inline_thinking(content: str | None) -> bool:
    return bool(content) and bool(_THINK_TAG_RE.search(content))


def visible_text(content: str | None) -> str:
    if not content:
        return ""
    return _CLOSED_THINK_RE.sub("", str(content)).strip()


def has_thinking(result) -> bool:
    return bool(
        getattr(result, "reasoning_content", "")
        or getattr(result, "thinking_blocks", ())
        or has_inline_thinking(getattr(result, "text", ""))
    )


def classify_empty_response(
    result,
    visible: str | None,
    *,
    prev_had_tool_calls: bool,
    nudges_done: int,
    prefill_retries: int,
    empty_retries: int,
    limits: RecoveryLimits,
) -> RecoveryAction:
    if visible or not limits.enabled:
        return RecoveryAction.COMPLETE
    thinking = has_thinking(result)
    if thinking and prefill_retries < limits.thinking_prefill_max_retries:
        return RecoveryAction.PREFILL
    if (
        prev_had_tool_calls
        and not thinking
        and nudges_done < limits.post_tool_empty_max_nudges
    ):
        return RecoveryAction.NUDGE
    prefill_exhausted = (
        thinking and prefill_retries >= limits.thinking_prefill_max_retries
    )
    if empty_retries < limits.empty_content_max_retries and (
        not thinking or prefill_exhausted
    ):
        return RecoveryAction.RETRY
    return RecoveryAction.COMPLETE


__all__ = [
    "POST_TOOL_NUDGE",
    "RecoveryAction",
    "RecoveryLimits",
    "classify_empty_response",
    "has_inline_thinking",
    "has_thinking",
    "visible_text",
]
