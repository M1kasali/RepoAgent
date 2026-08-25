import asyncio
from io import BytesIO
import json
import threading
import time
import urllib.error
from unittest.mock import patch

import pytest

from repoagent import RepoAgent, SessionStore, WorkspaceContext
from repoagent.providers import (
    AnthropicCompatibleModelClient,
    CancellationToken,
    FallbackModelClient,
    ModelEvent,
    ModelRequest,
    ModelResult,
    ModelTool,
    ModelUsage,
    OllamaModelClient,
    OpenAICompatibleModelClient,
    ProviderCancelledError,
    ProviderError,
    ProviderFallbackExhaustedError,
    ProviderProtocolError,
    ToolCall,
    UsageSource,
    generate_model,
    stream_model,
)


def test_model_request_validates_budget_timeout_and_attempt():
    with pytest.raises(ValueError, match="positive"):
        ModelRequest(prompt="hello", max_output_tokens=0)
    with pytest.raises(ValueError, match="timeout_seconds"):
        ModelRequest(prompt="hello", max_output_tokens=1, timeout_seconds=0)
    with pytest.raises(ValueError, match="attempt"):
        ModelRequest(prompt="hello", max_output_tokens=1, attempt=0)


def test_cancellation_token_is_idempotent_and_closes_registered_resources():
    token = CancellationToken()
    calls = []
    remove = token.add_callback(lambda: calls.append("closed"))

    assert token.cancel() is True
    assert token.cancel() is False
    remove()
    assert calls == ["closed"]
    with pytest.raises(ProviderCancelledError):
        token.raise_if_cancelled(provider="test")


def test_model_usage_normalizes_actual_and_missing_metadata():
    actual = ModelUsage.from_metadata(
        {
            "input_tokens": 10,
            "output_tokens": 2,
            "cached_tokens": 4,
        }
    )
    missing = ModelUsage.from_metadata({})

    assert actual == ModelUsage(
        input_tokens=10,
        output_tokens=2,
        total_tokens=12,
        cache_read_tokens=4,
        source=UsageSource.ACTUAL,
    )
    assert missing.source is UsageSource.MISSING


def test_tool_call_copies_arguments_before_crossing_provider_boundary():
    arguments = {"path": "README.md"}
    call = ToolCall(id="call-1", name="read_file", arguments=arguments)
    arguments["path"] = "changed"

    assert dict(call.arguments) == {"path": "README.md"}
    with pytest.raises(TypeError):
        call.arguments["path"] = "blocked"


def test_generate_model_adapts_complete_only_client():
    class LegacyClient:
        model = "legacy-model"
        last_completion_metadata = {"prompt_tokens": 3, "completion_tokens": 1}

        def complete(self, prompt, max_new_tokens, **kwargs):
            assert prompt == "hello"
            assert max_new_tokens == 8
            return "done"

    result = generate_model(
        LegacyClient(), ModelRequest(prompt="hello", max_output_tokens=8)
    )

    assert result.text == "done"
    assert result.model == "legacy-model"
    assert result.usage.total_tokens == 4
    assert result.usage.source is UsageSource.ACTUAL


def test_generate_model_rejects_invalid_typed_result():
    class InvalidProvider:
        def generate(self, request):
            return "not-a-result"

    with pytest.raises(ProviderProtocolError, match="expected ModelResult"):
        generate_model(
            InvalidProvider(), ModelRequest(prompt="hello", max_output_tokens=8)
        )


def test_builtin_provider_generate_returns_typed_usage_and_stream_fallback():
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield json.dumps({"response": "do", "done": False}).encode()
            yield json.dumps(
                {
                    "response": "ne",
                    "done": True,
                    "prompt_eval_count": 9,
                    "eval_count": 3,
                }
            ).encode()

    client = OllamaModelClient("qwen", "http://localhost:11434", 0, 1, 30)
    request = ModelRequest(prompt="hello", max_output_tokens=8, timeout_seconds=2)

    with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
        events = list(client.stream(request))

    assert urlopen.call_args.kwargs["timeout"] == 2
    assert [event.kind for event in events] == [
        "text_delta",
        "text_delta",
        "completed",
    ]
    assert events[-1].result.text == "done"
    assert events[-1].result.usage == ModelUsage(
        input_tokens=9,
        output_tokens=3,
        total_tokens=12,
        source=UsageSource.ACTUAL,
    )


