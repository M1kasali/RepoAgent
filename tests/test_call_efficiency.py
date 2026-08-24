import json
import os
from unittest.mock import patch

import pytest

from repoagent import (
    CallEfficiencyEntry,
    CallEfficiencySummary,
    FallbackModelClient,
    ModelPricing,
    ModelProfile,
    ModelResult,
    ModelUsage,
    RepoAgent,
    SessionStore,
    UsageSource,
    WorkspaceContext,
    price_usage,
)
from repoagent.cli import _resolve_model_profile, build_arg_parser
from repoagent.providers import InputTokenSemantics, ProviderError


def _pricing(**overrides):
    values = {
        "input_per_1m_usd": 2.0,
        "output_per_1m_usd": 10.0,
        "source": "test snapshot",
    }
    values.update(overrides)
    return ModelPricing(**values)


def _profile(provider, model, pricing=None):
    return ModelProfile(
        name=provider,
        provider=provider,
        protocol="openai",
        model=model,
        base_url=f"https://{provider}.example/v1",
        pricing=pricing,
    )


def _entry(*, status="completed", usage=None, pricing=None):
    return CallEfficiencyEntry(
        provider_call_id="request:1:0",
        turn_id="turn",
        request_id="request",
        session_id="session",
        agent_attempt=1,
        provider_attempt=0,
        provider="provider",
        model="model",
        status=status,
        duration_ms=12,
        usage=usage or ModelUsage(),
        pricing=pricing,
    )


def _agent(tmp_path, provider):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return RepoAgent(
        model_client=provider,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )


def _jsonl(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"input_per_1m_usd": -1}, "input_per_1m_usd"),
        ({"output_per_1m_usd": float("nan")}, "output_per_1m_usd"),
        ({"source": ""}, "source"),
        ({"currency": "CNY"}, "USD"),
    ],
)
def test_model_pricing_validates_and_has_stable_identity(overrides, message):
    with pytest.raises(ValueError, match=message):
        _pricing(**overrides)

    first = _pricing()
    second = _pricing()
    assert first.pricing_id == second.pricing_id
    assert first.to_dict()["pricing_id"].startswith("sha256:")
    assert _pricing(source=" snapshot ").source == "snapshot"


def test_price_usage_distinguishes_priced_and_incomplete_evidence():
    pricing = _pricing()
    actual = ModelUsage(100, 20, 120, source=UsageSource.ACTUAL)
    estimated = ModelUsage(50, 5, 55, source=UsageSource.ESTIMATED)
    cached = ModelUsage(
        100,
        20,
        120,
        cache_read_tokens=40,
        source=UsageSource.ACTUAL,
    )

    assert price_usage(actual, pricing) == (0.0004, "priced")
    assert price_usage(estimated, pricing) == (0.00015, "priced")
    assert price_usage(ModelUsage(), pricing) == (None, "incomplete_usage")
    assert price_usage(actual, None) == (None, "unpriced")
    assert price_usage(cached, pricing) == (None, "ambiguous_cache_usage")


def test_cache_pricing_normalizes_fresh_and_total_input_semantics():
    pricing = _pricing(
        cache_read_per_1m_usd=0.2,
        cache_write_per_1m_usd=2.5,
    )
    fresh = ModelUsage(
        60,
        20,
        120,
        cache_read_tokens=40,
        source=UsageSource.ACTUAL,
        input_token_semantics=InputTokenSemantics.FRESH,
    )
    total = ModelUsage(
        100,
        20,
        120,
        cache_read_tokens=40,
        source=UsageSource.ACTUAL,
        input_token_semantics=InputTokenSemantics.TOTAL,
    )
    invalid = ModelUsage(
        10,
        2,
        12,
        cache_read_tokens=11,
        source=UsageSource.ACTUAL,
        input_token_semantics=InputTokenSemantics.TOTAL,
    )

    assert price_usage(fresh, pricing) == (0.000328, "priced")
    assert price_usage(total, pricing) == (0.000328, "priced")
    assert price_usage(invalid, pricing) == (None, "invalid_cache_usage")


def test_cache_pricing_requires_each_used_cache_rate():
    read_usage = ModelUsage(
        60,
        20,
        120,
        cache_read_tokens=40,
        source=UsageSource.ACTUAL,
        input_token_semantics=InputTokenSemantics.FRESH,
    )
    assert price_usage(read_usage, _pricing()) == (
        None,
        "cache_pricing_required",
    )


