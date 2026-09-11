from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

import pytest

from repoagent import RepoAgent, SessionStore, WorkspaceContext
from repoagent.evolver import (
    BudgetedEvaluationClient,
    EvaluationBudgetError,
    EvaluationModelLimits,
)
from repoagent.pricing import ModelPricing
from repoagent.providers.base import (
    CancellationToken,
    InputTokenSemantics,
    ModelMessage,
    ModelRequest,
    ModelResult,
    ModelTool,
    ModelUsage,
    ProviderConnectionError,
    ProviderCancelledError,
    UsageSource,
    stream_model,
)
from repoagent.providers.fallback import FallbackModelClient
from repoagent.providers.profiles import ModelProfile


class LeafClient:
    model = "fixture-model"
    supports_structured_messages = True

    def __init__(self, *, usage=None, error=None, outputs=None):
        self.profile = ModelProfile(
            name="fixture",
            provider="fixture",
            protocol="openai",
            model=self.model,
            base_url="https://fixture.invalid/v1",
        )
        self.requests = []
        self.usage = usage or ModelUsage(
            input_tokens=10, output_tokens=5, total_tokens=15, source=UsageSource.ACTUAL
        )
        self.error = error
        self.outputs = outputs or ["<final>Done.</final>"]

    def generate(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return ModelResult(
            text=self.outputs[min(len(self.requests) - 1, len(self.outputs) - 1)],
            usage=self.usage,
            model=self.model,
            provider="fixture",
        )


def _client(leaf=None, *, limits=None, pricing=None, counter=None):
    return BudgetedEvaluationClient(
        leaf or LeafClient(),
        limits=limits
        or EvaluationModelLimits(
            max_calls=2, max_input_tokens=100, max_output_tokens=20
        ),
        pricing=pricing
        or ModelPricing(
            1,
            1,
            "test-only snapshot",
            cache_read_per_1m_usd=1,
            cache_write_per_1m_usd=1,
        ),
        request_token_counter=counter or (lambda request: 10),
        counter_identity="test-full-request/v1",
    )


def _request(**kwargs):
    return ModelRequest("request", kwargs.pop("max_output_tokens", 20), **kwargs)


def test_calls_are_reserved_once_and_costs_remain_conservative():
    leaf = LeafClient()
    client = _client(leaf)
    client.generate(_request())
    client.generate(_request(call_kind="compaction"))
    with pytest.raises(EvaluationBudgetError, match="call_limit"):
        client.generate(_request())
    evidence = client.evidence()
    assert len(leaf.requests) == evidence["calls_reserved"] == 2
    assert float(evidence["reserved_cost_usd"]) == pytest.approx(0.00024)
    assert evidence["known_estimated_cost_usd"] == pytest.approx(0.00003)
    assert evidence["cost_complete"]
    assert evidence["entries"][1]["call_kind"] == "compaction"


def test_budget_denials_happen_before_backend_and_exact_cost_boundary_is_allowed():
    leaf = LeafClient()
    client = _client(
        leaf,
        limits=EvaluationModelLimits(
            max_calls=3,
            max_input_tokens=100,
            max_output_tokens=20,
            max_estimated_cost_usd=0.00012,
        ),
    )
    client.generate(_request())
    with pytest.raises(EvaluationBudgetError, match="cost_limit"):
        client.generate(_request())
    assert len(leaf.requests) == 1


@pytest.mark.parametrize(
    "model_request,counter,reason",
    [
        (_request(max_output_tokens=21), lambda request: 10, "output_limit"),
        (_request(max_output_tokens=True), lambda request: 10, "output_limit"),
        (_request(), lambda request: 101, "input_limit"),
        (_request(), lambda request: True, "invalid_input_count"),
        (_request(), lambda request: -1, "invalid_input_count"),
        (_request(timeout_seconds=float("nan")), lambda request: 10, "invalid_timeout"),
    ],
)
def test_invalid_requests_never_reserve_or_send(model_request, counter, reason):
    leaf = LeafClient()
    client = _client(leaf, counter=counter)
    with pytest.raises(EvaluationBudgetError, match=reason):
        client.generate(model_request)
    assert not leaf.requests
    assert client.evidence()["calls_reserved"] == 0


def test_counter_receives_full_messages_and_tools_and_timeout_is_bounded():
    seen = []
    leaf = LeafClient()
    client = _client(leaf, counter=lambda request: seen.append(request) or 10)
    request = _request(
        timeout_seconds=300,
        messages=(ModelMessage("user", "structured"),),
        tools=(ModelTool("read", "read files", {"type": "object", "properties": {}}),),
    )
    client.generate(request)
    assert seen[0].messages == request.messages
    assert seen[0].tools == request.tools
    assert leaf.requests[0].timeout_seconds == 60
    assert client.evidence()["entries"][0]["request_digest"].startswith("sha256:")


def test_reservation_uses_most_expensive_cache_rate_not_just_fresh_input():
    leaf = LeafClient()
    client = _client(
        leaf,
        pricing=ModelPricing(
            1, 1, "test", cache_read_per_1m_usd=1, cache_write_per_1m_usd=10
        ),
        limits=EvaluationModelLimits(
            max_input_tokens=100, max_output_tokens=20, max_estimated_cost_usd=0.0005
        ),
    )
    with pytest.raises(EvaluationBudgetError, match="cost_limit"):
        client.generate(_request())
    assert not leaf.requests


@pytest.mark.parametrize(
    "changes",
    [
        {"source": UsageSource.MISSING},
        {"source": UsageSource.ESTIMATED},
        {"source": UsageSource.MIXED},
        {"input_tokens": -1},
        {"output_tokens": True},
        {"input_tokens": 101},
        {"output_tokens": 21},
        {
            "cache_read_tokens": 2,
            "input_token_semantics": InputTokenSemantics.AMBIGUOUS,
        },
        {"cache_read_tokens": 11, "input_token_semantics": InputTokenSemantics.TOTAL},
        {
            "input_tokens": 90,
            "cache_write_tokens": 20,
            "input_token_semantics": InputTokenSemantics.FRESH,
        },
        {"input_token_semantics": "fresh"},
        {"input_tokens": float("nan")},
    ],
)
def test_invalid_usage_blocks_subsequent_calls_and_never_releases_result(changes):
    leaf = LeafClient()
    leaf.usage = replace(leaf.usage, **changes)
    client = _client(leaf)
    with pytest.raises((EvaluationBudgetError, ValueError)):
        client.generate(_request())
    with pytest.raises(EvaluationBudgetError, match="previous_call"):
        client.generate(_request())
    assert len(leaf.requests) == 1
    evidence = client.evidence()
    assert not evidence["measurement_valid"]
    assert evidence["entries"][0]["status"] == "failed"
    json.dumps(evidence, allow_nan=False)


@pytest.mark.parametrize(
    "error", [ProviderConnectionError("connection lost"), KeyboardInterrupt()]
)
def test_uncertain_send_keeps_reservation_and_refuses_retry(error):
    leaf = LeafClient(error=error)
    client = _client(leaf)
    with pytest.raises(type(error)):
        client.generate(_request())
    with pytest.raises(EvaluationBudgetError, match="previous_call"):
        client.generate(_request())
    assert client.evidence()["reserved_cost_usd"] != "0"
    assert len(leaf.requests) == 1


def test_pre_cancelled_request_is_not_sent_or_reserved():
    token = CancellationToken()
    token.cancel()
    leaf = LeafClient()
    client = _client(leaf)
    with pytest.raises(ProviderCancelledError):
        client.generate(_request(cancellation_token=token))
    assert not leaf.requests
    assert client.evidence()["calls_reserved"] == 0


def test_stream_does_not_emit_unvalidated_output():
    client = _client(LeafClient(usage=ModelUsage()))
    stream = client.stream(_request())
    with pytest.raises(EvaluationBudgetError):
        next(stream)
    assert stream_model(_client(), _request()).text == "<final>Done.</final>"


def test_concurrent_callers_cannot_overbook_slots():
    leaf = LeafClient()
    client = _client(leaf)

    def run(_):
        try:
            client.generate(_request())
            return True
        except EvaluationBudgetError:
            return False

    with ThreadPoolExecutor(max_workers=5) as pool:
        assert sum(pool.map(run, range(5))) == 2
    assert len(leaf.requests) == 2


def test_hidden_fallback_is_rejected_and_cache_prices_are_required():
    with pytest.raises(TypeError, match="leaf"):
        _client(FallbackModelClient([LeafClient()]))
    with pytest.raises(ValueError, match="cache pricing"):
        _client(pricing=ModelPricing(1, 1, "test"))


@pytest.mark.parametrize(
    "changes",
    [
        {"max_calls": True},
        {"max_input_tokens": 0},
        {"max_output_tokens": 1.2},
        {"timeout_seconds": float("nan")},
        {"max_estimated_cost_usd": -1},
    ],
)
def test_limits_reject_invalid_values(changes):
    with pytest.raises(ValueError):
        EvaluationModelLimits(**changes)


@pytest.mark.parametrize("max_calls,exists", [(1, True), (2, True)])
def test_actual_agent_loop_uses_guard_for_tool_and_final_calls(
    tmp_path, max_calls, exists
):
    leaf = LeafClient(
        outputs=[
            '<tool name="write_file" path="result.txt"><content>done</content></tool>',
            "<final>Done.</final>",
        ]
    )
    client = _client(
        leaf,
        limits=EvaluationModelLimits(
            max_calls=max_calls, max_input_tokens=20000, max_output_tokens=20
        ),
    )
    agent = RepoAgent(
        model_client=client,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent/sessions"),
        approval_policy="auto",
        max_steps=3,
        max_new_tokens=20,
        allowed_tools=["write_file"],
    )
    if max_calls == 1:
        with pytest.raises(RuntimeError, match="evaluation model budget: call_limit"):
            agent.ask("Write done to result.txt")
    else:
        agent.ask("Write done to result.txt")
    assert (tmp_path / "result.txt").exists() is exists
    assert len(leaf.requests) == max_calls
    assert client.evidence()["calls_reserved"] == max_calls
    if max_calls == 2:
        assert agent.current_task_state.status == "completed"
        report = json.loads(
            agent.run_store.report_path(agent.current_task_state.run_id).read_text()
        )
        assert report["call_efficiency"]["cost_complete"]
        assert report["call_efficiency"][
            "cost_per_successful_turn_usd"
        ] == pytest.approx(0.00003)
        assert leaf.profile.pricing is None
    else:
        assert agent.current_task_state.status != "completed"


def test_reported_overrun_keeps_known_cost_but_invalidates_measurement():
    leaf = LeafClient()
    leaf.usage = replace(leaf.usage, input_tokens=101)
    client = _client(leaf)
    with pytest.raises(EvaluationBudgetError, match="reported_tokens"):
        client.generate(_request())
    evidence = client.evidence()
    assert evidence["cost_complete"]
    assert not evidence["measurement_valid"]
    assert evidence["known_estimated_cost_usd"] == pytest.approx(0.000106)


def test_model_drift_is_rejected_before_send():
    leaf = LeafClient()
    client = _client(leaf)
    leaf.model = "changed"
    with pytest.raises(EvaluationBudgetError, match="configuration_changed"):
        client.generate(_request())
    assert not leaf.requests


def test_response_model_mismatch_blocks_reuse_and_pricing():
    class WrongModel(LeafClient):
        def generate(self, request):
            return replace(super().generate(request), model="wrong-model")

    client = _client(WrongModel())
    with pytest.raises(EvaluationBudgetError, match="identity_mismatch"):
        client.generate(_request())
    assert not client.evidence()["cost_complete"]


def test_post_send_cancellation_retains_uncertain_reservation():
    token = CancellationToken()

    class Cancel(LeafClient):
        def generate(self, request):
            result = super().generate(request)
            token.cancel()
            return result

    leaf = Cancel()
    client = _client(leaf)
    with pytest.raises(ProviderCancelledError):
        client.generate(_request(cancellation_token=token))
    with pytest.raises(EvaluationBudgetError, match="previous_call"):
        client.generate(_request())
    assert len(leaf.requests) == 1
    assert not client.evidence()["cost_complete"]


def test_reported_hidden_fallback_is_not_a_single_priced_call():
    class Nested(LeafClient):
        def generate(self, request):
            return replace(
                super().generate(request), metadata={"fallback": {"used": True}}
            )

    client = _client(Nested())
    with pytest.raises(EvaluationBudgetError, match="nested_fallback"):
        client.generate(_request())
    assert not client.evidence()["cost_complete"]


def test_reported_float_price_rounding_does_not_reject_valid_token_ceiling():
    leaf = LeafClient(
        usage=ModelUsage(
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            source=UsageSource.ACTUAL,
        )
    )
    rate = 0.9999999999999999
    client = _client(
        leaf,
        pricing=ModelPricing(
            rate,
            rate,
            "fractional test rate",
            cache_read_per_1m_usd=rate,
            cache_write_per_1m_usd=rate,
        ),
    )
    client.generate(_request())
    assert client.evidence()["measurement_valid"]
