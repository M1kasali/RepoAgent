"""Issue-specific admission settings matching the actual Anthropic wire input."""

from decimal import Decimal
import json
from dataclasses import replace

from ..evolver.model_budget import EvaluationModelLimits, _json_default
from ..providers.clients import _anthropic_messages
from ..context_overflow import emergency_shrink_messages
from ..evolver.model_budget import EvaluationBudgetError


def issue_limits():
    per_call = (Decimal(128000) * Decimal("0.3") + Decimal(4096) * Decimal("1.2")) / 1000000
    return EvaluationModelLimits(
        max_calls=min(24, int(Decimal("1") // per_call)),
        max_input_tokens=128000, max_output_tokens=4096,
        max_estimated_cost_usd=1, timeout_seconds=90,
    )


def count_anthropic_request(request, *, model, temperature=None):
    # A conservative serialized-byte bound, not an exact model tokenizer.
    # Structured messages replace request.prompt on this provider's wire path.
    payload = {"model": model, "messages": _anthropic_messages(request),
               "max_tokens": request.max_output_tokens, "stream": True}
    if temperature is not None:
        payload["temperature"] = temperature
    if request.tools:
        payload["tools"] = [{"name": tool.name, "description": tool.description,
                              "input_schema": dict(tool.parameters)} for tool in request.tools]
    return len(json.dumps(payload, default=_json_default).encode("utf-8")) + 512


def fit_issue_request(request, *, model, input_limit, temperature=None):
    before = count_anthropic_request(request, model=model, temperature=temperature)
    if before <= input_limit:
        return request, None
    messages, elided = emergency_shrink_messages(request.messages)
    fitted = replace(request, messages=messages)
    after = count_anthropic_request(fitted, model=model, temperature=temperature)
    if not elided or after > input_limit:
        raise EvaluationBudgetError("input_limit")
    return fitted, {"before_input_bound": before, "after_input_bound": after,
                    "input_limit": input_limit, "elided_tool_results": elided}