def test_call_summary_only_claims_unit_cost_for_complete_success():
    priced = _entry(
        usage=ModelUsage(100, 20, 120, source=UsageSource.ACTUAL),
        pricing=_pricing(),
    )
    complete = CallEfficiencySummary.from_entries(
        [priced], turn_succeeded=True
    )
    incomplete = CallEfficiencySummary.from_entries(
        [priced, _entry(status="failed", pricing=_pricing())],
        turn_succeeded=True,
    )
    cancelled = CallEfficiencySummary.from_entries(
        [_entry(status="cancelled")], turn_succeeded=False
    )

    assert complete.cost_complete is True
    assert complete.cost_per_successful_turn_usd == 0.0004
    assert incomplete.partial_estimated_cost_usd == 0.0004
    assert incomplete.cost_complete is False
    assert incomplete.cost_per_successful_turn_usd is None
    assert cancelled.cancelled_call_count == 1


def test_cli_attaches_only_explicit_complete_pricing_snapshots():
    args = build_arg_parser().parse_args(
        [
            "--profile",
            "openai",
            "--input-cost-per-1m-usd",
            "2",
            "--output-cost-per-1m-usd",
            "10",
            "--pricing-source",
            "vendor page 2026-08-24",
            "--cache-read-cost-per-1m-usd",
            "0.2",
            "--cache-write-cost-per-1m-usd",
            "2.5",
        ]
    )
    profile = _resolve_model_profile(args)

    assert profile.pricing == _pricing(
        source="vendor page 2026-08-24",
        cache_read_per_1m_usd=0.2,
        cache_write_per_1m_usd=2.5,
    )
    assert profile.to_dict()["pricing"]["source"] == "vendor page 2026-08-24"

    incomplete = build_arg_parser().parse_args(
        ["--profile", "openai", "--input-cost-per-1m-usd", "2"]
    )
    with pytest.raises(ValueError, match="configured together"):
        _resolve_model_profile(incomplete)


def test_cli_resolves_provider_specific_pricing_environment():
    args = build_arg_parser().parse_args(["--profile", "deepseek"])
    env = {
        "REPOAGENT_DEEPSEEK_INPUT_COST_PER_1M_USD": "0.4",
        "REPOAGENT_DEEPSEEK_OUTPUT_COST_PER_1M_USD": "1.2",
        "REPOAGENT_DEEPSEEK_PRICING_SOURCE": "internal contract",
    }
    with patch.dict(os.environ, env, clear=True):
        profile = _resolve_model_profile(args)

    assert profile.pricing == ModelPricing(0.4, 1.2, "internal contract")


def test_agent_persists_priced_call_ledger_trace_and_report(tmp_path):
    class Provider:
        supports_prompt_cache = False
        model = "metered-model"
        profile = _profile("metered", model, _pricing())

        def generate(self, request):
            return ModelResult(
                text="<final>done</final>",
                usage=ModelUsage(100, 20, 120, source=UsageSource.ACTUAL),
                provider="metered",
                model=self.model,
                latency_ms=9,
            )

    agent = _agent(tmp_path, Provider())
    assert agent.ask("finish") == "done"
    run_id = agent.current_task_state.run_id
    calls = _jsonl(agent.run_store.call_ledger_path(run_id))
    trace = _jsonl(agent.run_store.trace_path(run_id))
    report = json.loads(
        agent.run_store.report_path(run_id).read_text(encoding="utf-8")
    )
    turns = _jsonl(agent.run_store.turn_events_path(run_id))

    assert len(calls) == 1
    assert calls[0]["provider_call_id"].endswith(":1:0")
    assert calls[0]["estimated_cost_usd"] == 0.0004
    assert calls[0]["pricing"]["pricing_id"] == _pricing().pricing_id
    accounted = [row for row in trace if row["event"] == "model_call_accounted"]
    assert accounted[0]["provider_call_id"] == calls[0]["provider_call_id"]
    assert report["call_efficiency"]["cost_complete"] is True
    assert report["call_efficiency"]["cost_per_successful_turn_usd"] == 0.0004
    terminal = next(row for row in turns if row["kind"] == "turn.completed")
    assert terminal["payload"]["call_efficiency"] == report["call_efficiency"]


