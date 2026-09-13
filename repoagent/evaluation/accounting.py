"""Offline Runtime accounting acceptance with explicit synthetic pricing."""

import json
from pathlib import Path

from ..pricing import ModelPricing
from ..providers import (
    FallbackModelClient, InputTokenSemantics, ModelProfile, ModelResult,
    ModelUsage, ProviderError, UsageSource,
)
from ..runtime import RepoAgent
from ..run_store import RunStore
from ..session_store import SessionStore
from ..workspace import WorkspaceContext


class _Provider:
    supports_prompt_cache = False
    model = "synthetic-accounting-model"

    def __init__(self, usage, pricing, *, fail=False):
        self.usage = usage
        self.fail = fail
        self.calls = 0
        self.profile = ModelProfile(
            name="offline", provider="offline", protocol="openai",
            model=self.model, base_url="https://offline.invalid/v1", pricing=pricing,
        )

    def generate(self, request):
        self.calls += 1
        if self.fail:
            raise ProviderError(
                "scripted unavailable", category="overloaded", provider="offline",
                retryable=True, should_fallback=True,
            )
        return ModelResult(
            text="<final>done</final>", usage=self.usage,
            provider="offline", model=self.model,
        )


def run_accounting_experiment(output_root):
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    pricing = ModelPricing(
        input_per_1m_usd=2, output_per_1m_usd=10,
        cache_read_per_1m_usd=0.2, source="synthetic arithmetic fixture, not vendor rates",
    )
    plain = ModelUsage(100, 20, 120, source=UsageSource.ACTUAL)
    total = ModelUsage(
        100, 20, 120, cache_read_tokens=40, source=UsageSource.ACTUAL,
        input_token_semantics=InputTokenSemantics.TOTAL,
    )
    fresh = ModelUsage(
        60, 20, 120, cache_read_tokens=40, source=UsageSource.ACTUAL,
        input_token_semantics=InputTokenSemantics.FRESH,
    )
    ambiguous = ModelUsage(100, 20, 120, cache_read_tokens=40, source=UsageSource.ACTUAL)
    cases = [
        ("uncached", plain, pricing, False, ["priced"], 0.0004),
        ("cache-total", total, pricing, False, ["priced"], 0.000328),
        ("cache-fresh", fresh, pricing, False, ["priced"], 0.000328),
        ("missing-usage", ModelUsage(), pricing, False, ["incomplete_usage"], None),
        ("ambiguous-cache", ambiguous, pricing, False, ["ambiguous_cache_usage"], None),
        ("missing-price", plain, None, False, ["unpriced"], None),
        ("fallback-missing-usage", plain, pricing, True, ["incomplete_usage", "priced"], None),
    ]
    rows = []
    for name, usage, rates, fallback, statuses, unit_cost in cases:
        directory = root / name
        workspace = directory / "workspace"
        workspace.mkdir(parents=True)
        provider = _Provider(usage, rates)
        providers = [_Provider(ModelUsage(), pricing, fail=True), provider] if fallback else [provider]
        agent = RepoAgent(
            model_client=FallbackModelClient(providers) if fallback else provider,
            workspace=WorkspaceContext.build(workspace),
            session_store=SessionStore(directory / "sessions"),
            run_store=RunStore(directory / "runs"),
            approval_policy="never", feature_flags={"memory": False},
        )
        answer = agent.ask("Reply done without using tools.")
        run_id = agent.current_task_state.run_id
        calls = agent.run_store.load_model_calls(run_id)
        report = agent.run_store.load_report(run_id)
        terminal = [row for row in agent.run_store.load_turn_events(run_id)
                    if row["kind"] == "turn.completed"]
        summary = report["call_efficiency"]
        expected_partial = 0.0004 if fallback else (unit_cost or 0.0)
        checks = {
            "answer": answer == "done",
            "call_conservation": len(calls) == sum(p.calls for p in providers) == len(statuses),
            "unique_call_ids": len({row["provider_call_id"] for row in calls}) == len(calls),
            "statuses": [row["cost_status"] for row in calls] == statuses,
            "execution_statuses": [row["status"] for row in calls] == (
                ["failed", "completed"] if fallback else ["completed"]
            ),
            "unit_cost": summary["cost_per_successful_turn_usd"] == unit_cost,
            "partial_cost": summary["partial_estimated_cost_usd"] == expected_partial,
            "completeness": summary["cost_complete"] == (unit_cost is not None),
            "report_call_count": summary["call_count"] == len(calls),
            "terminal_agrees": len(terminal) == 1 and terminal[0]["payload"]["call_efficiency"] == summary,
        }
        rows.append({
            "case": name, "checks": checks, "passed": all(checks.values()),
            "expected_unit_cost_usd": unit_cost, "expected_cost_statuses": statuses,
            "calls": calls, "summary": summary,
            "run_directory": agent.run_store.run_dir(run_id).relative_to(root).as_posix(),
        })
    result = {
        "schema": "repoagent.accounting-acceptance/v1",
        "evidence_scope": "scripted_provider_real_runtime_accounting_contract",
        "positive_claim_eligible": False, "passed": all(row["passed"] for row in rows),
        "cases": rows, "pricing": pricing.to_dict(),
        "comparisons": [
            {"control": "uncached", "treatment": "cache-total",
             "purpose": "Verify the prescribed cached-token arithmetic, not cache hit efficacy."},
            {"control": "cache-total", "treatment": "cache-fresh",
             "purpose": "Equivalent token semantics must yield equal cost."},
            {"control": "uncached", "treatment": "fallback-missing-usage",
             "purpose": "Missing failed-call usage must suppress complete unit cost."},
        ],
        "limitations": [
            "No network calls, vendor bills, real cache hits or quality improvements measured.",
            "ACTUAL denotes simulated provider-reported usage, not live API evidence.",
            "Fallback is exercised; HTTP transport retry behavior is not simulated here.",
        ],
    }
    (root / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
