import json
from types import SimpleNamespace
from unittest.mock import patch

from repoagent.evaluation.provider_probe import (
    ProviderProbeError,
    build_probe_approval,
    run_provider_probe,
)
from repoagent.providers import (
    AnthropicCompatibleModelClient,
    ModelEvent,
    ModelResult,
    ModelUsage,
    ToolCall,
    UsageSource,
)


class ProbeProvider:
    def __init__(self, results):
        self.results = list(results)
        self.requests = []
        self.profile = SimpleNamespace(provider="probe", model="resolved-model")

    def stream(self, request):
        self.requests.append(request)
        yield ModelEvent(kind="completed", result=self.results.pop(0))


def _result(arguments, *, model="resolved-model", provider="probe", metadata=None):
    return ModelResult(
        tool_calls=(ToolCall("probe-call", "repoagent_preflight_echo", arguments),),
        finish_reason="tool_use",
        usage=ModelUsage(
            input_tokens=10,
            output_tokens=4,
            total_tokens=14,
            source=UsageSource.ACTUAL,
        ),
        provider=provider,
        model=model,
        metadata=dict(metadata or {}),
    )


def _probe(provider, **overrides):
    options = {
        "requested_model": "resolved-model",
        "tokenizer_metadata": {
            "identity": "test-tokenizer-v1",
            "source": "provider",
        },
        "pricing_source": "test-pricing-snapshot",
    }
    options.update(overrides)
    return run_provider_probe(provider, **options)


def test_provider_probe_verifies_native_tool_model_and_usage():
    provider = ProbeProvider([_result({"value": "ready"})])

    result = _probe(provider)

    assert result.tool_calling_supported is True
    assert result.provider_name == "probe"
    assert result.requested_model == "resolved-model"
    assert result.resolved_model == "resolved-model"
    assert result.usage_fields == (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    )
    assert result.usage_source == "actual"
    assert result.tokenizer_identity == "test-tokenizer-v1"
    assert result.tokenizer_digest.startswith("sha256:")
    assert result.pricing_source == "test-pricing-snapshot"
    assert result.attempts == 1
    assert len(provider.requests) == 1
    assert provider.requests[0].tools[0].name == "repoagent_preflight_echo"
    assert provider.requests[0].timeout_seconds == 60.0
    assert result.max_output_tokens == 128
    assert result.approval_digest.startswith("sha256:")


def test_provider_probe_retries_invalid_arguments_once():
    provider = ProbeProvider([_result({"value": "wrong"}), _result({"value": "ready"})])

    result = _probe(provider)

    assert result.attempts == 2
    assert len(provider.requests) == 2


def test_provider_probe_fails_closed_after_bounded_attempts():
    provider = ProbeProvider(
        [_result({"value": "wrong"}), _result({"value": "still-wrong"})]
    )

    try:
        _probe(provider)
    except ProviderProbeError as exc:
        assert "after 2 attempts" in str(exc)
    else:
        raise AssertionError("invalid provider probe unexpectedly passed")


def test_provider_probe_requires_actual_model_identity():
    provider = ProbeProvider([_result({"value": "ready"}, model="")])

    try:
        _probe(provider)
    except ProviderProbeError as exc:
        assert "model identity" in str(exc)
    else:
        raise AssertionError(
            "provider probe without model identity unexpectedly passed"
        )


def test_provider_probe_rejects_provider_or_model_identity_drift():
    for result in (
        _result({"value": "ready"}, provider="other"),
        _result({"value": "ready"}, model="other"),
    ):
        try:
            _probe(ProbeProvider([result]))
        except ProviderProbeError as exc:
            assert "identity changed" in str(exc)
        else:
            raise AssertionError("provider identity drift unexpectedly passed")


