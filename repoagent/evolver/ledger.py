"""Tamper-evident append-only history for candidate promotion decisions."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from ..atomic_io import append_jsonl_unlocked, file_lock, read_jsonl_unlocked


LEDGER_SCHEMA = "repoagent.evolver-ledger-event/v1"
GENESIS_DIGEST = "sha256:" + ("0" * 64)
PROTOCOL_EVENT_TYPES = frozenset(
    {
        "candidate.created",
        "candidate.materialized",
        "gate.evaluated",
        "approval.requested",
        "approval.confirmed",
        "activation.activated",
        "activation.rollback",
    }
)
_PROTOCOL_AUTHORITY = object()


class LedgerIntegrityError(RuntimeError):
    pass


def _digest(payload):
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class EvolutionLedger:
    def __init__(self, path):
        self.path = Path(path)
        self.lock_path = self.path.parent / ".lock" / f"{self.path.name}.lock"

    @staticmethod
    def _verify_rows(rows):
        previous = GENESIS_DIGEST
        for sequence, row in enumerate(rows, start=1):
            if row.get("schema") != LEDGER_SCHEMA:
                raise LedgerIntegrityError(f"invalid ledger schema at sequence {sequence}")
            if row.get("sequence") != sequence:
                raise LedgerIntegrityError(f"invalid ledger sequence {sequence}")
            if row.get("previous_digest") != previous:
                raise LedgerIntegrityError(f"broken ledger chain at sequence {sequence}")
            unsigned = {key: value for key, value in row.items() if key != "digest"}
            observed = _digest(unsigned)
            if row.get("digest") != observed:
                raise LedgerIntegrityError(f"invalid ledger digest at sequence {sequence}")
            previous = observed
        return rows

    def events(self):
        with file_lock(self.lock_path):
            rows = read_jsonl_unlocked(self.path)
            return tuple(self._verify_rows(rows))

    def verify(self):
        return bool(self.events() or not self.path.exists())

    def _append(self, event_type, *, actor, candidate_id=None, payload=None):
        if not event_type or not actor:
            raise ValueError("ledger event type and actor are required")
        payload = dict(payload or {})
        with file_lock(self.lock_path):
            rows = read_jsonl_unlocked(self.path)
            self._verify_rows(rows)
            event = {
                "schema": LEDGER_SCHEMA,
                "sequence": len(rows) + 1,
                "event_id": "evolution_" + uuid4().hex,
                "event_type": str(event_type),
                "actor": str(actor),
                "candidate_id": str(candidate_id) if candidate_id is not None else None,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "previous_digest": rows[-1]["digest"] if rows else GENESIS_DIGEST,
                "payload": payload,
            }
            event["digest"] = _digest(event)
            append_jsonl_unlocked(self.path, event)
            return dict(event)

    def append(self, event_type, *, actor, candidate_id=None, payload=None):
        if event_type in PROTOCOL_EVENT_TYPES:
            raise PermissionError(
                "protocol events must be written through ControlledEvolver"
            )
        return self._append(
            event_type,
            actor=actor,
            candidate_id=candidate_id,
            payload=payload,
        )

    def _append_protocol(
        self,
        event_type,
        *,
        actor,
        candidate_id=None,
        payload=None,
        authority=None,
    ):
        if authority is not _PROTOCOL_AUTHORITY or event_type not in PROTOCOL_EVENT_TYPES:
            raise PermissionError("invalid evolution protocol event authority")
        return self._append(
            event_type,
            actor=actor,
            candidate_id=candidate_id,
            payload=payload,
        )


__all__ = [
    "GENESIS_DIGEST",
    "LEDGER_SCHEMA",
    "PROTOCOL_EVENT_TYPES",
    "EvolutionLedger",
    "LedgerIntegrityError",
]
