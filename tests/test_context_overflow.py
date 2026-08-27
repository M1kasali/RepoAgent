import json
from pathlib import Path

from repoagent import RepoAgent, SessionStore, WorkspaceContext
from repoagent.context_overflow import (
    OVERFLOW_ELISION_PLACEHOLDER,
    emergency_shrink_history,
    emergency_shrink_messages,
    fit_messages_to_token_budget,
)
from repoagent.providers import (
    ModelEvent,
    ModelMessage,
    ModelResult,
    ProviderError,
    ToolCall,
)
from repoagent.tokenization import Utf8TokenEstimator


def test_emergency_shrink_elides_all_but_three_recent_tool_results():
    messages = [ModelMessage(role="user", content="request")]
    for index in range(6):
        messages.extend(
            (
                ModelMessage(
                    role="assistant",
                    content="calling",
                    tool_calls=(),
                ),
                ModelMessage(
                    role="tool",
                    content=f"result {index}",
                    tool_call_id=f"call-{index}",
                    name="read_file",
                ),
            )
        )

    shrunk, elided = emergency_shrink_messages(messages)

    assert elided == 3
    assert [message.content for message in shrunk if message.role == "tool"] == [
        OVERFLOW_ELISION_PLACEHOLDER,
        OVERFLOW_ELISION_PLACEHOLDER,
        OVERFLOW_ELISION_PLACEHOLDER,
        "result 3",
        "result 4",
        "result 5",
    ]
    assert messages[0] is shrunk[0]


def test_emergency_shrink_is_noop_with_three_or_fewer_tool_results():
    messages = (
        ModelMessage(role="user", content="request"),
        ModelMessage(
            role="tool",
            content="result",
            tool_call_id="call-1",
            name="read_file",
        ),
    )

    shrunk, elided = emergency_shrink_messages(messages)

    assert shrunk == messages
    assert elided == 0


def test_emergency_shrink_history_copies_entries_and_preserves_recent_results():
    history = [{"role": "user", "content": "request"}]
    for index in range(5):
        history.append(
            {
                "role": "tool",
                "name": "read_file",
                "tool_call_id": f"call-{index}",
                "content": f"large result {index}",
            }
        )

    shrunk, elided = emergency_shrink_history(history)

    assert elided == 2
    assert [item["content"] for item in shrunk if item["role"] == "tool"] == [
        OVERFLOW_ELISION_PLACEHOLDER,
        OVERFLOW_ELISION_PLACEHOLDER,
        "large result 2",
        "large result 3",
        "large result 4",
    ]
    assert history[1]["content"] == "large result 0"
    assert shrunk[0] is not history[0]


def test_message_budget_reduction_drops_thinking_only_retry_as_one_unit():
    messages = (
        ModelMessage(role="user", content="request"),
        ModelMessage(
            role="assistant",
            reasoning_content="r" * 20_000,
            thinking_blocks=(
                {"type": "thinking", "thinking": "r" * 20_000},
            ),
        ),
    )

    fitted, evidence = fit_messages_to_token_budget(
        messages,
        Utf8TokenEstimator(provider="test", model="test"),
        1_000,
    )

    assert evidence["fitted"] is True
    assert evidence["after_tokens"] <= 1_000
    assert fitted == (messages[0],)
    assert evidence["dropped_thinking_only_messages"] == 1


def test_message_budget_reduction_never_mutates_signed_tool_thinking():
    call = ToolCall("call-1", "read_file", {"path": "x" * 20_000})
    thinking = (
        {"type": "thinking", "thinking": "required", "signature": "sig"},
    )
    messages = (
        ModelMessage(role="user", content="request"),
        ModelMessage(
            role="assistant",
            tool_calls=(call,),
            reasoning_content="r" * 20_000,
            thinking_blocks=thinking,
        ),
        ModelMessage(
            role="tool",
            content="result",
            tool_call_id="call-1",
            name="read_file",
        ),
    )

    fitted, evidence = fit_messages_to_token_budget(
        messages,
        Utf8TokenEstimator(provider="test", model="test"),
        1_000,
    )

    assert evidence["fitted"] is False
    assert fitted == messages
    assert fitted[1].thinking_blocks == thinking
    assert fitted[1].tool_calls[0].arguments == call.arguments


