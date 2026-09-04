import asyncio
from io import BytesIO
import json
import threading
import time
import urllib.error
from unittest.mock import patch

import pytest

from repoagent import RepoAgent, SessionStore, WorkspaceContext
from repoagent.empty_recovery import POST_TOOL_NUDGE, RecoveryLimits
from repoagent.providers import (
    AnthropicCompatibleModelClient,
    CancellationToken,
    FallbackModelClient,
    ModelEvent,
    ModelMessage,
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
from repoagent.providers.clients import _anthropic_messages


def test_model_request_validates_budget_timeout_and_attempt():
    with pytest.raises(ValueError, match="positive"):
        ModelRequest(prompt="hello", max_output_tokens=0)
    with pytest.raises(ValueError, match="timeout_seconds"):
        ModelRequest(prompt="hello", max_output_tokens=1, timeout_seconds=0)
    with pytest.raises(ValueError, match="attempt"):
        ModelRequest(prompt="hello", max_output_tokens=1, attempt=0)


def test_model_message_requires_matched_tool_protocol_fields():
    call = ToolCall("call-1", "read_file", {"path": "README.md"})
    assert ModelMessage(role="assistant", tool_calls=(call,)).tool_calls == (call,)
    assert (
        ModelMessage(
            role="tool", content="done", tool_call_id="call-1", name="read_file"
        ).tool_call_id
        == "call-1"
    )
    with pytest.raises(ValueError, match="call id and name"):
        ModelMessage(role="tool", content="done")


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
        "should_compress": False,
        "status_code": 503,
    }


def test_http_context_overflow_compresses_without_fallback():
    client = OpenAICompatibleModelClient(
        "gpt-test", "https://api.openai.com/v1", "key", 0, 30
    )
    error = urllib.error.HTTPError(
        "https://api.openai.com/v1/responses",
        400,
        "failed",
        {},
        BytesIO(b'{"error":"maximum context length exceeded"}'),
    )

    with patch("urllib.request.urlopen", side_effect=error):
        with pytest.raises(ProviderError) as raised:
            list(client.stream(ModelRequest(prompt="hello", max_output_tokens=8)))

    assert raised.value.category == "context_overflow"
    assert raised.value.retryable is False
    assert raised.value.should_fallback is False
    assert raised.value.should_compress is True


def test_fallback_chain_never_switches_on_context_overflow():
    class Overflow:
        model = "primary"

        def stream(self, request):
            raise ProviderError(
                "maximum context length exceeded",
                category="context_overflow",
                provider="primary",
                should_compress=True,
                status_code=400,
            )
            yield

    class Backup:
        model = "backup"

        def __init__(self):
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            yield ModelEvent(
                kind="completed",
                result=ModelResult(text="must not run", provider="backup"),
            )

    backup = Backup()
    client = FallbackModelClient((Overflow(), backup))

    with pytest.raises(ProviderError, match="maximum context") as raised:
        stream_model(client, ModelRequest(prompt="hello", max_output_tokens=8))

    assert raised.value.should_compress is True
    assert backup.calls == 0


