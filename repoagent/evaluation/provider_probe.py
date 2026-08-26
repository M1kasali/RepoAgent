"""Bounded live-provider preflight aligned with Pico Harness campaigns."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from collections.abc import Mapping

from ..providers import ModelRequest, ModelTool, UsageSource, stream_model


PROVIDER_PROBE_MAX_ATTEMPTS = 2
_REQUIRED_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


class ProviderProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderProbeResult:
    provider_name: str
    requested_model: str
    resolved_model: str
    tool_calling_supported: bool
    usage_fields: tuple[str, ...]
    usage_source: str
    tokenizer_identity: str
    tokenizer_source: str
    tokenizer_digest: str
    pricing_source: str
    attempts: int
    fallback_used: bool
    timeout_seconds: float
    max_output_tokens: int
    approval_identity: dict
    approval_digest: str

    def to_dict(self):
        return asdict(self)


def build_probe_approval(
    *,
    provider: str,
    model: str,
    tokenizer_metadata: Mapping,
    pricing_source: str,
    max_attempts: int,
    max_output_tokens: int,
    timeout_seconds: float,
    approval_identity: Mapping | None = None,
):
    approval = dict(approval_identity or {})
    if approval:
        required = {
            "source_commit",
            "source_tree_digest",
            "source_dirty",
            "benchmark_digest",
            "runtime_config_digest",
            "sandbox_identity",
            "sandbox_isolated",
        }
        missing = sorted(required - set(approval))
        if missing:
            raise ProviderProbeError(
                "provider preflight approval identity is missing: "
                + ", ".join(missing)
            )
        for key in required - {"source_dirty", "sandbox_isolated"}:
            if not str(approval.get(key, "")).strip():
                raise ProviderProbeError(
                    f"provider preflight approval identity has empty {key}"
                )
        if not isinstance(approval["source_dirty"], bool) or not isinstance(
            approval["sandbox_isolated"], bool
        ):
            raise ProviderProbeError(
                "provider preflight approval booleans must be explicit"
            )
    payload = {
        **approval,
        "provider": str(provider),
        "model": str(model),
        "tokenizer": dict(tokenizer_metadata),
        "pricing_source": str(pricing_source),
        "max_attempts": int(max_attempts),
        "max_output_tokens": int(max_output_tokens),
        "timeout_seconds": float(timeout_seconds),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return approval, "sha256:" + hashlib.sha256(encoded).hexdigest()


def run_provider_probe(
    model_client,
    *,
    requested_model: str,
    tokenizer_metadata: Mapping | None = None,
    pricing_source: str = "",
    max_output_tokens: int = 128,
    max_attempts: int = PROVIDER_PROBE_MAX_ATTEMPTS,
    timeout_seconds: float = 60.0,
    approval_identity: Mapping | None = None,
) -> ProviderProbeResult:
    if max_attempts < 1:
        raise ValueError("provider probe max_attempts must be positive")
    if timeout_seconds <= 0:
        raise ValueError("provider probe timeout_seconds must be positive")
    profile = getattr(model_client, "profile", None)
    expected_provider = str(getattr(profile, "provider", "")).strip()
    requested_model = str(requested_model).strip()
    if not expected_provider:
        raise ProviderProbeError(
            "provider preflight requires a configured provider identity"
        )
    if not requested_model:
        raise ProviderProbeError(
            "provider preflight requires a configured model identity"
        )
    tokenizer = dict(tokenizer_metadata or {})
    tokenizer_identity = str(tokenizer.get("identity", "")).strip()
    tokenizer_source = str(tokenizer.get("source", "")).strip()
    if not tokenizer_identity or not tokenizer_source:
        raise ProviderProbeError(
            "provider preflight requires tokenizer identity metadata"
        )
    pricing_source = str(pricing_source).strip()
    if not pricing_source:
        raise ProviderProbeError("provider preflight requires a pricing source")
    approval, approval_digest = build_probe_approval(
        provider=expected_provider,
        model=requested_model,
        tokenizer_metadata=tokenizer,
        pricing_source=pricing_source,
        max_attempts=max_attempts,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        approval_identity=approval_identity,
    )

    tool = ModelTool(
        name="repoagent_preflight_echo",
        description="Return the exact probe value to verify native tool calling.",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    last_result = None
    for attempt in range(1, max_attempts + 1):
        last_result = stream_model(
            model_client,
            ModelRequest(
                prompt=(
                    'Call repoagent_preflight_echo exactly once with value "ready". '
                    "Do not answer with text."
                ),
                max_output_tokens=max_output_tokens,
                turn_id="provider-preflight",
                session_id="provider-preflight",
                request_id="provider-preflight",
                attempt=attempt,
                tools=(tool,),
                timeout_seconds=timeout_seconds,
            ),
        )
        if any(
            call.name == tool.name and dict(call.arguments) == {"value": "ready"}
            for call in last_result.tool_calls
        ):
            break
    else:
        raise ProviderProbeError(
            f"provider did not return a valid native tool call after {max_attempts} attempts"
        )

    actual_provider = str(last_result.provider or "").strip()
    if actual_provider != expected_provider:
        raise ProviderProbeError("provider identity changed during preflight")
    resolved_model = str(last_result.model or "").strip()
    if not resolved_model:
        raise ProviderProbeError("provider preflight did not report model identity")
    if resolved_model != requested_model:
        raise ProviderProbeError("model identity changed during preflight")
    usage_metadata = last_result.usage.to_metadata()
    usage_fields = tuple(
        field for field in _REQUIRED_USAGE_FIELDS if field in usage_metadata
    )
    if last_result.usage.source is not UsageSource.ACTUAL:
        raise ProviderProbeError("provider preflight requires actual usage metadata")
    missing_usage = set(_REQUIRED_USAGE_FIELDS) - set(usage_fields)
    if missing_usage:
        raise ProviderProbeError(
            "provider preflight is missing required usage fields: "
            + ", ".join(sorted(missing_usage))
        )
    metadata = dict(last_result.metadata)
    fallback = metadata.get("fallback")
    fallback_used = bool(isinstance(fallback, dict) and fallback.get("used"))
    if fallback_used:
        raise ProviderProbeError("provider preflight used a fallback model")
    tokenizer_payload = json.dumps(
        tokenizer, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return ProviderProbeResult(
        provider_name=actual_provider,
        requested_model=requested_model,
        resolved_model=resolved_model,
        tool_calling_supported=True,
        usage_fields=usage_fields,
        usage_source=last_result.usage.source.value,
        tokenizer_identity=tokenizer_identity,
        tokenizer_source=tokenizer_source,
        tokenizer_digest="sha256:" + hashlib.sha256(tokenizer_payload).hexdigest(),
        pricing_source=pricing_source,
        attempts=attempt,
        fallback_used=fallback_used,
        timeout_seconds=float(timeout_seconds),
        max_output_tokens=int(max_output_tokens),
        approval_identity=approval,
        approval_digest=approval_digest,
    )


__all__ = [
    "PROVIDER_PROBE_MAX_ATTEMPTS",
    "ProviderProbeError",
    "ProviderProbeResult",
    "build_probe_approval",
    "run_provider_probe",
]
