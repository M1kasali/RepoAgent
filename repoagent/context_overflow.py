"""Deterministic emergency reduction for an overflowing Provider transcript."""

from __future__ import annotations

from dataclasses import replace

from .conversation import model_messages_token_count
OVERFLOW_ELISION_PLACEHOLDER = "[earlier tool output elided to fit the context window]"
OVERFLOW_KEEP_RECENT_TOOL_RESULTS = 3
OVERFLOW_MAX_COMPRESS_RETRIES = 2
OVERFLOW_PAYLOAD_ELISION_PLACEHOLDER = "[content elided to fit input budget]"


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


def fit_messages_to_token_budget(messages, token_counter, token_budget):
    """Bound replay payloads without orphaning native Tool call/result pairs."""

    if isinstance(token_budget, bool) or not isinstance(token_budget, int):
        raise TypeError("message token budget must be an integer")
    if token_budget < 1:
        raise ValueError("message token budget must be positive")
    original = tuple(messages)
    before_tokens = model_messages_token_count(original, token_counter)
    if before_tokens <= token_budget:
        return original, {
            "before_tokens": before_tokens,
            "after_tokens": before_tokens,
            "elided_tool_results": 0,
            "clipped_messages": 0,
            "dropped_thinking_only_messages": 0,
            "dropped_tool_exchanges": 0,
            "fitted": True,
        }

    shrunk, elided_tool_results = emergency_shrink_messages(original)
    without_thinking_only = tuple(
        message
        for message in shrunk
        if not (
            message.role == "assistant"
            and message.thinking_blocks
            and not message.content
            and not message.tool_calls
        )
    )
    dropped_thinking_only = len(shrunk) - len(without_thinking_only)
    shrunk = without_thinking_only
    shrunk, dropped_tool_exchanges = _drop_old_tool_exchanges_to_budget(
        shrunk, token_counter, token_budget
    )
    shrunk_tokens = model_messages_token_count(shrunk, token_counter)
    if shrunk_tokens <= token_budget:
        return shrunk, {
            "before_tokens": before_tokens,
            "after_tokens": shrunk_tokens,
            "elided_tool_results": elided_tool_results,
            "clipped_messages": elided_tool_results,
            "dropped_thinking_only_messages": dropped_thinking_only,
            "dropped_tool_exchanges": dropped_tool_exchanges,
            "fitted": True,
        }

    def clipped(char_limit):
        output = []
        changed_messages = 0
        for message in shrunk:
            if message.role in {"system", "user"}:
                output.append(message)
                continue
            content = message.content
            reasoning = message.reasoning_content
            thinking = message.thinking_blocks
            changed = False
            if message.role == "tool" and len(content) > char_limit:
                content = OVERFLOW_ELISION_PLACEHOLDER
                changed = True
            elif len(content) > char_limit:
                content = _clip_text(content, char_limit)
                changed = True
            if len(reasoning) > char_limit:
                reasoning = _clip_text(reasoning, char_limit)
                changed = True
            if changed:
                changed_messages += 1
                message = replace(
                    message,
                    content=content,
                    reasoning_content=reasoning,
                    thinking_blocks=thinking,
                )
            output.append(message)
        return tuple(output), changed_messages

    high = max(
        (
            max(
                len(message.content),
                len(message.reasoning_content),
            )
            for message in shrunk
            if message.role not in {"system", "user"}
        ),
        default=0,
    )
    minimum, minimum_changed = clipped(0)
    minimum_tokens = model_messages_token_count(minimum, token_counter)
    if minimum_tokens > token_budget:
        return original, {
            "before_tokens": before_tokens,
            "after_tokens": before_tokens,
            "elided_tool_results": 0,
            "clipped_messages": 0,
            "dropped_thinking_only_messages": 0,
            "dropped_tool_exchanges": 0,
            "fitted": False,
        }

    low = 0
    best = minimum
    best_changed = minimum_changed
    best_tokens = minimum_tokens
    while low <= high:
        middle = (low + high) // 2
        candidate, changed = clipped(middle)
        candidate_tokens = model_messages_token_count(candidate, token_counter)
        if candidate_tokens <= token_budget:
            best = candidate
            best_changed = changed
            best_tokens = candidate_tokens
            low = middle + 1
        else:
            high = middle - 1
    return best, {
        "before_tokens": before_tokens,
        "after_tokens": best_tokens,
        "elided_tool_results": elided_tool_results,
        "clipped_messages": best_changed,
        "dropped_thinking_only_messages": dropped_thinking_only,
        "dropped_tool_exchanges": dropped_tool_exchanges,
        "fitted": True,
    }


def _drop_old_tool_exchanges_to_budget(messages, token_counter, token_budget):
    """Remove complete old Tool exchanges; never rewrite signed replay blocks."""

    working = tuple(messages)
    dropped = 0
    while model_messages_token_count(working, token_counter) > token_budget:
        exchanges = []
        for assistant_index, message in enumerate(working):
            if message.role != "assistant" or not message.tool_calls:
                continue
            call_ids = {call.id for call in message.tool_calls}
            result_indexes = {
                index
                for index, candidate in enumerate(working)
                if candidate.role == "tool" and candidate.tool_call_id in call_ids
            }
            if len(result_indexes) == len(call_ids):
                exchanges.append(({assistant_index, *result_indexes}, assistant_index))
        if len(exchanges) <= 1:
            break
        remove_indexes, _ = exchanges[0]
        working = tuple(
            message for index, message in enumerate(working) if index not in remove_indexes
        )
        dropped += 1
    return working, dropped


def _clip_text(value, char_limit):
    if char_limit <= 0:
        return OVERFLOW_PAYLOAD_ELISION_PLACEHOLDER
    return value[:char_limit] + "..."


__all__ = [
    "OVERFLOW_ELISION_PLACEHOLDER",
    "OVERFLOW_KEEP_RECENT_TOOL_RESULTS",
    "OVERFLOW_MAX_COMPRESS_RETRIES",
    "fit_messages_to_token_budget",
    "emergency_shrink_history",
    "emergency_shrink_messages",
]