def test_provider_error_exposes_stable_classification_without_message_parsing():
    error = ProviderError(
        "backend unavailable",
        category="server",
        provider="test",
        retryable=True,
        should_fallback=True,
        status_code=503,
    )

    assert error.to_dict() == {
        "category": "server",
        "provider": "test",
        "retryable": True,
        "should_fallback": True,
        "status_code": 503,
    }


@pytest.mark.parametrize(
    "status,category,retryable,should_fallback",
    [
        (401, "auth", False, False),
        (402, "billing", False, True),
        (404, "model_unavailable", False, True),
        (408, "timeout", True, True),
        (429, "rate_limit", True, True),
        (503, "server", True, True),
    ],
)
def test_http_errors_have_explicit_fallback_classification(
    status, category, retryable, should_fallback
):
    client = OpenAICompatibleModelClient(
        "gpt-test", "https://api.openai.com/v1", "key", 0, 30
    )
    error = urllib.error.HTTPError(
        "https://api.openai.com/v1/responses",
        status,
        "failed",
        {},
        BytesIO(b'{"error":"redacted"}'),
    )

    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(ProviderError) as raised:
            list(client.stream(ModelRequest(prompt="hello", max_output_tokens=8)))

    assert raised.value.category == category
    assert raised.value.retryable is retryable
    assert raised.value.should_fallback is should_fallback


def test_fallback_chain_switches_only_before_stream_output():
    class Provider:
        supports_prompt_cache = False

        def __init__(self, name, error=None, text=""):
            self.model = name
            self.error = error
            self.text = text
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.error is not None:
                raise self.error
            result = ModelResult(
                text=self.text,
                provider=self.model,
                model=self.model,
            )
            yield ModelEvent(kind="text_delta", text=self.text)
            yield ModelEvent(kind="completed", result=result)

    primary = Provider(
        "primary",
        ProviderError(
            "overloaded",
            category="server",
            provider="primary",
            retryable=True,
            should_fallback=True,
            status_code=503,
        ),
    )
    backup = Provider("backup", text="recovered")

    result = stream_model(
        FallbackModelClient([primary, backup]),
        ModelRequest(prompt="hello", max_output_tokens=8),
    )

    assert result.text == "recovered"
    assert primary.calls == backup.calls == 1
    fallback = result.metadata["fallback"]
    assert fallback["used"] is True
    assert fallback["selected_index"] == 1
    assert fallback["selected_provider"] == "backup"
    assert fallback["selected_model"] == "backup"
    assert [row["status"] for row in fallback["attempts"]] == [
        "failed",
        "completed",
    ]
    assert fallback["attempts"][0]["category"] == "server"
    assert fallback["attempts"][0]["status_code"] == 503
    assert all(row["duration_ms"] >= 0 for row in fallback["attempts"])


def test_fallback_chain_does_not_switch_on_non_fallback_error():
    class Provider:
        supports_prompt_cache = False
        model = "primary"

        def __init__(self, error=None):
            self.error = error
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.error:
                raise self.error
            yield ModelEvent(
                kind="completed", result=ModelResult(text="unexpected")
            )

    primary = Provider(
        ProviderError(
            "unauthorized",
            category="auth",
            provider="primary",
            should_fallback=False,
            status_code=401,
        )
    )
    backup = Provider()

    with pytest.raises(ProviderError, match="unauthorized"):
        stream_model(
            FallbackModelClient([primary, backup]),
            ModelRequest(prompt="hello", max_output_tokens=8),
        )

    assert primary.calls == 1
    assert backup.calls == 0


def test_fallback_chain_does_not_mix_partial_streams():
    class PartialProvider:
        supports_prompt_cache = False
        model = "partial"

        def stream(self, request):
            yield ModelEvent(kind="text_delta", text="partial")
            raise ProviderError(
                "connection lost",
                category="connection",
                provider="partial",
                retryable=True,
                should_fallback=True,
            )

    class BackupProvider:
        supports_prompt_cache = False
        model = "backup"

        def __init__(self):
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            yield ModelEvent(
                kind="completed", result=ModelResult(text="backup")
            )

    backup = BackupProvider()
    chain = FallbackModelClient([PartialProvider(), backup])

    with pytest.raises(ProviderError, match="connection lost"):
        list(chain.stream(ModelRequest(prompt="hello", max_output_tokens=8)))

    assert backup.calls == 0


