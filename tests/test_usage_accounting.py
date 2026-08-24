import json

import pytest

from repoagent import RepoAgent, SessionStore, WorkspaceContext
from repoagent.providers import (
    FallbackModelClient,
    ModelEvent,
    ModelResult,
    ModelUsage,
    ModelUsageAggregate,
    ProviderError,
    ToolCall,
    UsageSource,
)


def _agent(tmp_path, provider):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return RepoAgent(
        model_client=provider,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )


def _artifacts(agent):
    run_id = agent.current_task_state.run_id
    report = json.loads(
        agent.run_store.report_path(run_id).read_text(encoding="utf-8")
    )
    trace = [
        json.loads(line)
        for line in agent.run_store.trace_path(run_id)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    turns = [
        json.loads(line)
        for line in agent.run_store.turn_events_path(run_id)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    return report, trace, turns


def test_usage_aggregate_sums_tokens_and_keeps_source_counts():
    aggregate = ModelUsageAggregate.from_usages(
        [
            ModelUsage(
                input_tokens=10,
                output_tokens=2,
                total_tokens=12,
                cache_read_tokens=3,
                source=UsageSource.ACTUAL,
            ),
            ModelUsage(
                input_tokens=20,
                output_tokens=4,
                total_tokens=24,
                cache_write_tokens=5,
                source=UsageSource.ACTUAL,
            ),
        ]
    )

    assert aggregate.usage == ModelUsage(
        input_tokens=30,
        output_tokens=6,
        total_tokens=36,
        cache_read_tokens=3,
        cache_write_tokens=5,
        source=UsageSource.ACTUAL,
    )
    assert aggregate.model_call_count == 2
    assert aggregate.complete is True
    assert aggregate.to_metadata()["usage_source_counts"] == {
        "actual": 2,
        "estimated": 0,
        "missing": 0,
        "mixed": 0,
    }


def test_usage_aggregate_marks_cross_source_totals_mixed():
    aggregate = ModelUsageAggregate.from_usages(
        [
            ModelUsage(10, 2, 12, source=UsageSource.ACTUAL),
            ModelUsage(8, 1, 9, source=UsageSource.ESTIMATED),
            ModelUsage(),
        ]
    )

    assert aggregate.usage.total_tokens == 21
    assert aggregate.usage.source is UsageSource.MIXED
    assert aggregate.complete is False
    assert aggregate.to_metadata()["usage_source_counts"] == {
        "actual": 1,
        "estimated": 1,
        "missing": 1,
        "mixed": 0,
    }


def test_usage_aggregate_rejects_non_usage_rows():
    with pytest.raises(TypeError, match="ModelUsage"):
        ModelUsageAggregate.from_usages([{}])


def test_multistep_turn_persists_aggregate_usage_everywhere(tmp_path):
    class Provider:
        supports_prompt_cache = False
        model = "metered"

        def __init__(self):
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                call = ToolCall("call-1", "list_files", {"path": "."})
                result = ModelResult(
                    tool_calls=(call,),
                    finish_reason="tool_calls",
                    usage=ModelUsage(
                        10,
                        2,
                        12,
                        cache_read_tokens=3,
                        source=UsageSource.ACTUAL,
                    ),
                    provider="metered",
                    model=self.model,
                )
                yield ModelEvent(kind="tool_call", tool_call=call)
            else:
                result = ModelResult(
                    text="<final>done</final>",
                    usage=ModelUsage(
                        20,
                        4,
                        24,
                        cache_write_tokens=5,
                        source=UsageSource.ACTUAL,
                    ),
                    provider="metered",
                    model=self.model,
                )
                yield ModelEvent(kind="text_delta", text=result.text)
            yield ModelEvent(kind="completed", result=result)

    agent = _agent(tmp_path, Provider())

    assert agent.ask("inspect") == "done"
    report, trace, turns = _artifacts(agent)

    expected = {
        "input_tokens": 30,
        "output_tokens": 6,
        "total_tokens": 36,
        "cache_read_tokens": 3,
        "cache_write_tokens": 5,
        "usage_source": "actual",
        "input_token_semantics": "ambiguous",
        "model_call_count": 2,
        "usage_source_counts": {
            "actual": 2,
            "estimated": 0,
            "missing": 0,
            "mixed": 0,
        },
        "usage_complete": True,
    }
    assert report["usage"] == expected
    assert {
        key: agent.last_completion_metadata[key] for key in expected
    } == expected
    parsed = [event for event in trace if event["event"] == "model_parsed"]
    assert [event["usage_aggregate"]["model_call_count"] for event in parsed] == [
        1,
        2,
    ]
    terminal = next(event for event in turns if event["kind"] == "turn.completed")
    assert terminal["payload"]["usage"] == {
        "prompt_tokens": 30,
        "completion_tokens": 6,
        "total_tokens": 36,
        "cache_read_tokens": 3,
        "cache_write_tokens": 5,
            "usage_source": "actual",
            "input_token_semantics": "ambiguous",
        "model_call_count": 2,
        "usage_source_counts": expected["usage_source_counts"],
    }


def test_missing_usage_makes_multistep_turn_explicitly_mixed(tmp_path):
    class Provider:
        supports_prompt_cache = False
        model = "partially-metered"

        def __init__(self):
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                call = ToolCall("call-1", "list_files", {"path": "."})
                result = ModelResult(
                    tool_calls=(call,),
                    usage=ModelUsage(10, 2, 12, source=UsageSource.ACTUAL),
                )
                yield ModelEvent(kind="tool_call", tool_call=call)
            else:
                result = ModelResult(text="<final>done</final>")
                yield ModelEvent(kind="text_delta", text=result.text)
            yield ModelEvent(kind="completed", result=result)

    agent = _agent(tmp_path, Provider())

    assert agent.ask("inspect") == "done"
    report, _trace, turns = _artifacts(agent)

    assert report["usage"]["usage_source"] == "mixed"
    assert report["usage"]["usage_complete"] is False
    assert report["usage"]["usage_source_counts"] == {
        "actual": 1,
        "estimated": 0,
        "missing": 1,
        "mixed": 0,
    }
    terminal = next(event for event in turns if event["kind"] == "turn.completed")
    assert terminal["payload"]["usage"]["usage_source"] == "mixed"


def test_successful_fallback_counts_failed_attempt_as_missing_usage(tmp_path):
    class Primary:
        supports_prompt_cache = False
        model = "primary"

        def stream(self, request):
            raise ProviderError(
                "unavailable",
                category="server",
                provider="primary",
                retryable=True,
                should_fallback=True,
                status_code=503,
            )
            yield

    class Backup:
        supports_prompt_cache = False
        model = "backup"

        def stream(self, request):
            result = ModelResult(
                text="<final>recovered</final>",
                usage=ModelUsage(7, 1, 8, source=UsageSource.ACTUAL),
                provider="backup",
                model=self.model,
            )
            yield ModelEvent(kind="text_delta", text=result.text)
            yield ModelEvent(kind="completed", result=result)

    agent = _agent(tmp_path, FallbackModelClient([Primary(), Backup()]))

    assert agent.ask("recover") == "recovered"
    report, _trace, _turns = _artifacts(agent)

    assert report["usage"]["model_call_count"] == 2
    assert report["usage"]["usage_source"] == "mixed"
    assert report["usage"]["usage_source_counts"] == {
        "actual": 1,
        "estimated": 0,
        "missing": 1,
        "mixed": 0,
    }


def test_failed_turn_retains_usage_from_completed_model_calls(tmp_path):
    class Provider:
        supports_prompt_cache = False
        model = "fails-late"

        def __init__(self):
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                call = ToolCall("call-1", "list_files", {"path": "."})
                result = ModelResult(
                    tool_calls=(call,),
                    usage=ModelUsage(
                        11, 2, 13, source=UsageSource.ACTUAL
                    ),
                )
                yield ModelEvent(kind="tool_call", tool_call=call)
                yield ModelEvent(kind="completed", result=result)
                return
            raise ProviderError(
                "late failure",
                category="server",
                provider="fails-late",
                retryable=True,
                should_fallback=True,
                status_code=503,
            )

    agent = _agent(tmp_path, Provider())

    with pytest.raises(RuntimeError, match="late failure"):
        agent.ask("inspect")

    turns = [
        json.loads(line)
        for line in agent.run_store.turn_events_path(
            agent.current_task_state.run_id
        )
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    terminal = next(event for event in turns if event["kind"] == "turn.failed")
    assert terminal["payload"]["usage"]["total_tokens"] == 13
    assert terminal["payload"]["usage"]["usage_source"] == "mixed"
    assert terminal["payload"]["usage"]["model_call_count"] == 2
    assert terminal["payload"]["usage"]["usage_source_counts"] == {
        "actual": 1,
        "estimated": 0,
        "missing": 1,
        "mixed": 0,
    }
