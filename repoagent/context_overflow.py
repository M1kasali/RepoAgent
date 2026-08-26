"""Deterministic emergency reduction for an overflowing Provider transcript."""

from __future__ import annotations

from dataclasses import replace


OVERFLOW_ELISION_PLACEHOLDER = "[earlier tool output elided to fit the context window]"
OVERFLOW_KEEP_RECENT_TOOL_RESULTS = 3
OVERFLOW_MAX_COMPRESS_RETRIES = 2


def emergency_shrink_messages(messages):
    messages = tuple(messages)
    tool_indexes = [
        index for index, message in enumerate(messages) if message.role == "tool"
    ]
    if len(tool_indexes) <= OVERFLOW_KEEP_RECENT_TOOL_RESULTS:
        return messages, 0
    elide = set(tool_indexes[:-OVERFLOW_KEEP_RECENT_TOOL_RESULTS])
    shrunk = []
    elided = 0
    for index, message in enumerate(messages):
        if (
            index in elide
            and message.content
            and message.content != OVERFLOW_ELISION_PLACEHOLDER
        ):
            shrunk.append(replace(message, content=OVERFLOW_ELISION_PLACEHOLDER))
            elided += 1
        else:
            shrunk.append(message)
    return tuple(shrunk), elided


def emergency_shrink_history(history):
    history = [dict(item) for item in history]
    tool_indexes = [
        index for index, item in enumerate(history) if item.get("role") == "tool"
    ]
    if len(tool_indexes) <= OVERFLOW_KEEP_RECENT_TOOL_RESULTS:
        return history, 0
    elide = set(tool_indexes[:-OVERFLOW_KEEP_RECENT_TOOL_RESULTS])
    elided = 0
    for index in elide:
        item = history[index]
        if item.get("content") and item.get("content") != OVERFLOW_ELISION_PLACEHOLDER:
            item["content"] = OVERFLOW_ELISION_PLACEHOLDER
            elided += 1
    return history, elided


__all__ = [
    "OVERFLOW_ELISION_PLACEHOLDER",
    "OVERFLOW_KEEP_RECENT_TOOL_RESULTS",
    "OVERFLOW_MAX_COMPRESS_RETRIES",
    "emergency_shrink_history",
    "emergency_shrink_messages",
]