def test_fallback_exhaustion_preserves_final_context_overflow_classification():
    class Failing:
        def __init__(self, name, error):
            self.model = name
            self.error = error

        def stream(self, request):
            raise self.error
            yield

    client = FallbackModelClient(
        (
            Failing(
                "primary",
                ProviderError(
                    "server unavailable",
                    category="server",
                    provider="primary",
                    should_fallback=True,
                ),
            ),
            Failing(
                "backup",
                ProviderError(
                    "maximum context length exceeded",
                    category="context_overflow",
                    provider="backup",
                    should_compress=True,
                ),
            ),
        )
    )

    with pytest.raises(ProviderFallbackExhaustedError) as raised:
        stream_model(client, ModelRequest(prompt="hello", max_output_tokens=8))

    assert raised.value.category == "context_overflow"
    assert raised.value.should_fallback is False
    assert raised.value.should_compress is True


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
            yield ModelEvent(kind="completed", result=ModelResult(text="unexpected"))

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
            yield ModelEvent(kind="completed", result=ModelResult(text="backup"))

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
            yield ModelEvent(kind="completed", result=ModelResult(text="unexpected"))

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
        stream_model(InvalidStream(), ModelRequest(prompt="hello", max_output_tokens=8))


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
                messages=(
                    ModelMessage(role="user", content="inspect"),
                    ModelMessage(
                        role="assistant",
                        tool_calls=(
                            ToolCall("prior-call", "read_file", {"path": "prior.txt"}),
                        ),
                    ),
                    ModelMessage(
                        role="tool",
                        content="prior contents",
                        tool_call_id="prior-call",
                        name="read_file",
                    ),
                    ModelMessage(role="user", content="continue"),
                ),
            ),
        )

    assert captured["timeout"] == 4
    assert captured["body"]["stream"] is True
    assert captured["body"]["tools"][0]["name"] == "read_file"
    assert captured["body"]["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "inspect"}]},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "prior-call",
            "name": "read_file",
            "arguments": '{"path":"prior.txt"}',
        },
        {
            "type": "function_call_output",
            "call_id": "prior-call",
            "output": "prior contents",
        },
        {
            "role": "user",
            "content": [{"type": "input_text", "text": "continue"}],
        },
    ]
    assert result.text == "working"
    assert result.tool_calls == (
        ToolCall("call-1", "read_file", {"path": "README.md"}),
    )
    assert result.usage.total_tokens == 12


def test_openai_non_stream_response_normalizes_reasoning_item():
    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "output": [
                        {
                            "type": "reasoning",
                            "id": "rs_1",
                            "status": "completed",
                            "summary": [
                                {
                                    "type": "summary_text",
                                    "text": "Inspect the repository first.",
                                }
                            ],
                            "encrypted_content": "sealed-reasoning",
                        }
                    ],
                    "usage": {
                        "input_tokens": 4,
                        "output_tokens": 2,
                        "total_tokens": 6,
                    },
                }
            ).encode()

    client = OpenAICompatibleModelClient(
        "gpt-test", "https://api.openai.com/v1", "key", 0, 30
    )
    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = stream_model(
            client, ModelRequest(prompt="inspect", max_output_tokens=20)
        )

    assert result.text == ""
    assert result.reasoning_content == "Inspect the repository first."
    assert [dict(block) for block in result.thinking_blocks] == [
        {
            "type": "reasoning",
            "id": "rs_1",
            "status": "completed",
            "summary": [
                {
                    "type": "summary_text",
                    "text": "Inspect the repository first.",
                }
            ],
            "encrypted_content": "sealed-reasoning",
        }
    ]


def test_openai_stream_normalizes_and_replays_encrypted_reasoning_item():
    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            response = {
                "output": [
                    {
                        "type": "reasoning",
                        "id": "rs_new",
                        "status": "completed",
                        "summary": [
                            {"type": "summary_text", "text": "Continue carefully."}
                        ],
                        "encrypted_content": "new-sealed-reasoning",
                    }
                ],
                "usage": {
                    "input_tokens": 5,
                    "output_tokens": 2,
                    "total_tokens": 7,
                },
            }
            events = [
                {
                    "type": "response.reasoning_summary_text.delta",
                    "delta": "Continue carefully.",
                },
                {"type": "response.completed", "response": response},
            ]
            for event in events:
                yield f"data: {json.dumps(event)}\n".encode()

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        return FakeResponse()

    prior_reasoning = {
        "type": "reasoning",
        "id": "rs_prior",
        "status": "completed",
        "summary": [{"type": "summary_text", "text": "Inspect first."}],
        "encrypted_content": "prior-sealed-reasoning",
    }
    request = ModelRequest(
        prompt="continue",
        max_output_tokens=20,
        messages=(
            ModelMessage(role="user", content="inspect"),
            ModelMessage(
                role="assistant",
                reasoning_content="Inspect first.",
                thinking_blocks=(prior_reasoning,),
            ),
            ModelMessage(role="user", content="continue"),
        ),
    )
    client = OpenAICompatibleModelClient(
        "gpt-test", "https://api.openai.com/v1", "key", 0, 30
    )
    with patch("urllib.request.urlopen", fake_urlopen):
        result = stream_model(client, request)

    assert result.reasoning_content == "Continue carefully."
    assert dict(result.thinking_blocks[0])["encrypted_content"] == (
        "new-sealed-reasoning"
    )
    assert captured["body"]["include"] == ["reasoning.encrypted_content"]
    assert captured["body"]["input"][1] == prior_reasoning


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