def test_fallback_preserves_partial_cost_without_claiming_unit_cost(tmp_path):
    class Primary:
        supports_prompt_cache = False
        model = "primary-model"
        profile = _profile("primary", model, _pricing())

        def generate(self, request):
            raise ProviderError(
                "unavailable",
                category="overloaded",
                provider="primary",
                retryable=True,
                should_fallback=True,
            )

    class Backup:
        supports_prompt_cache = False
        model = "backup-model"
        profile = _profile("backup", model, _pricing())

        def generate(self, request):
            return ModelResult(
                text="<final>recovered</final>",
                usage=ModelUsage(100, 20, 120, source=UsageSource.ACTUAL),
                provider="backup",
                model=self.model,
            )

    agent = _agent(tmp_path, FallbackModelClient([Primary(), Backup()]))
    assert agent.ask("finish") == "recovered"
    calls = _jsonl(
        agent.run_store.call_ledger_path(agent.current_task_state.run_id)
    )

    assert [row["status"] for row in calls] == ["failed", "completed"]
    assert [row["cost_status"] for row in calls] == [
        "incomplete_usage",
        "priced",
    ]
    summary = agent.last_call_efficiency_summary
    assert summary["partial_estimated_cost_usd"] == 0.0004
    assert summary["cost_complete"] is False
    assert summary["cost_per_successful_turn_usd"] is None


def test_fallback_pricing_matches_provider_before_shared_model_name(tmp_path):
    class Primary:
        supports_prompt_cache = False
        model = "shared-model"
        profile = _profile("primary", model, _pricing(output_per_1m_usd=1))

        def generate(self, request):
            raise ProviderError(
                "unavailable",
                category="overloaded",
                provider="primary",
                should_fallback=True,
            )

    class Backup:
        supports_prompt_cache = False
        model = "shared-model"
        profile = _profile("backup", model, _pricing(output_per_1m_usd=20))

        def generate(self, request):
            return ModelResult(
                text="<final>recovered</final>",
                usage=ModelUsage(0, 10, 10, source=UsageSource.ACTUAL),
                provider="backup",
                model=self.model,
            )

    agent = _agent(tmp_path, FallbackModelClient([Primary(), Backup()]))
    assert agent.ask("finish") == "recovered"
    calls = _jsonl(
        agent.run_store.call_ledger_path(agent.current_task_state.run_id)
    )

    assert calls[1]["pricing"]["output_per_1m_usd"] == 20
    assert calls[1]["estimated_cost_usd"] == 0.0002


def test_invalid_fallback_metadata_does_not_corrupt_call_accounting(tmp_path):
    class Provider:
        supports_prompt_cache = False
        model = "custom-model"

        def generate(self, request):
            return ModelResult(
                text="<final>done</final>",
                usage=ModelUsage(10, 2, 12, source=UsageSource.ACTUAL),
                provider="custom",
                model=self.model,
                metadata={
                    "fallback": {
                        "attempts": [{"status": "broken", "index": "bad"}]
                    }
                },
            )

    agent = _agent(tmp_path, Provider())
    assert agent.ask("finish") == "done"
    calls = _jsonl(
        agent.run_store.call_ledger_path(agent.current_task_state.run_id)
    )
    assert len(calls) == 1
    assert calls[0]["status"] == "completed"


def test_summary_keeps_compaction_calls_separate():
    usage = ModelUsage(10, 2, 12, source=UsageSource.ACTUAL)
    agent_call = _entry(usage=usage, pricing=_pricing())
    compaction_call = CallEfficiencyEntry(
        **{
            **agent_call.__dict__,
            "provider_call_id": "request:1:1",
            "provider_attempt": 1,
            "call_kind": "compaction",
        }
    )

    summary = CallEfficiencySummary.from_entries(
        [agent_call, compaction_call], turn_succeeded=True
    )
    assert summary.call_kind_counts == {"agent": 1, "compaction": 1}


def test_failed_turn_persists_incomplete_call_summary(tmp_path):
    class Provider:
        supports_prompt_cache = False
        model = "failing-model"
        profile = _profile("failing", model, _pricing())

        def generate(self, request):
            raise ProviderError(
                "unavailable",
                category="server",
                provider="failing",
            )

    agent = _agent(tmp_path, Provider())
    with pytest.raises(RuntimeError, match="unavailable"):
        agent.ask("finish")
    run_id = agent.current_task_state.run_id
    calls = _jsonl(agent.run_store.call_ledger_path(run_id))
    turns = _jsonl(agent.run_store.turn_events_path(run_id))
    terminal = next(row for row in turns if row["kind"] == "turn.failed")

    assert calls[0]["cost_status"] == "incomplete_usage"
    assert terminal["payload"]["call_efficiency"]["call_count"] == 1
    assert terminal["payload"]["call_efficiency"]["cost_complete"] is False
