"""One-shot sealed validation after a search has stopped generating candidates."""

import json
import math

from ..atomic_io import file_lock
from .evaluation import CandidateEvaluationError, payload_digest
from .ledger import _PROTOCOL_AUTHORITY
from .sealed import SealedEvaluationVault
from .workspace import _git, candidate_ref


def finalize_search(
    evolver, repo_root, *, run_id, candidate_id, vault, backend, max_estimated_cost_usd
):
    """The isolated backend must enforce the supplied total sealed-call budget.

    Its evaluate method accepts max_estimated_cost_usd in addition to the vault
    arguments. No candidate regeneration, automatic approval or retry occurs.
    """
    if (
        not isinstance(vault, SealedEvaluationVault)
        or getattr(backend, "is_isolated", None) is not True
    ):
        raise TypeError("finalization requires a sealed vault and isolated backend")
    if (
        type(max_estimated_cost_usd) not in {int, float}
        or not math.isfinite(max_estimated_cost_usd)
        or max_estimated_cost_usd < 0
    ):
        raise ValueError("sealed budget must be finite and nonnegative")
    with file_lock(evolver.ledger.path.parent / ".lock" / "search.lock"):
        events = evolver.ledger.events()
        finished = [
            e
            for e in events
            if e["event_type"] == "search.finished" and e["payload"]["run_id"] == run_id
        ]
        if len(finished) != 1:
            raise CandidateEvaluationError("search is not finished")
        state = finished[0]["payload"]["state"]
        if candidate_id not in state["qualified_candidates"]:
            raise CandidateEvaluationError("finalist did not qualify in this search")
        if any(
            e["event_type"] == "sealed.started" and e["payload"]["run_id"] == run_id
            for e in events
        ):
            raise CandidateEvaluationError(
                "search finalist is already frozen; no automatic retry"
            )
        training = {row["check_id"] for row in state["plan"]["paired_checks"]}
        if set(vault.training_task_ids) != training:
            raise CandidateEvaluationError("sealed split does not match search tasks")
        selected = [e for e in events if e["candidate_id"] == candidate_id]
        materialized = [
            e for e in selected if e["event_type"] == "candidate.materialized"
        ]
        paired = [
            e
            for e in selected
            if e["event_type"] == "gate.evaluated" and e["payload"]["stage"] == "paired"
        ]
        if len(materialized) != 1 or not paired or not paired[-1]["payload"]["passed"]:
            raise CandidateEvaluationError("finalist evidence is incomplete")
        commit = materialized[0]["payload"]["commit_sha"]
        if _git(repo_root, "rev-parse", candidate_ref(candidate_id)) != commit:
            raise CandidateEvaluationError("finalist reference changed")
        plan = {
            "run_id": run_id,
            "commit_sha": commit,
            "paired_digest": paired[-1]["payload"]["evidence_digest"],
            "grader_digest": vault.grader_digest,
            "sealed_tasks": list(vault._sealed_task_ids),
            "budget_usd": max_estimated_cost_usd,
            "backend": backend.descriptor(repo_root),
        }

        def record(kind, payload):
            return evolver.ledger._append_protocol(
                kind,
                actor="evolution-finalizer",
                candidate_id=candidate_id,
                payload=payload,
                authority=_PROTOCOL_AUTHORITY,
            )

        record("sealed.started", plan)

        class BoundedBackend:
            is_isolated = True

            def evaluate(self, **kwargs):
                return backend.evaluate(
                    **kwargs, max_estimated_cost_usd=max_estimated_cost_usd
                )

        try:
            receipt = vault.score(
                candidate_id, commit, BoundedBackend(), candidate_workspace=repo_root
            )
            payload = json.loads((vault.root / f"{candidate_id}.json").read_text())
            if payload_digest(payload) != receipt.artifact_digest:
                raise CandidateEvaluationError("sealed artifact changed")
            rows = payload["measurements"]
            expected = set(vault._sealed_task_ids)
            if not isinstance(rows, list) or len(rows) != len(expected):
                raise CandidateEvaluationError("sealed measurements incomplete")
            seen, cost = set(), 0.0
            for row in rows:
                task = row.get("task_id")
                amount = row.get("estimated_cost_usd")
                if (
                    task not in expected
                    or task in seen
                    or type(row.get("passed")) is not bool
                    or type(amount) not in {int, float}
                    or not math.isfinite(amount)
                    or amount < 0
                ):
                    raise CandidateEvaluationError(
                        "sealed measurement invalid or unpriced"
                    )
                seen.add(task)
                cost += amount
            if cost > max_estimated_cost_usd:
                raise CandidateEvaluationError("sealed reported cost exceeded budget")
            if _git(repo_root, "rev-parse", candidate_ref(candidate_id)) != commit:
                raise CandidateEvaluationError("finalist reference changed")
            result = {
                **plan,
                "receipt": receipt.to_dict(),
                "passed": all(row["passed"] for row in rows),
                "estimated_cost_usd": cost,
            }
            record("sealed.completed", result)
            return result
        except BaseException as exc:
            record(
                "sealed.failed", {"run_id": run_id, "error_type": type(exc).__name__}
            )
            raise


def request_finalist_approval(evolver, *, run_id, candidate_id):
    with file_lock(evolver.ledger.path.parent / ".lock" / "search.lock"):
        events = evolver.ledger.events()
        completed = [
            e
            for e in events
            if e["event_type"] == "sealed.completed"
            and e["candidate_id"] == candidate_id
            and e["payload"]["run_id"] == run_id
        ]
        if len(completed) != 1 or completed[0]["payload"]["passed"] is not True:
            raise CandidateEvaluationError(
                "approval requires passing sealed finalization"
            )
        paired = [
            e
            for e in events
            if e["event_type"] == "gate.evaluated"
            and e["candidate_id"] == candidate_id
            and e["payload"]["stage"] == "paired"
        ]
        if (
            not paired
            or not paired[-1]["payload"]["passed"]
            or paired[-1]["payload"]["evidence_digest"]
            != completed[0]["payload"]["paired_digest"]
        ):
            raise CandidateEvaluationError("paired evidence changed after finalization")
        return evolver.approvals.request(
            candidate_id, completed[0]["payload"]["paired_digest"]
        )
