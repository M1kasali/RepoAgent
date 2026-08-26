from repoagent.conversation import (
    build_structured_history,
    model_messages_token_count,
)
from repoagent.providers import ModelMessage, ToolCall
from repoagent.tokenization import Utf8TokenEstimator


def _counter():
    return Utf8TokenEstimator(provider="test", model="test", bytes_per_token=1)


def test_structured_history_reconstructs_orphan_legacy_tool_pair():
    history = [
        {"role": "user", "content": "inspect"},
        {
            "role": "tool",
            "name": "read_file",
            "args": {"path": "README.md"},
            "tool_call_id": "legacy-1",
            "content": "contents",
        },
        {"role": "assistant", "content": "done"},
    ]

    result = build_structured_history(history, _counter(), 10_000)

    assert [message.role for message in result.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert result.messages[1].tool_calls[0] == ToolCall(
        "legacy-1", "read_file", {"path": "README.md"}
    )
    assert result.messages[2].tool_call_id == "legacy-1"


def test_structured_history_clips_complete_latest_turn_and_drops_older_turns():
    history = [
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "recent request"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "read-1", "name": "read_file", "args": {"path": "a.txt"}}
            ],
        },
        {
            "role": "tool",
            "name": "read_file",
            "args": {"path": "a.txt"},
            "tool_call_id": "read-1",
            "content": "x" * 2_000,
        },
        {"role": "assistant", "content": "recent answer"},
    ]
    recent = build_structured_history(history[2:], _counter(), 20_000)
    recent_only_budget = recent.token_count - 500

    result = build_structured_history(history, _counter(), recent_only_budget)

    assert result.messages[0].content == "recent request"
    assert result.dropped_turn_count == 1
    assert result.clipped_message_count >= 1
    tool = next(message for message in result.messages if message.role == "tool")
    assert "[BEGIN UNTRUSTED TOOL RESULT read_file" in tool.content
    assert "[END UNTRUSTED TOOL RESULT read_file" in tool.content
    assert result.token_count <= recent_only_budget


def test_model_message_token_count_includes_tool_arguments():
    counter = _counter()
    small = (
        ModelMessage(
            role="assistant",
            tool_calls=(ToolCall("call-1", "read_file", {"path": "a"}),),
        ),
    )
    large = (
        ModelMessage(
            role="assistant",
            tool_calls=(ToolCall("call-1", "read_file", {"path": "a" * 500}),),
        ),
    )

    assert model_messages_token_count(large, counter) > model_messages_token_count(
        small, counter
    )