def test_anthropic_stream_projects_native_assistant_and_tool_result_messages():
    captured = {}

    class FakeResponse:
        headers = {"Content-Type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            events = [
                {"type": "message_start", "message": {"usage": {"input_tokens": 9}}},
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "done"},
                },
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
                {"type": "message_stop"},
            ]
            for event in events:
                yield f"data: {json.dumps(event)}\n".encode()

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        return FakeResponse()

    call = ToolCall("tool-1", "read_file", {"path": "README.md"})
    request = ModelRequest(
        prompt="compatibility prompt",
        max_output_tokens=20,
        messages=(
            ModelMessage(role="user", content="inspect"),
            ModelMessage(
                role="assistant",
                content="checking",
                tool_calls=(call,),
                reasoning_content="inspect first",
                thinking_blocks=(
                    {
                        "type": "thinking",
                        "thinking": "inspect first",
                        "signature": "sig",
                    },
                ),
            ),
            ModelMessage(
                role="tool",
                content="file contents",
                tool_call_id="tool-1",
                name="read_file",
            ),
            ModelMessage(role="user", content="summarize"),
        ),
    )
    client = AnthropicCompatibleModelClient(
        "claude-test", "https://anthropic.example/v1", "key", 0, 30
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        result = stream_model(client, request)

    assert result.text == "done"
    assert captured["body"]["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "inspect"}]},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "thinking",
                    "thinking": "inspect first",
                    "signature": "sig",
                },
                {"type": "text", "text": "checking"},
                {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "read_file",
                    "input": {"path": "README.md"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": "file contents",
                },
                {"type": "text", "text": "summarize"},
            ],
        },
    ]


def test_anthropic_projection_omits_messages_without_wire_blocks():
    request = ModelRequest(
        prompt="inspect",
        max_output_tokens=20,
        messages=(
            ModelMessage(role="user", content="inspect"),
            ModelMessage(role="assistant", reasoning_content="display only"),
            ModelMessage(role="user", content="continue"),
        ),
    )

    assert _anthropic_messages(request) == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "inspect"},
                {"type": "text", "text": "continue"},
            ],
        }
    ]


def test_anthropic_stream_preserves_structured_thinking_for_recovery():
    class FakeResponse:
        headers = {"Content-Type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            events = [
                {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "thinking", "thinking": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "inspect first"},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "signature_delta", "signature": "sig"},
                },
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
                {"type": "message_stop"},
            ]
            for event in events:
                yield f"data: {json.dumps(event)}\n".encode()

    client = AnthropicCompatibleModelClient(
        "deepseek-test", "https://deepseek.example/anthropic", "key", 0, 30
    )
    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = stream_model(
            client, ModelRequest(prompt="inspect", max_output_tokens=20)
        )

    assert result.text == ""
    assert result.reasoning_content == "inspect first"
    assert [dict(block) for block in result.thinking_blocks] == [
        {"type": "thinking", "thinking": "inspect first", "signature": "sig"}
    ]


