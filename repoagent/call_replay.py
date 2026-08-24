"""Deterministic offline verification for persisted model-call ledgers."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .atomic_io import atomic_replace
from .call_efficiency import CALL_LEDGER_FORMAT_VERSION, CallEfficiencyEntry
from .pricing import ModelPricing
from .providers.base import ModelUsage


CALL_REPLAY_SCHEMA = "repoagent.call-replay.v1"


class CallReplayError(ValueError):
    pass


def file_digest(path: str | Path) -> str:
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return f"sha256:{digest}"


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _pricing_from_record(value: Any, *, row_index: int) -> ModelPricing | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CallReplayError(f"call row {row_index} pricing must be an object")
    try:
        pricing = ModelPricing(
            input_per_1m_usd=value["input_per_1m_usd"],
            output_per_1m_usd=value["output_per_1m_usd"],
            source=value["source"],
            currency=value.get("currency", "USD"),
            cache_read_per_1m_usd=value.get("cache_read_per_1m_usd"),
            cache_write_per_1m_usd=value.get("cache_write_per_1m_usd"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CallReplayError(
            f"call row {row_index} has invalid pricing"
        ) from exc
    if value.get("pricing_id") != pricing.pricing_id:
        raise CallReplayError(f"call row {row_index} pricing_id mismatch")
    return pricing


def _entry_from_record(value: Any, *, row_index: int) -> CallEfficiencyEntry:
    if not isinstance(value, dict):
        raise CallReplayError(f"call row {row_index} must be an object")
    if value.get("format_version") != CALL_LEDGER_FORMAT_VERSION:
        raise CallReplayError(
            f"call row {row_index} has unsupported format_version"
        )
    try:
        usage = ModelUsage.from_metadata(value["usage"])
        return CallEfficiencyEntry(
            provider_call_id=value["provider_call_id"],
            turn_id=value["turn_id"],
            request_id=value["request_id"],
            session_id=value["session_id"],
            agent_attempt=value["agent_attempt"],
            provider_attempt=value["provider_attempt"],
            provider=value["provider"],
            model=value["model"],
            status=value["status"],
            duration_ms=value["duration_ms"],
            usage=usage,
            pricing=_pricing_from_record(value.get("pricing"), row_index=row_index),
            finish_reason=value.get("finish_reason", ""),
            error_category=value.get("error_category", ""),
            call_kind=value.get("call_kind", "agent"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, CallReplayError):
            raise
        raise CallReplayError(f"call row {row_index} is malformed") from exc


def _load_rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            raise CallReplayError(f"call ledger contains blank row {index}")
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise CallReplayError(f"call row {index} is invalid JSON") from exc
    if not rows:
        raise CallReplayError("call ledger must contain at least one row")
    return rows


def replay_call_ledger(
    path: str | Path,
    *,
    expected_source_digest: str,
) -> dict[str, Any]:
    """Recompute immutable call evidence and bind it to an external digest."""

    source_path = Path(path)
    if not expected_source_digest:
        raise CallReplayError("expected_source_digest is required")
    source_digest = file_digest(source_path)
    rows = _load_rows(source_path)
    findings = []
    replayed_rows = []
    seen_call_ids = set()
    for index, row in enumerate(rows):
        entry = _entry_from_record(row, row_index=index)
        if entry.provider_call_id in seen_call_ids:
            findings.append(f"duplicate_provider_call_id:{entry.provider_call_id}")
        seen_call_ids.add(entry.provider_call_id)
        replayed = entry.to_dict()
        if row != replayed:
            findings.append(f"call_evidence_mismatch:{index}")
        replayed_rows.append(replayed)
    expected_matches = source_digest == expected_source_digest
    if not expected_matches:
        findings.append("source_digest_not_expected")
    report = {
        "schema": CALL_REPLAY_SCHEMA,
        "source_digest": source_digest,
        "expected_source_digest": expected_source_digest,
        "expected_source_digest_matches": expected_matches,
        "row_count": len(rows),
        "replayed_rows_digest": _canonical_digest(replayed_rows),
        "equivalent": not findings,
        "findings": findings,
    }
    return {**report, "replay_digest": _canonical_digest(report)}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay and verify a RepoAgent calls.jsonl ledger offline."
    )
    parser.add_argument("--source", required=True, help="Source calls.jsonl")
    parser.add_argument(
        "--expected-source-digest",
        required=True,
        help="Externally recorded sha256:<hex> digest for the source ledger.",
    )
    parser.add_argument("--output", required=True, help="Replay report JSON")
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    if source == output:
        parser.error("replay output must not overwrite the source ledger")
    report = replay_call_ledger(
        source,
        expected_source_digest=args.expected_source_digest,
    )
    atomic_replace(
        output,
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
    )
    return 0 if report["equivalent"] else 1


__all__ = [
    "CALL_REPLAY_SCHEMA",
    "CallReplayError",
    "build_arg_parser",
    "file_digest",
    "main",
    "replay_call_ledger",
]
