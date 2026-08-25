from types import SimpleNamespace

import pytest

from repoagent.tokenization import (
    CallableTokenCounter,
    TokenCountSource,
    Utf8TokenEstimator,
    resolve_token_counter,
)


def test_callable_counter_validates_results_and_records_provider_source():
    counter = CallableTokenCounter(
        lambda text: len(text.split()),
        counter_identity="provider:test-model",
    )

    assert counter.count("one two three") == 3
    assert counter.metadata() == {
        "identity": "provider:test-model",
        "source": "provider",
    }

    invalid = CallableTokenCounter(lambda text: -1, counter_identity="invalid")
    with pytest.raises(ValueError, match="non-negative integer"):
        invalid.count("text")


def test_utf8_estimator_counts_bytes_and_never_claims_provider_tokens():
    counter = Utf8TokenEstimator(
        provider="anthropic",
        model="example",
        bytes_per_token=4,
    )

    assert counter.count("abcd") == 1
    assert counter.count("你好") == 2
    assert counter.source is TokenCountSource.ESTIMATED
    assert counter.metadata()["source"] == "estimated"
    assert counter.metadata()["bytes_per_token"] == 4.0


def test_resolver_prefers_explicit_counter_then_provider_count_tokens():
    explicit = Utf8TokenEstimator(provider="custom", model="explicit")
    assert resolve_token_counter(SimpleNamespace(token_counter=explicit)) is explicit

    client = SimpleNamespace(
        profile=SimpleNamespace(provider="openai", model="model-x"),
        count_tokens=lambda text: len(text),
    )
    resolved = resolve_token_counter(client)

    assert resolved.count("abc") == 3
    assert resolved.metadata() == {
        "identity": "provider:openai:model-x",
        "source": "provider",
    }


def test_resolver_fallback_is_model_identified_estimate():
    client = SimpleNamespace(
        profile=SimpleNamespace(provider="deepseek", model="deepseek-v4"),
    )

    resolved = resolve_token_counter(client)

    assert isinstance(resolved, Utf8TokenEstimator)
    assert resolved.metadata()["identity"] == "utf8_estimate:deepseek:deepseek-v4"
    assert resolved.metadata()["source"] == "estimated"