def test_provider_probe_requires_formal_tokenizer_and_pricing_metadata():
    provider = ProbeProvider([_result({"value": "ready"})])
    try:
        _probe(provider, tokenizer_metadata={})
    except ProviderProbeError as exc:
        assert "tokenizer identity" in str(exc)
    else:
        raise AssertionError("missing tokenizer metadata unexpectedly passed")

    provider = ProbeProvider([_result({"value": "ready"})])
    try:
        _probe(provider, pricing_source="")
    except ProviderProbeError as exc:
        assert "pricing source" in str(exc)
    else:
        raise AssertionError("missing pricing source unexpectedly passed")


def test_provider_probe_rejects_non_actual_usage_and_fallback():
    estimated = _result({"value": "ready"})
    estimated = ModelResult(
        tool_calls=estimated.tool_calls,
        finish_reason=estimated.finish_reason,
        usage=ModelUsage(source=UsageSource.ESTIMATED),
        provider=estimated.provider,
        model=estimated.model,
    )
    for result, expected in (
        (estimated, "actual usage"),
        (
            _result({"value": "ready"}, metadata={"fallback": {"used": True}}),
            "fallback",
        ),
    ):
        try:
            _probe(ProbeProvider([result]))
        except ProviderProbeError as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("invalid formal preflight unexpectedly passed")


def test_provider_probe_binds_formal_approval_identity():
    identity = {
        "source_commit": "a" * 40,
        "source_tree_digest": "sha256:" + "b" * 64,
        "source_dirty": False,
        "benchmark_digest": "sha256:" + "c" * 64,
        "runtime_config_digest": "sha256:" + "d" * 64,
        "sandbox_identity": "docker:test",
        "sandbox_isolated": True,
    }
    result = _probe(
        ProbeProvider([_result({"value": "ready"})]),
        approval_identity=identity,
        timeout_seconds=15,
    )

    assert result.approval_identity == identity
    assert result.timeout_seconds == 15
    assert result.approval_digest.startswith("sha256:")


def test_provider_probe_rejects_incomplete_approval_before_model_call():
    provider = ProbeProvider([_result({"value": "ready"})])

    try:
        _probe(provider, approval_identity={"source_commit": "abc"})
    except ProviderProbeError as exc:
        assert "approval identity is missing" in str(exc)
    else:
        raise AssertionError("incomplete approval identity unexpectedly passed")
    assert provider.requests == []


def test_probe_approval_digest_is_canonical_and_budget_sensitive():
    options = {
        "provider": "probe",
        "model": "model",
        "tokenizer_metadata": {"source": "provider", "identity": "tok"},
        "pricing_source": "snapshot",
        "max_attempts": 2,
        "max_output_tokens": 128,
        "timeout_seconds": 60,
    }
    _, first = build_probe_approval(**options)
    _, reordered = build_probe_approval(
        **{**options, "tokenizer_metadata": {"identity": "tok", "source": "provider"}}
    )
    _, changed = build_probe_approval(**{**options, "max_output_tokens": 256})

    assert first == reordered
    assert first != changed


def test_provider_probe_integrates_with_anthropic_compatible_identity():
    class FakeResponse:
        headers = {"Content-Type": "text/event-stream"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            events = [
                {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "probe-call",
                        "name": "repoagent_preflight_echo",
                        "input": {},
                    },
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '{"value":"ready"}',
                    },
                },
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use"},
                    "usage": {"output_tokens": 2},
                },
                {"type": "message_stop"},
            ]
            for event in events:
                yield f"data: {json.dumps(event)}\n".encode()

    client = AnthropicCompatibleModelClient(
        "deepseek-test", "https://deepseek.example/anthropic", "key", 0, 30
    )
    client.profile = SimpleNamespace(provider="deepseek", model="deepseek-test")

    with patch("urllib.request.urlopen", return_value=FakeResponse()):
        result = run_provider_probe(
            client,
            requested_model="deepseek-test",
            tokenizer_metadata={
                "identity": "utf8_estimate:deepseek:deepseek-test",
                "source": "estimated",
            },
            pricing_source="test-pricing",
        )

    assert result.provider_name == "deepseek"
    assert result.resolved_model == "deepseek-test"
    assert result.tool_calling_supported is True
    assert result.usage_source == "actual"
