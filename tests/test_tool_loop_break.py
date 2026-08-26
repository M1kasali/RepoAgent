import json

import pytest

from repoagent import RepoAgent, SessionStore, WorkspaceContext
from repoagent.providers import ModelEvent, ModelResult, ToolCall
from repoagent.tool_contracts import ToolEffect, ToolResult
from repoagent.tool_loop_break import (
    TOOL_LOOP_BREAK_MAX_NUDGES,
    is_hard_tool_failure,
)


@pytest.mark.parametrize(
    "result,expected",
    [
        ("Error: Tool 'x' not found", True),
        ("Error: file does not exist", True),
        ("No matches found.", False),
        ("No files found", False),
        ("route not found in cache, using local fallback", False),
        ("Exit code: 1\nboom", True),
        ("Exit code: 0\nok", False),
        ("No error: the fallback completed", False),
        ('{"error": "URL validation failed"}', True),
        ("Error: 429 rate limit, retry later", False),
        ("request timed out", False),
    ],
)
def test_hard_tool_failure_classification(result, expected):
    assert is_hard_tool_failure(result) is expected


def test_structured_transient_and_partial_results_are_not_hard_failures():
    timeout = ToolResult(
        call_id="timeout-1",
        name="run_shell",
        status="timeout",
        effect=ToolEffect.EXECUTE,
        content="request timed out",
        error_code="tool_timeout",
    )
    partial = ToolResult(
        call_id="partial-1",
        name="run_shell",
        status="partial_success",
        effect=ToolEffect.EXECUTE,
        content="exit_code: 1",
        error_code="tool_partial_success",
        affected_paths=("changed.txt",),
        workspace_changed=True,
    )
    assert is_hard_tool_failure(timeout) is False
    assert is_hard_tool_failure(partial) is False


def test_repeated_hard_tool_failures_receive_two_bounded_nudges(tmp_path):
    class RepeatingProvider:
        supports_prompt_cache = False
        model = "repeating"

        def __init__(self):
            self.requests = []
            self.marker_counts = []

        def stream(self, request):
            self.requests.append(request)
            self.marker_counts.append(
                sum("[loop]" in message.content for message in request.messages)
            )
            if not request.tools:
                yield ModelEvent(
                    kind="completed",
                    result=ModelResult(
                        text="<final>Stopped repeating.</final>",
                        provider="repeating",
                        model=self.model,
                    ),
                )
                return
            call = ToolCall(f"missing-{len(self.requests)}", "missing_tool", {})
            yield ModelEvent(
                kind="completed",
                result=ModelResult(
                    tool_calls=(call,),
                    finish_reason="tool_calls",
                    provider="repeating",
                    model=self.model,
                ),
            )

    provider = RepeatingProvider()
    agent = RepoAgent(
        model_client=provider,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
        max_steps=6,
    )

    assert agent.ask("keep trying") == "Stopped repeating."
    assert max(provider.marker_counts) == TOOL_LOOP_BREAK_MAX_NUDGES
    assert (
        sum("[loop]" in item.get("content", "") for item in agent.session["history"])
        == TOOL_LOOP_BREAK_MAX_NUDGES
    )
    trace = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    nudges = [event for event in trace if event["event"] == "tool_failure_loop_nudged"]
    assert len(nudges) == TOOL_LOOP_BREAK_MAX_NUDGES
    assert {event["consecutive_failures"] for event in nudges} == {2}