def test_fallback_chain_exhaustion_preserves_attempt_evidence():
    class FailingProvider:
        supports_prompt_cache = False

        def __init__(self, model, status):
            self.model = model
            self.status = status

        def stream(self, request):
            raise ProviderError(
                "unavailable",
                category="server",
                provider=self.model,
                retryable=True,
                should_fallback=True,
                status_code=self.status,
            )
            yield

    chain = FallbackModelClient(
        [FailingProvider("primary", 503), FailingProvider("backup", 502)]
    )

    with pytest.raises(ProviderFallbackExhaustedError) as raised:
        stream_model(chain, ModelRequest(prompt="hello", max_output_tokens=8))

    evidence = raised.value.to_dict()
    assert evidence["fallback_exhausted"] is True
    assert [row["provider"] for row in evidence["fallback_attempts"]] == [
        "primary",
        "backup",
    ]
    assert evidence["should_fallback"] is False


def test_fallback_chain_never_switches_on_cancellation():
    class CancelledProvider:
        supports_prompt_cache = False
        model = "cancelled"

        def stream(self, request):
            raise ProviderCancelledError("cancelled", provider="cancelled")
            yield

    class BackupProvider:
        supports_prompt_cache = False
        model = "backup"

        def __init__(self):
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            yield ModelEvent(
                kind="completed", result=ModelResult(text="unexpected")
            )

    backup = BackupProvider()

    with pytest.raises(ProviderCancelledError):
        stream_model(
            FallbackModelClient([CancelledProvider(), backup]),
            ModelRequest(prompt="hello", max_output_tokens=8),
        )

    assert backup.calls == 0


def test_stream_model_rejects_events_after_terminal_result():
    class InvalidStream:
        def stream(self, request):
            yield ModelEvent(kind="completed", result=ModelResult(text="done"))
            yield ModelEvent(kind="text_delta", text="late")

    with pytest.raises(ProviderProtocolError, match="after completion"):
        stream_model(
            InvalidStream(), ModelRequest(prompt="hello", max_output_tokens=8)
        )


def test_stream_model_rejects_truncated_stream_without_terminal_result():
    class TruncatedStream:
        def stream(self, request):
            yield ModelEvent(kind="text_delta", text="partial")

    with pytest.raises(ProviderProtocolError, match="without a completed"):
        stream_model(
            TruncatedStream(), ModelRequest(prompt="hello", max_output_tokens=8)
        )


