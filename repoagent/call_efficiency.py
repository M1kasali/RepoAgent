"""Per-provider-call pricing evidence and Turn efficiency summaries."""

from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Mapping

from .pricing import ModelPricing
from .providers.base import InputTokenSemantics, ModelUsage, UsageSource


CALL_LEDGER_FORMAT_VERSION = 1


def price_usage(
    usage: ModelUsage, pricing: ModelPricing | None
) -> tuple[float | None, str]:
    if usage.source in {UsageSource.MISSING, UsageSource.MIXED}:
        return None, "incomplete_usage"
    if pricing is None:
        return None, "unpriced"
    fresh_input_tokens = usage.input_tokens
    if usage.cache_read_tokens or usage.cache_write_tokens:
        if usage.input_token_semantics is InputTokenSemantics.AMBIGUOUS:
            return None, "ambiguous_cache_usage"
        if usage.input_token_semantics is InputTokenSemantics.TOTAL:
            cached = usage.cache_read_tokens + usage.cache_write_tokens
            if cached > usage.input_tokens:
                return None, "invalid_cache_usage"
            fresh_input_tokens -= cached
        if (
            usage.cache_read_tokens
            and pricing.cache_read_per_1m_usd is None
        ) or (
            usage.cache_write_tokens
            and pricing.cache_write_per_1m_usd is None
        ):
            return None, "cache_pricing_required"
    cost = (
        Decimal(fresh_input_tokens)
        * Decimal(str(pricing.input_per_1m_usd))
        + Decimal(usage.output_tokens)
        * Decimal(str(pricing.output_per_1m_usd))
        + Decimal(usage.cache_read_tokens)
        * Decimal(str(pricing.cache_read_per_1m_usd or 0))
        + Decimal(usage.cache_write_tokens)
        * Decimal(str(pricing.cache_write_per_1m_usd or 0))
    ) / Decimal(1_000_000)
    return float(cost), "priced"


@dataclass(frozen=True)
class CallEfficiencyEntry:
    provider_call_id: str
    turn_id: str
    request_id: str
    session_id: str
    agent_attempt: int
    provider_attempt: int
    provider: str
    model: str
    status: str
    duration_ms: int
    usage: ModelUsage = field(default_factory=ModelUsage)
    pricing: ModelPricing | None = None
    finish_reason: str = ""
    error_category: str = ""
    call_kind: str = "agent"
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""

    def __post_init__(self) -> None:
        if not self.provider_call_id:
            raise ValueError("provider_call_id must not be empty")
        if self.agent_attempt < 1 or self.provider_attempt < 0:
            raise ValueError("call attempt indexes are invalid")
        if self.status not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"invalid call status: {self.status}")
        if self.duration_ms < 0:
            raise ValueError("call duration must not be negative")
        if self.call_kind not in {"agent", "compaction"}:
            raise ValueError("call_kind must be agent or compaction")

    @property
    def estimated_cost_usd(self) -> float | None:
        return price_usage(self.usage, self.pricing)[0]

    @property
    def cost_status(self) -> str:
        return price_usage(self.usage, self.pricing)[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": CALL_LEDGER_FORMAT_VERSION,
            "provider_call_id": self.provider_call_id,
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "agent_attempt": self.agent_attempt,
            "provider_attempt": self.provider_attempt,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "finish_reason": self.finish_reason,
            "error_category": self.error_category,
            "call_kind": self.call_kind,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "stage": "provider",
            "usage": self.usage.to_metadata(),
            "pricing": self.pricing.to_dict() if self.pricing else None,
            "estimated_cost_usd": self.estimated_cost_usd,
            "cost_status": self.cost_status,
        }


@dataclass(frozen=True)
class CallEfficiencySummary:
    call_count: int
    completed_call_count: int
    failed_call_count: int
    cancelled_call_count: int
    priced_call_count: int
    cost_status_counts: Mapping[str, int]
    call_kind_counts: Mapping[str, int]
    partial_estimated_cost_usd: float
    cost_complete: bool
    successful_turn_count: int
    cost_per_successful_turn_usd: float | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "cost_status_counts",
            MappingProxyType(dict(self.cost_status_counts)),
        )
        object.__setattr__(
            self,
            "call_kind_counts",
            MappingProxyType(dict(self.call_kind_counts)),
        )

    @classmethod
    def from_entries(
        cls, entries, *, turn_succeeded: bool
    ) -> "CallEfficiencySummary":
        rows = tuple(entries)
        counts: dict[str, int] = {}
        priced_costs = []
        for row in rows:
            counts[row.cost_status] = counts.get(row.cost_status, 0) + 1
            if row.estimated_cost_usd is not None:
                priced_costs.append(row.estimated_cost_usd)
        total = float(sum(Decimal(str(value)) for value in priced_costs))
        complete = bool(rows) and len(priced_costs) == len(rows)
        successful_turn_count = int(bool(turn_succeeded))
        unit_cost = total if complete and turn_succeeded else None
        return cls(
            call_count=len(rows),
            completed_call_count=sum(row.status == "completed" for row in rows),
            failed_call_count=sum(row.status == "failed" for row in rows),
            cancelled_call_count=sum(row.status == "cancelled" for row in rows),
            priced_call_count=len(priced_costs),
            cost_status_counts=counts,
            call_kind_counts={
                kind: sum(row.call_kind == kind for row in rows)
                for kind in ("agent", "compaction")
            },
            partial_estimated_cost_usd=total,
            cost_complete=complete,
            successful_turn_count=successful_turn_count,
            cost_per_successful_turn_usd=unit_cost,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_count": self.call_count,
            "completed_call_count": self.completed_call_count,
            "failed_call_count": self.failed_call_count,
            "cancelled_call_count": self.cancelled_call_count,
            "priced_call_count": self.priced_call_count,
            "cost_status_counts": dict(self.cost_status_counts),
            "call_kind_counts": dict(self.call_kind_counts),
            "partial_estimated_cost_usd": self.partial_estimated_cost_usd,
            "cost_complete": self.cost_complete,
            "successful_turn_count": self.successful_turn_count,
            "cost_per_successful_turn_usd": self.cost_per_successful_turn_usd,
        }


__all__ = [
    "CALL_LEDGER_FORMAT_VERSION",
    "CallEfficiencyEntry",
    "CallEfficiencySummary",
    "ModelPricing",
    "price_usage",
]
