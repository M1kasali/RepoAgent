"""Bounded detection and nudging for deterministic Tool failure loops."""

from __future__ import annotations

import json
import re

from .tool_contracts import ToolResult


TOOL_LOOP_BREAK_THRESHOLD = 2
TOOL_LOOP_BREAK_MAX_NUDGES = 2
_TRANSIENT_MARKERS = (
    "429",
    "rate limit",
    "timed out",
    "timeout",
    "no healthy upstream",
    "502",
    "503",
)
_EMPTY_SUCCESS_MARKERS = ("no matches found", "no files found")
_TRANSIENT_ERROR_CODES = {"tool_timeout", "tool_cancelled"}


def is_tool_failure(result) -> bool:
    if isinstance(result, ToolResult):
        return result.status in {"error", "rejected", "timeout", "cancelled"}
    text = str(result).strip()
    match = re.search(r"(?:^|\n)(?:Exit code|exit_code):\s*(-?\d+)(?:\s|$)", text)
    if match:
        return match.group(1) != "0"
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass
        else:
            if isinstance(payload, dict) and payload.get("error"):
                return True
    lowered = text.lower()
    return lowered.startswith(
        (
            "error:",
            "error ",
            "proxy error:",
            "(mcp tool call failed:",
            "(mcp tool call timed out ",
            "(mcp tool call was cancelled)",
        )
    )


def is_hard_tool_failure(result) -> bool:
    content = result.content if isinstance(result, ToolResult) else str(result)
    lowered = content.lower()
    if isinstance(result, ToolResult):
        if result.error_code in _TRANSIENT_ERROR_CODES:
            return False
        if result.status == "partial_success" or result.workspace_changed:
            return False
    if any(marker in lowered for marker in _TRANSIENT_MARKERS):
        return False
    if content.strip().rstrip(".").lower() in _EMPTY_SUCCESS_MARKERS:
        return False
    return is_tool_failure(result)


def loop_break_nudge(tool: str, failures: int) -> str:
    return (
        f"[loop] `{tool}` has failed {failures} times in a row with the same kind "
        "of error. Stop repeating it. If it is an external dependency "
        "(network/API/search), complete what you can offline from local data and "
        "report what stayed blocked. If it is a file or path error, re-examine "
        "the EXACT path before any retry - do not call it again unchanged. "
        "Otherwise change approach: a different tool, command, or strategy."
    )


__all__ = [
    "TOOL_LOOP_BREAK_MAX_NUDGES",
    "TOOL_LOOP_BREAK_THRESHOLD",
    "is_hard_tool_failure",
    "is_tool_failure",
    "loop_break_nudge",
]
