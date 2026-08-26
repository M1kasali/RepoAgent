from types import SimpleNamespace

import pytest

from repoagent.empty_recovery import (
    RecoveryAction,
    RecoveryLimits,
    classify_empty_response,
    has_inline_thinking,
    has_thinking,
    visible_text,
)


def _result(text="", *, reasoning_content="", thinking_blocks=()):
    return SimpleNamespace(
        text=text,
        reasoning_content=reasoning_content,
        thinking_blocks=thinking_blocks,
    )


def _classify(result, visible="", **overrides):
    options = {
        "prev_had_tool_calls": False,
        "nudges_done": 0,
        "prefill_retries": 0,
        "empty_retries": 0,
        "limits": RecoveryLimits(),
    }
    options.update(overrides)
    return classify_empty_response(result, visible, **options)


def test_thinking_detection_and_visible_text():
    assert has_inline_thinking("<THINKING>x") is True
    assert has_inline_thinking("plain") is False
    assert has_thinking(_result(reasoning_content="hmm")) is True
    assert has_thinking(_result(thinking_blocks=({"type": "thinking"},))) is True
    assert has_thinking(_result("<think>hmm</think>")) is True
    assert visible_text("<think>hmm</think> answer") == "answer"
    assert visible_text("<think>hmm</think>") == ""


def test_visible_or_disabled_response_completes():
    assert _classify(_result("answer"), "answer") is RecoveryAction.COMPLETE
    assert (
        _classify(_result(), limits=RecoveryLimits(enabled=False))
        is RecoveryAction.COMPLETE
    )


def test_thinking_prefill_precedes_plain_retry():
    result = _result(reasoning_content="hmm")
    assert _classify(result, prefill_retries=0) is RecoveryAction.PREFILL
    assert _classify(result, prefill_retries=1) is RecoveryAction.PREFILL
    assert _classify(result, prefill_retries=2) is RecoveryAction.RETRY
    assert (
        _classify(result, prefill_retries=2, empty_retries=3) is RecoveryAction.COMPLETE
    )


def test_post_tool_nudge_is_bounded_and_yields_to_thinking():
    assert (
        _classify(_result(), prev_had_tool_calls=True, nudges_done=0)
        is RecoveryAction.NUDGE
    )
    assert (
        _classify(_result(), prev_had_tool_calls=True, nudges_done=1)
        is RecoveryAction.RETRY
    )
    assert (
        _classify(_result(reasoning_content="hmm"), prev_had_tool_calls=True)
        is RecoveryAction.PREFILL
    )


def test_plain_empty_retry_budget_and_limit_validation():
    assert _classify(_result(), empty_retries=2) is RecoveryAction.RETRY
    assert _classify(_result(), empty_retries=3) is RecoveryAction.COMPLETE
    with pytest.raises(ValueError, match="non-negative integer"):
        RecoveryLimits(empty_content_max_retries=-1)