def test_anthropic_stream_repairs_malformed_tool_argument_json():
    class FakeResponse:
        headers = {"Content-Type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            events = [
                {"type": "message_start", "message": {"usage": {"input_tokens": 4}}},
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tool-repaired",
                        "name": "read_file",
                        "input": {},
                    },
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '{"path":"README.md",}',
                    },
                },
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
                {"type": "message_stop"},
            ]
            for event in events:
                yield f"data: {json.dumps(event)}\n".encode()

    client = AnthropicCompatibleModelClient(
        "deepseek-test", "https://deepseek.example/anthropic", "key", 0, 30
    )
    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = stream_model(
            client, ModelRequest(prompt="inspect", max_output_tokens=20)
        )

    assert result.tool_calls == (
        ToolCall("tool-repaired", "read_file", {"path": "README.md"}),
    )


def test_anthropic_stream_preserves_repaired_non_object_tool_arguments():
    class FakeResponse:
        headers = {"Content-Type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            events = [
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tool-list",
                        "name": "read_file",
                        "input": {},
                    },
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '["README.md",]',
                    },
                },
                {"type": "message_stop"},
            ]
            for event in events:
                yield f"data: {json.dumps(event)}\n".encode()

    client = AnthropicCompatibleModelClient(
        "deepseek-test", "https://deepseek.example/anthropic", "key", 0, 30
    )
    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = stream_model(
            client, ModelRequest(prompt="inspect", max_output_tokens=20)
        )

    assert result.tool_calls == (
        ToolCall(
            "tool-list",
            "read_file",
            {"_raw_arguments": '["README.md",]'},
        ),
    )


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
    tool_history = [item for item in agent.session["history"] if item["role"] == "tool"]
    assert [item["name"] for item in tool_history] == ["list_files", "read_file"]
    assert [item["tool_call_id"] for item in tool_history] == ["call-1", "call-2"]
    assert [message.role for message in provider.requests[1].messages] == [
        "user",
        "assistant",
        "tool",
        "tool",
    ]
    assistant_message = provider.requests[1].messages[1]
    assert [call.id for call in assistant_message.tool_calls] == ["call-1", "call-2"]
    assert provider.requests[1].messages[2].tool_call_id == "call-1"
    assert (
        "BEGIN UNTRUSTED TOOL RESULT list_files"
        in provider.requests[1].messages[2].content
    )
    assert provider.requests[1].messages[3].tool_call_id == "call-2"
    read_tool = next(
        tool for tool in provider.requests[0].tools if tool.name == "read_file"
    )
    assert read_tool.parameters["required"] == ["path"]
    assert read_tool.parameters["properties"]["end"]["default"] == 200