def test_openai_stream_normalizes_text_tool_call_usage_and_schema():
    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            events = [
                {"type": "response.output_text.delta", "delta": "working"},
                {
                    "type": "response.output_item.added",
                    "output_index": 1,
                    "item": {
                        "id": "item-1",
                        "call_id": "call-1",
                        "type": "function_call",
                        "name": "read_file",
                        "arguments": "",
                    },
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": "item-1",
                    "delta": '{"path":"README.md"}',
                },
                {
                    "type": "response.completed",
                    "response": {
                        "output": [
                            {
                                "type": "function_call",
                                "id": "item-1",
                                "call_id": "call-1",
                                "name": "read_file",
                                "arguments": '{"path":"README.md"}',
                            }
                        ],
                        "usage": {
                            "input_tokens": 10,
                            "output_tokens": 2,
                            "total_tokens": 12,
                        },
                    },
                },
            ]
            for event in events:
                yield f"data: {json.dumps(event)}\n".encode()
            yield b"data: [DONE]\n"

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse()

    client = OpenAICompatibleModelClient(
        "gpt-test", "https://api.openai.com/v1", "key", 0, 30
    )
    tool = ModelTool(
        name="read_file",
        description="Read a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )
    with patch("urllib.request.urlopen", fake_urlopen):
        result = stream_model(
            client,
            ModelRequest(
                prompt="inspect",
                max_output_tokens=20,
                timeout_seconds=4,
                tools=(tool,),
            ),
        )

    assert captured["timeout"] == 4
    assert captured["body"]["stream"] is True
    assert captured["body"]["tools"][0]["name"] == "read_file"
    assert result.text == "working"
    assert result.tool_calls == (
        ToolCall("call-1", "read_file", {"path": "README.md"}),
    )
    assert result.usage.total_tokens == 12


def test_openai_stream_cancellation_closes_active_response():
    class BlockingResponse:
        headers = {"Content-Type": "text/event-stream"}

        def __init__(self):
            self.started = threading.Event()
            self.closed = threading.Event()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            self.started.set()
            self.closed.wait(timeout=2)
            raise OSError("response closed")
            yield b""

        def close(self):
            self.closed.set()

    response = BlockingResponse()
    token = CancellationToken()
    client = OpenAICompatibleModelClient(
        "gpt-test", "https://api.openai.com/v1", "key", 0, 30
    )
    captured = []

    def consume():
        try:
            list(
                client.stream(
                    ModelRequest(
                        prompt="wait",
                        max_output_tokens=20,
                        cancellation_token=token,
                    )
                )
            )
        except BaseException as exc:
            captured.append(exc)

    with patch("urllib.request.urlopen", return_value=response):
        worker = threading.Thread(target=consume)
        worker.start()
        assert response.started.wait(timeout=1)
        token.cancel()
        worker.join(timeout=1)

    assert worker.is_alive() is False
    assert response.closed.is_set()
    assert len(captured) == 1
    assert isinstance(captured[0], ProviderCancelledError)


def test_anthropic_stream_normalizes_text_tool_call_and_usage():
    class FakeResponse:
        headers = {"Content-Type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            events = [
                {
                    "type": "message_start",
                    "message": {"usage": {"input_tokens": 8}},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "checking"},
                },
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "read_file",
                        "input": {},
                    },
                },
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '{"path":"README.md"}',
                    },
                },
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use"},
                    "usage": {"output_tokens": 3},
                },
                {"type": "message_stop"},
            ]
            for event in events:
                yield f"data: {json.dumps(event)}\n".encode()

    client = AnthropicCompatibleModelClient(
        "claude-test", "https://anthropic.example/v1", "key", 0, 30
    )
    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = stream_model(
            client, ModelRequest(prompt="inspect", max_output_tokens=20)
        )

    assert result.text == "checking"
    assert result.finish_reason == "tool_use"
    assert result.tool_calls == (
        ToolCall("tool-1", "read_file", {"path": "README.md"}),
    )
    assert result.usage.input_tokens == 8
    assert result.usage.output_tokens == 3


def test_agent_loop_accepts_typed_only_provider_and_passes_correlation(tmp_path):
    class TypedOnlyProvider:
        supports_prompt_cache = False
        model = "typed-model"

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            return ModelResult(
                text="<final>typed result</final>",
                usage=ModelUsage(11, 2, 13, source=UsageSource.ACTUAL),
                provider="typed",
                model=self.model,
            )

    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    provider = TypedOnlyProvider()
    agent = RepoAgent(
        model_client=provider,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )

    assert agent.ask("run typed provider") == "typed result"
    request = provider.requests[0]
    assert request.turn_id == agent.current_task_state.run_id
    assert request.request_id == agent.current_task_state.task_id
    assert request.session_id == agent.session["id"]
    assert request.attempt == 1
    assert agent.last_completion_metadata["usage_source"] == "actual"
    assert agent.last_completion_metadata["provider"] == "typed"


def test_agent_loop_records_successful_provider_fallback(tmp_path):
    class Primary:
        supports_prompt_cache = False
        model = "primary"

        def stream(self, request):
            raise ProviderError(
                "overloaded",
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
                provider="backup",
                model="backup",
            )
            yield ModelEvent(kind="text_delta", text=result.text)
            yield ModelEvent(kind="completed", result=result)

    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    agent = RepoAgent(
        model_client=FallbackModelClient([Primary(), Backup()]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )

    assert agent.ask("recover") == "recovered"
    trace = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state.run_id)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    fallback = next(event for event in trace if event["event"] == "model_fallback")
    assert fallback["used"] is True
    assert fallback["selected_provider"] == "backup"
    assert [row["status"] for row in fallback["attempts"]] == [
        "failed",
        "completed",
    ]


