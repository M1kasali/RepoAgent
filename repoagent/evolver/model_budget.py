"""Fail-closed model admission for a single controlled evaluation trial."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from decimal import Decimal
import json
import math
import threading

from ..call_efficiency import price_usage
from ..pricing import ModelPricing
from ..providers.base import (
    InputTokenSemantics,
    ModelEvent,
    ModelRequest,
    ModelResult,
    ProviderError,
    UsageSource,
    generate_model,
)
from ..providers.fallback import FallbackModelClient
from ..providers.profiles import ModelProfile
from .evaluation import payload_digest


class EvaluationBudgetError(ProviderError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(
            f"evaluation model budget: {reason}", category="evaluation_budget"
        )


@dataclass(frozen=True)
class EvaluationModelLimits:
    max_calls: int = 10
    max_input_tokens: int = 12000
    max_output_tokens: int = 4096
    max_estimated_cost_usd: float = 1.0
    timeout_seconds: float = 60

    def __post_init__(self):
        for value in (self.max_calls, self.max_input_tokens, self.max_output_tokens):
            if type(value) is not int or value < 1:
                raise ValueError("model integer limits must be positive")
        for value in (self.max_estimated_cost_usd, self.timeout_seconds):
            if (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(
                    "model cost and timeout limits must be finite and positive"
                )


def _json_default(value):
    if is_dataclass(value):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("request metadata is not JSON serializable")


def _request_digest(request):
    values = {
        field.name: getattr(request, field.name)
        for field in fields(request)
        if field.name != "cancellation_token"
    }
    return payload_digest(
        json.loads(json.dumps(values, default=_json_default, allow_nan=False))
    )


class BudgetedEvaluationClient:
    """One typed leaf client, no hidden fallback, no exposure of partial streams.

    request_token_counter must count the complete provider request, including
    message framing and tools. Its accuracy is an integration responsibility.
    Reservations are in-memory within one trial; the paired coordinator must
    fence uncertain trial restarts and reserve the trial's outer cost envelope.
    """

    def __init__(
        self, client, *, limits, pricing, request_token_counter, counter_identity
    ):
        if isinstance(client, FallbackModelClient) or not callable(
            getattr(client, "generate", None)
        ):
            raise TypeError("evaluation budget requires a typed leaf model client")
        if not isinstance(limits, EvaluationModelLimits) or not isinstance(
            pricing, ModelPricing
        ):
            raise TypeError("evaluation budget requires explicit limits and pricing")
        if (
            pricing.cache_read_per_1m_usd is None
            or pricing.cache_write_per_1m_usd is None
        ):
            raise ValueError("evaluation admission requires explicit cache pricing")
        if (
            not callable(request_token_counter)
            or not isinstance(counter_identity, str)
            or not counter_identity.strip()
        ):
            raise ValueError(
                "evaluation requires an identified full-request token counter"
            )
        self._client = client
        self.limits, self.pricing = limits, pricing
        self._counter, self.counter_identity = request_token_counter, counter_identity
        self.model = str(getattr(client, "model", ""))
        if not self.model:
            raise ValueError("evaluation client requires an explicit model identity")
        profile = getattr(client, "profile", None)
        self.profile = (
            replace(profile, pricing=pricing)
            if isinstance(profile, ModelProfile)
            else None
        )
        if self.profile is not None and self.profile.model != self.model:
            raise ValueError("evaluation model and profile identities differ")
        self.supports_prompt_cache = bool(
            getattr(client, "supports_prompt_cache", False)
        )
        self.supports_structured_messages = bool(
            getattr(client, "supports_structured_messages", False)
        )
        self._lock = threading.Lock()
        self._entries = []
        self._reserved_cost = Decimal(0)
        self._blocked_reason = None

    def descriptor(self):
        return {
            "kind": "budgeted-evaluation-client/v1",
            "model": self.model,
            "limits": asdict(self.limits),
            "pricing": self.pricing.to_dict(),
            "counter_identity": self.counter_identity,
            "profile": self.profile.to_dict() if self.profile is not None else None,
        }

    def evidence(self):
        with self._lock:
            costs = [
                entry["estimated_cost_usd"]
                for entry in self._entries
                if entry.get("estimated_cost_usd") is not None
            ]
            return {
                "descriptor": self.descriptor(),
                "calls_reserved": len(self._entries),
                "reserved_cost_usd": str(self._reserved_cost),
                "blocked_reason": self._blocked_reason,
                "cost_complete": bool(self._entries)
                and len(costs) == len(self._entries),
                "measurement_valid": bool(self._entries)
                and all(entry["status"] == "completed" for entry in self._entries),
                "known_estimated_cost_usd": float(
                    sum((Decimal(str(cost)) for cost in costs), Decimal(0))
                ),
                "entries": json.loads(json.dumps(self._entries, allow_nan=False)),
            }

    def generate(self, request):
        if not isinstance(request, ModelRequest):
            raise TypeError("evaluation client requires ModelRequest")
        with self._lock:
            if request.cancellation_token is not None:
                request.cancellation_token.raise_if_cancelled(provider=self.model)
            if self._blocked_reason:
                raise EvaluationBudgetError(self._blocked_reason)
            if str(getattr(self._client, "model", "")) != self.model:
                raise EvaluationBudgetError("model_configuration_changed")
            if len(self._entries) >= self.limits.max_calls:
                raise EvaluationBudgetError("call_limit")
            if (
                type(request.max_output_tokens) is not int
                or not 0 < request.max_output_tokens <= self.limits.max_output_tokens
            ):
                raise EvaluationBudgetError("output_limit")
            if request.timeout_seconds is not None and (
                type(request.timeout_seconds) not in {int, float}
                or not math.isfinite(request.timeout_seconds)
                or request.timeout_seconds <= 0
            ):
                raise EvaluationBudgetError("invalid_timeout")
            effective = replace(
                request,
                timeout_seconds=min(
                    request.timeout_seconds
                    if request.timeout_seconds is not None
                    else self.limits.timeout_seconds,
                    self.limits.timeout_seconds,
                ),
            )
            counted = self._counter(effective)
            if type(counted) is not int or counted < 0:
                raise EvaluationBudgetError("invalid_input_count")
            if counted > self.limits.max_input_tokens:
                raise EvaluationBudgetError("input_limit")
            input_rate = max(
                self.pricing.input_per_1m_usd,
                self.pricing.cache_read_per_1m_usd,
                self.pricing.cache_write_per_1m_usd,
            )
            reserved = (
                Decimal(self.limits.max_input_tokens) * Decimal(str(input_rate))
                + Decimal(effective.max_output_tokens)
                * Decimal(str(self.pricing.output_per_1m_usd))
            ) / Decimal(1_000_000)
            if self._reserved_cost + reserved > Decimal(
                str(self.limits.max_estimated_cost_usd)
            ):
                raise EvaluationBudgetError("cost_limit")
            entry = {
                "index": len(self._entries),
                "request_digest": _request_digest(effective),
                "call_kind": effective.call_kind,
                "counted_input_tokens": counted,
                "reserved_cost_usd": str(reserved),
                "status": "started",
                "estimated_cost_usd": None,
            }
            self._entries.append(entry)
            self._reserved_cost += reserved
            try:
                result = generate_model(self._client, effective)
                entry["usage"] = json.loads(
                    json.dumps(asdict(result.usage), allow_nan=False)
                )
                reason = self._invalid_result(result)
                if reason:
                    raise EvaluationBudgetError(reason)
                cost, status = price_usage(result.usage, self.pricing)
                if (
                    status != "priced"
                    or cost is None
                    or not math.isfinite(cost)
                    or cost < 0
                ):
                    raise EvaluationBudgetError("unpriced_usage")
                entry["estimated_cost_usd"] = cost
                usage = result.usage
                cached = usage.cache_read_tokens + usage.cache_write_tokens
                total_input = usage.input_tokens + (
                    cached
                    if usage.input_token_semantics is InputTokenSemantics.FRESH
                    else 0
                )
                if (
                    total_input > self.limits.max_input_tokens
                    or usage.output_tokens > effective.max_output_tokens
                ):
                    raise EvaluationBudgetError("reported_tokens_exceed_limit")
                # Validated token ceilings and the maximum input rate bound cost;
                # comparing the display float to Decimal can reject exact ceilings.
                entry["status"] = "completed"
                return result
            except BaseException as exc:
                # An uncertain send may already have cost money. Never refund or retry it here.
                entry["status"] = "failed"
                entry["error_type"] = type(exc).__name__
                if isinstance(exc, EvaluationBudgetError):
                    entry["budget_failure"] = exc.reason
                self._blocked_reason = "previous_call_failed_or_unmeasurable"
                raise

    def _invalid_result(self, result):
        if not isinstance(result, ModelResult) or result.model != self.model:
            return "model_identity_mismatch"
        if "fallback" in result.metadata:
            return "nested_fallback_not_accounted"
        usage = result.usage
        if not isinstance(usage.input_token_semantics, InputTokenSemantics):
            return "invalid_input_semantics"
        counts = (
            usage.input_tokens,
            usage.output_tokens,
            usage.total_tokens,
            usage.cache_read_tokens,
            usage.cache_write_tokens,
        )
        if usage.source is not UsageSource.ACTUAL or any(
            type(value) is not int or value < 0 for value in counts
        ):
            return "invalid_or_nonactual_usage"
        cached = usage.cache_read_tokens + usage.cache_write_tokens
        if cached and usage.input_token_semantics is InputTokenSemantics.AMBIGUOUS:
            return "ambiguous_cache_usage"
        if (
            usage.input_token_semantics is InputTokenSemantics.TOTAL
            and cached > usage.input_tokens
        ):
            return "invalid_cache_usage"
        return None

    def stream(self, request):
        result = self.generate(request)
        # Keep evaluation deterministic and do not release unvalidated tool calls.
        yield ModelEvent(kind="completed", result=result)