def test_native_tool_protocol_survives_next_turn_and_session_reload(tmp_path):
    class CrossTurnProvider:
        supports_prompt_cache = False
        supports_structured_messages = True
        model = "cross-turn-tools"

        def __init__(self, responses):
            self.responses = list(responses)
            self.requests = []

        def stream(self, request):
            self.requests.append(request)
            result = self.responses.pop(0)
            for call in result.tool_calls:
                yield ModelEvent(kind="tool_call", tool_call=call)
            yield ModelEvent(kind="completed", result=result)

    (tmp_path / "README.md").write_text("evidence\n", encoding="utf-8")
    first_provider = CrossTurnProvider(
        [
            ModelResult(
                tool_calls=(ToolCall("read-1", "read_file", {"path": "README.md"}),),
                reasoning_content="inspect first",
                thinking_blocks=(
                    {
                        "type": "thinking",
                        "thinking": "inspect first",
                        "signature": "sig",
                    },
                ),
                finish_reason="tool_calls",
                provider="native",
                model="cross-turn-tools",
            ),
            ModelResult(
                text="<final>First turn complete.</final>",
                provider="native",
                model="cross-turn-tools",
            ),
            ModelResult(
                text="<final>Second turn complete.</final>",
                provider="native",
                model="cross-turn-tools",
            ),
        ]
    )
    store = SessionStore(tmp_path / ".repoagent" / "sessions")
    agent = RepoAgent(
        model_client=first_provider,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=store,
        approval_policy="auto",
    )

    assert agent.ask("Read the repository") == "First turn complete."
    assert agent.ask("Use that result") == "Second turn complete."
    second_turn = first_provider.requests[2]
    assert [message.role for message in second_turn.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert second_turn.messages[1].tool_calls[0].id == "read-1"
    assert second_turn.messages[1].reasoning_content == "inspect first"
    assert dict(second_turn.messages[1].thinking_blocks[0]) == {
        "type": "thinking",
        "thinking": "inspect first",
        "signature": "sig",
    }
    assert second_turn.messages[2].tool_call_id == "read-1"
    assert "BEGIN UNTRUSTED TOOL RESULT read_file" in second_turn.messages[2].content
    assert "[tool:read_file]" not in second_turn.prompt
    assert agent.last_prompt_metadata["history"]["mode"] == (
        "structured-provider-messages"
    )
    assert agent.last_prompt_metadata["history"]["token_count"] > 0
    assert (
        agent.last_prompt_metadata["provider_message_tokens"]
        == (agent.last_prompt_metadata["prompt_tokens"])
    )

    resumed_provider = CrossTurnProvider(
        [
            ModelResult(
                text="<final>Resumed turn complete.</final>",
                provider="native",
                model="cross-turn-tools",
            )
        ]
    )
    resumed = RepoAgent.from_session(
        model_client=resumed_provider,
        workspace=agent.workspace,
        session_store=store,
        session_id=agent.session["id"],
        approval_policy="auto",
    )

    assert resumed.ask("Continue after restart") == "Resumed turn complete."
    resumed_request = resumed_provider.requests[0]
    assert [message.role for message in resumed_request.messages[:3]] == [
        "user",
        "assistant",
        "tool",
    ]
    assert resumed_request.messages[1].tool_calls[0].id == "read-1"
    assert resumed_request.messages[1].reasoning_content == "inspect first"
    assert dict(resumed_request.messages[1].thinking_blocks[0]) == {
        "type": "thinking",
        "thinking": "inspect first",
        "signature": "sig",
    }
    assert resumed_request.messages[2].tool_call_id == "read-1"
    assert resumed_request.messages[-1].role == "user"


def test_agent_loop_prefills_thinking_only_without_persisting_scaffolding(tmp_path):
    class ThinkingProvider:
        supports_prompt_cache = False
        model = "thinking"

        def __init__(self):
            self.requests = []

        def stream(self, request):
            self.requests.append(request)
            result = (
                ModelResult(
                    reasoning_content="inspect the repository first",
                    provider="thinking",
                    model=self.model,
                )
                if len(self.requests) == 1
                else ModelResult(
                    text="<final>Recovered.</final>",
                    provider="thinking",
                    model=self.model,
                )
            )
            yield ModelEvent(kind="completed", result=result)

    provider = ThinkingProvider()
    agent = RepoAgent(
        model_client=provider,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )

    assert agent.ask("think, then answer") == "Recovered."
    assert [message.role for message in provider.requests[1].messages] == [
        "user",
        "assistant",
    ]
    assert provider.requests[1].messages[-1].content == ""
    assert (
        provider.requests[1].messages[-1].reasoning_content
        == "inspect the repository first"
    )
    assert all(
        "inspect the repository first" not in item.get("content", "")
        for item in agent.session["history"]
    )


def test_openai_reasoning_only_response_recovers_with_opaque_item_replay(tmp_path):
    requests = []

    class FakeResponse:
        headers = {"Content-Type": "application/json"}

        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

        def close(self):
            pass

    reasoning_item = {
        "type": "reasoning",
        "id": "rs_recovery",
        "status": "completed",
        "summary": [{"type": "summary_text", "text": "Inspect first."}],
        "encrypted_content": "sealed-recovery-reasoning",
    }
    responses = iter(
        [
            {"output": [reasoning_item], "usage": {"input_tokens": 4}},
            {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "<final>Recovered.</final>",
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 6, "output_tokens": 2},
            },
        ]
    )

    def fake_urlopen(request, timeout):
        requests.append(json.loads(request.data))
        return FakeResponse(next(responses))

    client = OpenAICompatibleModelClient(
        "gpt-test", "https://api.openai.com/v1", "key", 0, 30
    )
    agent = RepoAgent(
        model_client=client,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )

    with patch("urllib.request.urlopen", fake_urlopen):
        assert agent.ask("think, then answer") == "Recovered."

    assert len(requests) == 2
    assert reasoning_item in requests[1]["input"]
    assert requests[1]["include"] == ["reasoning.encrypted_content"]
    assert all(
        item.get("reasoning_content") != "Inspect first."
        and item.get("thinking_blocks") != [reasoning_item]
        for item in agent.session["history"]
    )