def test_agent_loop_executes_normalized_native_tool_call(tmp_path):
    class NativeToolProvider:
        supports_prompt_cache = False
        model = "native-tools"

        def __init__(self):
            self.requests = []

        def stream(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                calls = (
                    ToolCall("call-1", "list_files", {"path": "."}),
                    ToolCall(
                        "call-2",
                        "read_file",
                        {"path": "README.md", "end": 1},
                    ),
                )
                result = ModelResult(
                    tool_calls=calls,
                    finish_reason="tool_calls",
                    provider="native",
                    model=self.model,
                )
                for call in calls:
                    yield ModelEvent(kind="tool_call", tool_call=call)
                yield ModelEvent(kind="completed", result=result)
            else:
                result = ModelResult(
                    text="<final>native done</final>",
                    provider="native",
                    model=self.model,
                )
                yield ModelEvent(kind="text_delta", text=result.text)
                yield ModelEvent(kind="completed", result=result)

    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    provider = NativeToolProvider()
    agent = RepoAgent(
        model_client=provider,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )

    assert agent.ask("inspect natively") == "native done"
    tool_history = [
        item for item in agent.session["history"] if item["role"] == "tool"
    ]
    assert [item["name"] for item in tool_history] == ["list_files", "read_file"]
    assert [item["tool_call_id"] for item in tool_history] == ["call-1", "call-2"]
    read_tool = next(
        tool for tool in provider.requests[0].tools if tool.name == "read_file"
    )
    assert read_tool.parameters["required"] == ["path"]
    assert read_tool.parameters["properties"]["end"]["default"] == 200


def test_agent_loop_parallelizes_safe_reads_but_preserves_serial_barriers(tmp_path):
    class NativeBatchProvider:
        supports_prompt_cache = False
        model = "native-batch"

        def __init__(self):
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            if self.calls == 1:
                calls = (
                    ToolCall("read-0", "read_file", {"path": "file-0.txt"}),
                    ToolCall("read-1", "read_file", {"path": "file-1.txt"}),
                    ToolCall(
                        "write-0",
                        "write_file",
                        {"path": "written.txt", "content": "written\n"},
                    ),
                    ToolCall("read-2", "read_file", {"path": "file-2.txt"}),
                    ToolCall("read-3", "read_file", {"path": "file-3.txt"}),
                )
                result = ModelResult(
                    tool_calls=calls,
                    finish_reason="tool_calls",
                    provider="native",
                    model=self.model,
                )
                for call in calls:
                    yield ModelEvent(kind="tool_call", tool_call=call)
                yield ModelEvent(kind="completed", result=result)
                return
            result = ModelResult(
                text="<final>batch done</final>",
                provider="native",
                model=self.model,
            )
            yield ModelEvent(kind="text_delta", text=result.text)
            yield ModelEvent(kind="completed", result=result)

    for index in range(4):
        (tmp_path / f"file-{index}.txt").write_text(
            f"value-{index}\n", encoding="utf-8"
        )
    provider = NativeBatchProvider()
    agent = RepoAgent(
        model_client=provider,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
        max_parallel_tools=2,
    )
    lock = threading.Lock()
    events = []
    active = 0
    peak = 0

    def read_probe(args, control):
        nonlocal active, peak
        label = args["path"]
        with lock:
            active += 1
            peak = max(peak, active)
            events.append(("start", label))
        try:
            index = int(label.removesuffix(".txt").split("-")[-1])
            time.sleep(0.03 if index % 2 == 0 else 0.01)
            return label
        finally:
            with lock:
                events.append(("end", label))
                active -= 1

    def write_probe(args, control):
        events.append(("start", "write"))
        (tmp_path / args["path"]).write_text(args["content"], encoding="utf-8")
        events.append(("end", "write"))
        return "written"

    agent.tools["read_file"]["run"] = read_probe
    agent.tools["write_file"]["run"] = write_probe

    assert agent.ask("execute a mixed native batch") == "batch done"

    history = [item for item in agent.session["history"] if item["role"] == "tool"]
    assert [item["tool_call_id"] for item in history] == [
        "read-0",
        "read-1",
        "write-0",
        "read-2",
        "read-3",
    ]
    assert peak == 2
    write_start = events.index(("start", "write"))
    write_end = events.index(("end", "write"))
    assert events.index(("end", "file-0.txt")) < write_start
    assert events.index(("end", "file-1.txt")) < write_start
    assert write_end < events.index(("start", "file-2.txt"))
    assert write_end < events.index(("start", "file-3.txt"))
    trace = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    batches = [item for item in trace if item["event"] == "tool_batch_completed"]
    assert [item["mode"] for item in batches] == [
        "parallel",
        "serial",
        "parallel",
    ]
    assert all(
        item["tool_call_ids"] == item["result_call_ids"] for item in batches
    )
    assert [item["scheduling_reason"] for item in batches] == [
        "concurrency_safe_read",
        "mutation_conflict_policy",
        "concurrency_safe_read",
    ]
    assert {item["mutation_conflict_policy"] for item in batches} == {"serial"}


def test_final_text_stream_crosses_chunk_boundaries_without_leaking_protocol(tmp_path):
    class ChunkedProvider:
        supports_prompt_cache = False
        model = "chunked"

        def stream(self, request):
            for text in ("analysis", "<fi", "nal>hel", "lo</fi", "nal>"):
                yield ModelEvent(kind="text_delta", text=text)
            yield ModelEvent(
                kind="completed",
                result=ModelResult(
                    text="analysis<final>hello</final>",
                    provider="chunked",
                    model=self.model,
                ),
            )

    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    agent = RepoAgent(
        model_client=ChunkedProvider(),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )

    assert agent.ask("stream safely") == "hello"
    events = [
        json.loads(line)
        for line in agent.run_store.turn_events_path(agent.current_task_state.run_id)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    streamed = [
        event["payload"]["content"]
        for event in events
        if event["kind"] == "runner.text"
    ]
    assert "".join(streamed) == "hello"
    assert all("analysis" not in text and "final" not in text for text in streamed)


def test_ask_async_cancellation_converges_provider_and_turn(tmp_path):
    class CancellableProvider:
        supports_prompt_cache = False
        model = "cancellable"

        def __init__(self):
            self.started = threading.Event()
            self.stopped = threading.Event()

        def stream(self, request):
            self.started.set()
            try:
                while not request.cancellation_token.cancelled:
                    time.sleep(0.005)
                request.cancellation_token.raise_if_cancelled(provider="cancellable")
                yield ModelEvent(kind="completed", result=ModelResult(text="late"))
            finally:
                self.stopped.set()

    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    provider = CancellableProvider()
    agent = RepoAgent(
        model_client=provider,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )

    async def scenario():
        task = asyncio.create_task(agent.ask_async("cancel me"))
        assert await asyncio.to_thread(provider.started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await agent.aclose()

    asyncio.run(scenario())

    assert provider.stopped.wait(timeout=1)
    events = [
        json.loads(line)
        for line in agent.run_store.turn_events_path(agent.current_task_state.run_id)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [event["kind"] for event in events].count("turn.cancelled") == 1
    assert not any(event["kind"] == "turn.completed" for event in events)
    trace = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state.run_id)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(event["event"] == "model_cancelled" for event in trace)
    assert not any(item["role"] == "tool" for item in agent.session["history"])


def test_agent_loop_persists_structured_provider_failure_evidence(tmp_path):
    class FailingProvider:
        supports_prompt_cache = False
        model = "failing-model"

        def generate(self, request):
            raise ProviderError(
                "service unavailable",
                category="server",
                provider="failing",
                retryable=True,
                should_fallback=True,
                status_code=503,
            )

    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    agent = RepoAgent(
        model_client=FailingProvider(),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )

    with pytest.raises(RuntimeError, match="service unavailable"):
        agent.ask("fail with evidence")

    trace = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state.run_id)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    failed = next(event for event in trace if event["event"] == "model_failed")
    assert failed["category"] == "server"
    assert failed["provider"] == "failing"
    assert failed["retryable"] is True
    assert failed["should_fallback"] is True
    assert failed["status_code"] == 503
