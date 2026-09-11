"""Explicit release of unused reservations only after complete verified receipts."""

from decimal import Decimal
from pathlib import Path

from ..atomic_io import file_lock
from .evaluation import CandidateEvaluationError, payload_digest
from .ledger import EvolutionLedger, _PROTOCOL_AUTHORITY
from .paired_execution import _cost, _read_receipt


def settle_paired_costs(evolver, candidate_id):
    with file_lock(evolver.ledger.path.parent / ".lock" / "paired-execution.lock"):
        events = [
            e for e in evolver.ledger.events() if e["candidate_id"] == candidate_id
        ]
        reservations = [e for e in events if e["event_type"] == "paired.reserved"]
        if any(e["event_type"] == "paired.budget_violation" for e in events):
            raise CandidateEvaluationError(
                "budget violations require operator review before settlement"
            )
        if len(reservations) != 1:
            raise CandidateEvaluationError("settlement requires one reservation")
        reservation = reservations[0]
        completed = [e for e in events if e["event_type"] == "paired.trial_completed"]
        count = reservation["payload"]["pair_n"] * 2
        if len(completed) != count or {e["payload"]["index"] for e in completed} != set(
            range(count)
        ):
            raise CandidateEvaluationError(
                "incomplete or uncertain trials retain reservations"
            )
        total, digests = Decimal(0), []
        for event in sorted(completed, key=lambda e: e["payload"]["index"]):
            index = event["payload"]["index"]
            digest = event["payload"]["receipt_digest"]
            path = (
                evolver.ledger.path.parent
                / "evidence"
                / f"{candidate_id}-paired-{index}.json"
            )
            receipt = _read_receipt(path, digest)
            if receipt["plan_digest"] != reservation["payload"]["plan_digest"]:
                raise CandidateEvaluationError("settlement plan mismatch")
            result = receipt["result"]
            if result["status"] != "completed" or result["estimated_cost_usd"] is None:
                raise CandidateEvaluationError(
                    "invalid or unpriced trial retains reservation"
                )
            raw = result["raw"]
            if "model_journal" in raw:
                journal = raw["model_journal"]
                rows = EvolutionLedger(journal["path"]).events()
                if (
                    not rows
                    or len(rows) != journal["events"]
                    or rows[-1]["digest"] != journal["tail_digest"]
                    or rows[-1]["payload"].get("model_evidence")
                    != raw["model_evidence"]
                ):
                    raise CandidateEvaluationError(
                        "model journal differs from anchored receipt"
                    )
                evidence = raw["model_evidence"]
                if (
                    not evidence["cost_complete"]
                    or not evidence["measurement_valid"]
                    or evidence["known_estimated_cost_usd"]
                    != result["estimated_cost_usd"]
                ):
                    raise CandidateEvaluationError("model cost evidence is incomplete")
            total += _cost(result["estimated_cost_usd"])
            digests.append(digest)
        reserved = Decimal(reservation["payload"]["reserved_cost_usd"])
        if total > reserved:
            raise CandidateEvaluationError("reported cost exceeded reservation")
        payload = {
            "reservation_digest": reservation["digest"],
            "receipt_digests": digests,
            "actual_estimated_cost_usd": str(total),
            "released_cost_usd": str(reserved - total),
        }
        prior = [e for e in events if e["event_type"] == "paired.settled"]
        if prior:
            if len(prior) != 1 or prior[0]["payload"] != payload:
                raise CandidateEvaluationError("settlement evidence changed")
            return payload
        evolver.ledger._append_protocol(
            "paired.settled",
            actor="cost-settlement",
            candidate_id=candidate_id,
            payload=payload,
            authority=_PROTOCOL_AUTHORITY,
        )
        return payload


def reconcile_trial_cost(evolver, candidate_id, index, *, journal_path, actor):
    """Accounting-only operator action: never reconstruct quality or rerun work."""
    if (
        type(index) is not int
        or index < 0
        or not isinstance(actor, str)
        or not actor.strip()
    ):
        raise ValueError("reconciliation requires a trial index and explicit actor")
    with file_lock(evolver.ledger.path.parent / ".lock" / "paired-execution.lock"):
        events = [
            e for e in evolver.ledger.events() if e["candidate_id"] == candidate_id
        ]
        reservations = [e for e in events if e["event_type"] == "paired.reserved"]
        starts = [
            e
            for e in events
            if e["event_type"] == "paired.trial_started"
            and e["payload"]["index"] == index
        ]
        if len(reservations) != 1 or len(starts) != 1:
            raise CandidateEvaluationError(
                "reconciliation requires an anchored started trial"
            )
        plan = reservations[0]["payload"]["plan"]
        request = starts[0]["payload"]["request"]
        matching_starts = [
            e
            for e in evolver.ledger.events()
            if e["event_type"] == "paired.trial_started"
            and all(
                e["payload"]["request"].get(key) == request.get(key)
                for key in ("identity", "check", "repetition")
            )
        ]
        if len(matching_starts) != 1:
            raise CandidateEvaluationError(
                "journal attribution is ambiguous across trials"
            )
        evaluator = plan["evaluator"]
        if evaluator.get("model_mode") != "host-budgeted":
            raise CandidateEvaluationError("trial has no host model journal contract")
        path = Path(journal_path).resolve()
        if not path.is_relative_to(Path(evaluator["journal_root"]).resolve()):
            raise CandidateEvaluationError("journal is outside the frozen host root")
        rows = EvolutionLedger(path).events()
        bindings = [r["payload"] for r in rows if r["payload"].get("status") == "bound"]
        task = next(
            t
            for t in evaluator["tasks"]
            if t["task_id"] == request["check"]["check_id"]
        )
        expected = {
            "status": "bound",
            "source": request["identity"],
            "task": task,
            "repetition": request["repetition"],
            "outer_cost_limit_usd": plan["max_trial_cost_usd"],
            "gateway_digest": payload_digest(evaluator["model_gateway"]),
        }
        if bindings != [expected]:
            raise CandidateEvaluationError("journal belongs to another trial")
        evidence = rows[-1]["payload"].get("model_evidence") if rows else None
        if not evidence or not evidence.get("cost_complete"):
            raise CandidateEvaluationError(
                "journal cannot resolve unknown Provider usage"
            )
        known = _cost(evidence["known_estimated_cost_usd"])
        result = {
            "index": index,
            "journal_path": str(path),
            "journal_digest": rows[-1]["digest"],
            "known_estimated_cost_usd": str(known),
            "quality_recovered": False,
            "released_cost_usd": "0",
            "plan_digest": starts[0]["payload"]["plan_digest"],
        }
        previous = [
            e
            for e in events
            if e["event_type"] == "paired.reconciled" and e["payload"]["index"] == index
        ]
        if previous:
            if len(previous) != 1 or previous[0]["payload"] != result:
                raise CandidateEvaluationError("reconciliation evidence changed")
            return result
        evolver.ledger._append_protocol(
            "paired.reconciled",
            actor=actor,
            candidate_id=candidate_id,
            payload=result,
            authority=_PROTOCOL_AUTHORITY,
        )
        return result