def test_agent_loop_nudges_once_after_post_tool_empty_response(tmp_path):
    class PostToolEmptyProvider:
        supports_prompt_cache = False
        model = "post-tool-empty"

        def __init__(self):
            self.requests = []

        def stream(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                result = ModelResult(
                    tool_calls=(
                        ToolCall("read-1", "read_file", {"path": "README.md"}),
                    ),
                    finish_reason="tool_calls",
                    provider="post-tool-empty",
                    model=self.model,
                )
            elif len(self.requests) == 2:
                result = ModelResult(provider="post-tool-empty", model=self.model)
            else:
                result = ModelResult(
                    text="<final>Used the result.</final>",
                    provider="post-tool-empty",
                    model=self.model,
                )
            yield ModelEvent(kind="completed", result=result)

    (tmp_path / "README.md").write_text("evidence\n", encoding="utf-8")
    provider = PostToolEmptyProvider()
    agent = RepoAgent(
        model_client=provider,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )

    assert agent.ask("read, then answer") == "Used the result."
    assert [message.role for message in provider.requests[2].messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert provider.requests[2].messages[-1].content == POST_TOOL_NUDGE
    persisted = [item.get("content", "") for item in agent.session["history"]]
    assert POST_TOOL_NUDGE not in persisted
    assert "(empty)" not in persisted


def test_agent_loop_bounds_persistent_empty_recovery_per_turn(tmp_path):
    class EmptyProvider:
        supports_prompt_cache = False
        model = "empty"

        def __init__(self):
            self.requests = []

        def stream(self, request):
            self.requests.append(request)
            yield ModelEvent(
                kind="completed", result=ModelResult(provider="empty", model=self.model)
            )

    provider = EmptyProvider()
    agent = RepoAgent(
        model_client=provider,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
        max_steps=1,
    )

    assert agent.ask("answer") == "I have no response to give."
    assert len(provider.requests) == 1 + RecoveryLimits().empty_content_max_retries
    assert agent.current_task_state.stop_reason == "final_answer_returned"
    assert all(
        "Runtime notice" not in item.get("content", "")
        for item in agent.session["history"]
    )


def test_agent_loop_can_disable_empty_recovery(tmp_path):
    class EmptyProvider:
        supports_prompt_cache = False
        model = "empty"

        def __init__(self):
            self.calls = 0

        def stream(self, request):
            self.calls += 1
            yield ModelEvent(
                kind="completed", result=ModelResult(provider="empty", model=self.model)
            )

    provider = EmptyProvider()
    agent = RepoAgent(
        model_client=provider,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
        empty_recovery=RecoveryLimits(enabled=False),
    )

    assert agent.ask("answer") == "I have no response to give."
    assert provider.calls == 1


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
    assert all(item["tool_call_ids"] == item["result_call_ids"] for item in batches)
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
