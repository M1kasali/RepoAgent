"""Provider-safe reconstruction and budgeting of persisted conversation history."""

from __future__ import annotations

import copy
import json
import re
import secrets
from dataclasses import dataclass

from .providers.base import ModelMessage, ToolCall
from .tokenization import TokenCounter


def wrap_untrusted_tool_result(tool_name, content):
    source = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(tool_name))[:80] or "tool"
    nonce = secrets.token_hex(8)
    return (
        f"[BEGIN UNTRUSTED TOOL RESULT {source} #{nonce}]\n"
        f"{str(content)}\n"
        f"[END UNTRUSTED TOOL RESULT {source} #{nonce}]"
    )


def model_messages_token_count(messages, token_counter):
    if not isinstance(token_counter, TokenCounter):
        raise TypeError("message token counting requires a TokenCounter")
    payload = [
        {
            "role": message.role,
            "content": message.content,
            "tool_calls": [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments": dict(call.arguments),
                }
                for call in message.tool_calls
            ],
            "tool_call_id": message.tool_call_id,
            "name": message.name,
            "reasoning_content": message.reasoning_content,
            "thinking_blocks": [dict(block) for block in message.thinking_blocks],
        }
        for message in messages
    ]
    return token_counter.count(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    )


@dataclass(frozen=True)
class StructuredHistory:
    messages: tuple[ModelMessage, ...]
    token_count: int
    source_entry_count: int
    included_entry_count: int
    dropped_entry_count: int
    dropped_turn_count: int
    clipped_message_count: int

    def to_metadata(self):
        return {
            "mode": "structured-provider-messages",
            "token_count": self.token_count,
            "source_entry_count": self.source_entry_count,
            "included_entry_count": self.included_entry_count,
            "dropped_entry_count": self.dropped_entry_count,
            "dropped_turn_count": self.dropped_turn_count,
            "clipped_message_count": self.clipped_message_count,
            "message_count": len(self.messages),
        }


def build_structured_history(history, token_counter, token_budget):
    if not isinstance(token_counter, TokenCounter):
        raise TypeError("structured history requires a TokenCounter")
    if isinstance(token_budget, bool) or not isinstance(token_budget, int):
        raise TypeError("structured history token budget must be an integer")
    if token_budget < 0:
        raise ValueError("structured history token budget must not be negative")
    source = [dict(item) for item in history]
    groups = _user_turn_groups(source)
    selected = []
    selected_entries = 0
    clipped_messages = 0
    remaining = token_budget
    dropped_turns = 0

    for reverse_index, group in enumerate(reversed(groups)):
        messages = _messages_from_group(group)
        tokens = model_messages_token_count(messages, token_counter)
        clipped = 0
        if tokens > remaining and not selected:
            messages, clipped = _fit_group(group, token_counter, remaining)
            tokens = (
                model_messages_token_count(messages, token_counter) if messages else 0
            )
        if not messages or tokens > remaining:
            dropped_turns = len(groups) - reverse_index
            break
        selected.insert(0, messages)
        selected_entries += len(group)
        clipped_messages += clipped
        remaining -= tokens

    flattened = tuple(message for group in selected for message in group)
    token_count = (
        model_messages_token_count(flattened, token_counter) if flattened else 0
    )
    return StructuredHistory(
        messages=flattened,
        token_count=token_count,
        source_entry_count=len(source),
        included_entry_count=selected_entries,
        dropped_entry_count=len(source) - selected_entries,
        dropped_turn_count=dropped_turns,
        clipped_message_count=clipped_messages,
    )


def _user_turn_groups(history):
    groups = []
    current = []
    for item in history:
        if item.get("role") == "user":
            if current:
                groups.append(current)
            current = [item]
        elif current:
            current.append(item)
    if current:
        groups.append(current)
    return groups


def _tool_call(value):
    value = dict(value)
    function = dict(value.get("function") or {})
    call_id = str(value.get("id") or "").strip()
    name = str(value.get("name") or function.get("name") or "").strip()
    arguments = value.get("args", value.get("arguments", function.get("arguments", {})))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}
    if not call_id or not name or not isinstance(arguments, dict):
        return None
    return ToolCall(call_id, name, arguments)


def _messages_from_group(group):
    messages = []
    pending_call_ids = set()
    for item in group:
        role = str(item.get("role", ""))
        content = str(item.get("content", ""))
        if role == "user":
            messages.append(ModelMessage(role="user", content=content))
            pending_call_ids.clear()
        elif role == "assistant":
            calls = tuple(
                call
                for value in item.get("tool_calls", ())
                if (call := _tool_call(value)) is not None
            )
            reasoning_content = str(item.get("reasoning_content", ""))
            thinking_blocks = tuple(
                dict(block) for block in item.get("thinking_blocks", ())
            )
            pending_call_ids = {call.id for call in calls}
            if content or calls or reasoning_content or thinking_blocks:
                messages.append(
                    ModelMessage(
                        role="assistant",
                        content=content,
                        tool_calls=calls,
                        reasoning_content=reasoning_content,
                        thinking_blocks=thinking_blocks,
                    )
                )
        elif role == "tool":
            call_id = str(item.get("tool_call_id") or "").strip()
            name = str(item.get("name") or "invalid_tool").strip()
            if not call_id:
                continue
            if call_id not in pending_call_ids:
                messages.append(
                    ModelMessage(
                        role="assistant",
                        tool_calls=(
                            ToolCall(call_id, name, dict(item.get("args", {}))),
                        ),
                    )
                )
                pending_call_ids = {call_id}
            messages.append(
                ModelMessage(
                    role="tool",
                    content=wrap_untrusted_tool_result(name, content),
                    tool_call_id=call_id,
                    name=name,
                )
            )
            pending_call_ids.discard(call_id)
    return tuple(messages)


def _fit_group(group, token_counter, budget):
    if budget <= 0:
        return (), 0
    original = copy.deepcopy(group)

    def clipped(char_limit):
        candidate = copy.deepcopy(original)
        changed = 0
        for item in candidate:
            content = str(item.get("content", ""))
            if len(content) <= char_limit:
                continue
            item["content"] = (
                "(elided)" if char_limit == 0 else content[:char_limit] + "..."
            )
            changed += 1
        return _messages_from_group(candidate), changed

    minimum, minimum_changed = clipped(0)
    if not minimum or model_messages_token_count(minimum, token_counter) > budget:
        return (), 0
    high = max((len(str(item.get("content", ""))) for item in original), default=0)
    low = 0
    best = minimum
    best_changed = minimum_changed
    while low <= high:
        middle = (low + high) // 2
        candidate, changed = clipped(middle)
        if model_messages_token_count(candidate, token_counter) <= budget:
            best = candidate
            best_changed = changed
            low = middle + 1
        else:
            high = middle - 1
    return best, best_changed


__all__ = [
    "StructuredHistory",
    "build_structured_history",
    "model_messages_token_count",
    "wrap_untrusted_tool_result",
]