def test_agent_loop_shrinks_old_tool_results_and_retries_overflow(tmp_path):
    class OverflowThenAnswerProvider:
        supports_prompt_cache = False
        model = "overflow"

        def __init__(self):
            self.requests = []
            self.overflowed = False

        def stream(self, request):
            self.requests.append(request)
            tool_count = sum(message.role == "tool" for message in request.messages)
            if tool_count < 5:
                call = ToolCall(f"list-{tool_count}", "list_files", {"path": "."})
                yield ModelEvent(
                    kind="completed",
                    result=ModelResult(
                        tool_calls=(call,),
                        finish_reason="tool_calls",
                        provider="overflow",
                        model=self.model,
                    ),
                )
                return
            if not self.overflowed:
                self.overflowed = True
                raise ProviderError(
                    "maximum context length exceeded",
                    category="context_overflow",
                    provider="overflow",
                    should_compress=True,
                    status_code=400,
                )
            yield ModelEvent(
                kind="completed",
                result=ModelResult(
                    text="<final>Recovered after compaction.</final>",
                    provider="overflow",
                    model=self.model,
                ),
            )

    provider = OverflowThenAnswerProvider()
    agent = RepoAgent(
        model_client=provider,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
        max_steps=10,
    )

    assert agent.ask("inspect repeatedly") == "Recovered after compaction."
    assert provider.overflowed is True
    overflow_request = provider.requests[-2]
    recovery_request = provider.requests[-1]
    assert len(recovery_request.prompt) < len(overflow_request.prompt)
    trace = [
        json.loads(line)
        for line in Path(agent.run_store.trace_path(agent.current_task_state))
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    recovered = [
        event for event in trace if event["event"] == "context_overflow_recovered"
    ]
    assert len(recovered) == 1
    assert recovered[0]["elided_tool_results"] == 2
    assert recovered[0]["should_compress"] is True


def test_agent_loop_does_not_retry_overflow_without_elidable_results(tmp_path):
    class ImmediateOverflowProvider:
        supports_prompt_cache = False
        model = "overflow"

        def __init__(self):
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            raise ProviderError(
                "maximum context length exceeded",
                category="context_overflow",
                provider="overflow",
                should_compress=True,
                status_code=400,
            )
            yield

    provider = ImmediateOverflowProvider()
    agent = RepoAgent(
        model_client=provider,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )

    try:
        agent.ask("too large")
    except RuntimeError as exc:
        assert "maximum context length exceeded" in str(exc)
    else:
        raise AssertionError("overflow without elidable results unexpectedly recovered")
    assert provider.calls == 1
    trace = [
        json.loads(line)
        for line in Path(agent.run_store.trace_path(agent.current_task_state))
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    failed = [event for event in trace if event["event"] == "model_failed"]
    assert len(failed) == 1
    assert failed[0]["category"] == "context_overflow"
    assert failed[0]["should_compress"] is True
    state = json.loads(
        agent.run_store.task_state_path(agent.current_task_state)
        .read_text(encoding="utf-8")
    )
    report = json.loads(
        agent.run_store.report_path(agent.current_task_state)
        .read_text(encoding="utf-8")
    )
    assert state["status"] == "failed"
    assert state["stop_reason"] == "model_error"
    assert report["call_efficiency"]["call_count"] == 1
    assert report["call_efficiency"]["cost_complete"] is False


def test_message_budget_reduction_drops_old_tool_exchange_without_rewriting_calls():
    old_call = ToolCall("old", "write_file", {"content": "x" * 20_000})
    recent_call = ToolCall("recent", "read_file", {"path": "answer.go"})
    messages = (
        ModelMessage(role="user", content="request"),
        ModelMessage(role="assistant", tool_calls=(old_call,)),
        ModelMessage(
            role="tool", content="written", tool_call_id="old", name="write_file"
        ),
        ModelMessage(role="assistant", tool_calls=(recent_call,)),
        ModelMessage(
            role="tool", content="contents", tool_call_id="recent", name="read_file"
        ),
    )

    fitted, evidence = fit_messages_to_token_budget(
        messages,
        Utf8TokenEstimator(provider="test", model="test"),
        1_000,
    )

    assert evidence["fitted"] is True
    assert evidence["dropped_tool_exchanges"] == 1
    assert [call.id for message in fitted for call in message.tool_calls] == ["recent"]
    assert fitted[-1].tool_call_id == "recent"
    assert old_call.arguments == {"content": "x" * 20_000}
    assert recent_call.arguments == {"path": "answer.go"}
