"""Durable paired-check execution with conservative run-wide reservations."""

from dataclasses import asdict, dataclass
from decimal import Decimal
import json
import math

from ..atomic_io import atomic_replace_unlocked, file_lock
from .evaluation import (
    CandidateEvaluationError,
    DockerCandidateEvaluator,
    decision_from_receipt,
    evaluation_plan,
    payload_digest,
)
from .gates import GateDecision, PairedPromotionGate
from .ledger import _PROTOCOL_AUTHORITY
from .measurements import PairedMeasurement
from .workspace import _git, candidate_ref, verify_candidate_commit


@dataclass(frozen=True)
class EvolutionRunBudget:
    max_pairs: int = 50
    max_estimated_cost_usd: float = 5.0

    def __post_init__(self):
        if type(self.max_pairs) is not int or self.max_pairs < 1:
            raise ValueError("run pair budget must be a positive integer")
        _cost(self.max_estimated_cost_usd)


def _cost(value):
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise ValueError("cost must be finite and nonnegative")
    return Decimal(str(value))


class DockerPairedCheckEvaluator(DockerCandidateEvaluator):
    """Binary test grading on fresh checkouts; no Agent or Provider invocation."""

    def descriptor(self, repo_root):
        return {
            **super().descriptor(repo_root),
            "paired_grader": "check-exit-status/v1",
        }

    def run_trial(
        self, repo_root, identity, check, repetition, descriptor, *, cost_limit_usd
    ):
        rows = self.evaluate(repo_root, identity, [check], descriptor)
        if len(rows) != 1 or rows[0]["check_id"] != check.check_id:
            raise CandidateEvaluationError("paired check result does not match task")
        status = rows[0]["status"]
        if status not in {"pass", "fail", "error"}:
            raise CandidateEvaluationError("unknown paired check result")
        return {
            "status": "infrastructure_error" if status == "error" else "completed",
            "score": None if status == "error" else float(status == "pass"),
            "passed": None if status == "error" else status == "pass",
            "estimated_cost_usd": 0.0,
            "raw": {"results": rows, "grader": "check-exit-status/v1"},
        }


def _read_receipt(path, digest):
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if payload_digest(receipt) != digest:
            raise ValueError("digest mismatch")
        return receipt
    except (OSError, ValueError) as exc:
        raise CandidateEvaluationError(
            "paired prerequisite or trial evidence changed/missing"
        ) from exc


def _deterministic_evidence(evolver, identity):
    events = [
        event
        for event in evolver.ledger.events()
        if event["candidate_id"] == identity["candidate_id"]
    ]
    complete = [
        event for event in events if event["event_type"] == "evaluation.completed"
    ]
    gates = [
        event
        for event in events
        if event["event_type"] == "gate.evaluated"
        and event["payload"].get("stage") == "deterministic"
    ]
    if len(complete) != 1 or len(gates) != 1 or not gates[0]["payload"].get("passed"):
        raise CandidateEvaluationError(
            "paired execution requires completed passing deterministic evidence"
        )
    receipt = _read_receipt(
        evolver.ledger.path.parent
        / "evidence"
        / f"{identity['candidate_id']}-deterministic.json",
        complete[0]["payload"]["receipt_digest"],
    )
    if (
        receipt["plan"]["identity"] != identity
        or decision_from_receipt(receipt).to_dict() != gates[0]["payload"]
        or complete[0]["sequence"] >= gates[0]["sequence"]
    ):
        raise CandidateEvaluationError(
            "deterministic prerequisite identity or gate differs"
        )
    return gates[0]["digest"]


def _append(evolver, kind, candidate_id, payload, actor):
    return evolver.ledger._append_protocol(
        kind,
        candidate_id=candidate_id,
        actor=actor,
        payload=payload,
        authority=_PROTOCOL_AUTHORITY,
    )


def _measurement(receipt):
    request, result = receipt["request"], receipt["result"]
    return PairedMeasurement(
        task_id=request["check"]["check_id"],
        repetition=request["repetition"],
        arm=request["arm"],
        receipt_digest=payload_digest(receipt),
        status=result["status"],
        score=result.get("score"),
        passed=result.get("passed"),
        estimated_cost_usd=result.get("estimated_cost_usd"),
    )


def execute_paired_checks(
    evolver,
    repo_root,
    proposal,
    checks,
    evaluator,
    *,
    repetitions,
    gate,
    run_budget,
    max_trial_cost_usd,
    fired_tasks,
    actor,
):
    checks = tuple(checks)
    check_plan = evaluation_plan({}, checks, {})
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError("paired repetitions must be a positive integer")
    if not isinstance(gate, PairedPromotionGate) or not isinstance(
        run_budget, EvolutionRunBudget
    ):
        raise TypeError("paired execution requires a promotion gate and run budget")
    limit = _cost(max_trial_cost_usd)
    pair_n = len(checks) * repetitions
    reserved_cost = limit * (pair_n * 2)
    if pair_n > proposal.manifest.budget.max_trials or reserved_cost > _cost(
        proposal.manifest.budget.max_estimated_cost_usd
    ):
        raise CandidateEvaluationError(
            "paired plan exceeds candidate budget before execution"
        )
    if pair_n > run_budget.max_pairs or reserved_cost > _cost(
        run_budget.max_estimated_cost_usd
    ):
        raise CandidateEvaluationError(
            "paired plan exceeds run budget before execution"
        )
    if gate.max_estimated_cost_usd is not None and reserved_cost > _cost(
        gate.max_estimated_cost_usd
    ):
        raise CandidateEvaluationError(
            "paired plan exceeds gate cost budget before execution"
        )
    task_ids = [check.check_id for check in checks]
    fired = None if fired_tasks is None else sorted(set(fired_tasks))
    # Validate attribution and gate input before any backend or Git work.
    gate.run_measurements(
        [],
        task_ids=task_ids,
        repetitions=repetitions,
        candidate_budget=proposal.manifest.budget,
        fired_tasks=fired,
    )
    identity = evolver.materialize_candidate(repo_root, proposal)
    control = {
        "commit_sha": identity["base_commit"],
        "tree_sha": _git(repo_root, "rev-parse", identity["base_commit"] + "^{tree}"),
    }
    candidate_id = identity["candidate_id"]
    root = evolver.ledger.path.parent
    with file_lock(root / ".lock" / "paired-execution.lock"):
        prerequisite = _deterministic_evidence(evolver, identity)
        policy = {
            name: getattr(gate, name)
            for name in (
                "min_unique_tasks",
                "min_repetitions",
                "min_mean_lift",
                "max_estimated_cost_usd",
                "require_ci_lower",
            )
        }
        plan = {
            "schema": "repoagent.paired-check-plan/v1",
            "candidate": identity,
            "control": control,
            "checks": check_plan["checks"],
            "repetitions": repetitions,
            "evaluator": evaluator.descriptor(repo_root),
            "gate_policy": policy,
            "fired_tasks": fired,
            "max_trial_cost_usd": max_trial_cost_usd,
            "deterministic_event_digest": prerequisite,
            "run_budget": asdict(run_budget),
        }
        # Canonical JSON also prevents caller-owned nested descriptor mutation.
        plan = json.loads(json.dumps(plan, allow_nan=False))
        plan_digest = payload_digest(plan)
        events = evolver.ledger.events()
        reservations = [
            event for event in events if event["event_type"] == "paired.reserved"
        ]
        own = [event for event in reservations if event["candidate_id"] == candidate_id]
        if any(
            event["payload"]["plan"]["run_budget"] != plan["run_budget"]
            for event in reservations
        ):
            raise CandidateEvaluationError(
                "run budget is frozen for this evolution ledger"
            )
        if own:
            if (
                len(own) != 1
                or own[0]["payload"]["plan"] != plan
                or own[0]["payload"]["plan_digest"] != plan_digest
            ):
                raise CandidateEvaluationError("paired evaluation plan changed")
        else:
            if any(
                event["event_type"] == "paired.budget_violation" for event in events
            ):
                raise CandidateEvaluationError(
                    "run budget violation requires operator reconciliation"
                )
            used_pairs = sum(event["payload"]["pair_n"] for event in reservations)
            used_cost = sum(
                (
                    Decimal(event["payload"]["reserved_cost_usd"])
                    for event in reservations
                ),
                Decimal(0),
            )
            settled = [event for event in events if event["event_type"] == "paired.settled"]
            if len({event["candidate_id"] for event in settled}) != len(settled):
                raise CandidateEvaluationError("duplicate cost settlement")
            reservation_digests = {event["digest"] for event in reservations}
            for event in settled:
                if event["payload"]["reservation_digest"] not in reservation_digests:
                    raise CandidateEvaluationError("cost settlement has no reservation")
                used_cost -= Decimal(event["payload"]["released_cost_usd"])
            if (
                used_pairs + pair_n > run_budget.max_pairs
                or used_cost + reserved_cost > _cost(run_budget.max_estimated_cost_usd)
            ):
                raise CandidateEvaluationError("cumulative paired run budget exhausted")
            if any(
                event["event_type"] == "gate.evaluated"
                and event["candidate_id"] == candidate_id
                and event["payload"].get("stage") == "paired"
                for event in events
            ):
                raise CandidateEvaluationError(
                    "candidate already has unrelated paired evidence"
                )
            _append(
                evolver,
                "paired.reserved",
                candidate_id,
                {
                    "plan": plan,
                    "plan_digest": plan_digest,
                    "pair_n": pair_n,
                    "reserved_cost_usd": str(reserved_cost),
                },
                actor,
            )
        measurements = []
        requests = [
            dict(
                index=index,
                arm=arm,
                repetition=rep,
                check=asdict(check),
                identity=control if arm == "control" else identity,
            )
            for index, (check, rep, arm) in enumerate(
                (check, rep, arm)
                for check in checks
                for rep in range(repetitions)
                for arm in ("control", "treatment")
            )
        ]
        for request in requests:
            events = [
                event
                for event in evolver.ledger.events()
                if event["candidate_id"] == candidate_id
            ]
            index = request["index"]
            started = [
                event
                for event in events
                if event["event_type"] == "paired.trial_started"
                and event["payload"]["index"] == index
            ]
            completed = [
                event
                for event in events
                if event["event_type"] == "paired.trial_completed"
                and event["payload"]["index"] == index
            ]
            expected_start = {
                "index": index,
                "request": request,
                "plan_digest": plan_digest,
            }
            artifact = root / "evidence" / f"{candidate_id}-paired-{index}.json"
            if started and (
                len(started) != 1 or started[0]["payload"] != expected_start
            ):
                raise CandidateEvaluationError("paired trial journal differs from plan")
            if completed:
                if (
                    len(completed) != 1
                    or not started
                    or completed[0]["sequence"] <= started[0]["sequence"]
                ):
                    raise CandidateEvaluationError(
                        "paired trial journal is inconsistent"
                    )
                receipt = _read_receipt(
                    artifact, completed[0]["payload"]["receipt_digest"]
                )
                if (
                    receipt["request"] != request
                    or receipt["plan_digest"] != plan_digest
                ):
                    raise CandidateEvaluationError(
                        "paired trial receipt identity differs"
                    )
            elif started:
                raise CandidateEvaluationError(
                    "paired trial was interrupted; automatic rerun refused"
                )
            else:
                _verify_source(repo_root, proposal, identity)
                if any(
                    event["event_type"] == "paired.budget_violation"
                    for event in evolver.ledger.events()
                ):
                    raise CandidateEvaluationError(
                        "run budget violation requires operator reconciliation"
                    )
                _append(
                    evolver, "paired.trial_started", candidate_id, expected_start, actor
                )
                try:
                    result = evaluator.run_trial(
                        repo_root,
                        dict(request["identity"]),
                        checks[index // (repetitions * 2)],
                        request["repetition"],
                        json.loads(json.dumps(plan["evaluator"])),
                        cost_limit_usd=max_trial_cost_usd,
                    )
                    result = json.loads(json.dumps(result, allow_nan=False))
                    if not isinstance(result, dict) or not isinstance(
                        result.get("raw"), dict
                    ):
                        raise CandidateEvaluationError(
                            "paired trial requires structured raw evidence"
                        )
                    receipt = {
                        "schema": "repoagent.paired-check-receipt/v1",
                        "request": request,
                        "plan_digest": plan_digest,
                        "result": result,
                    }
                    measurement = _measurement(receipt)
                    payload_digest(receipt)
                except Exception as exc:
                    receipt = {
                        "schema": "repoagent.paired-check-receipt/v1",
                        "request": request,
                        "plan_digest": plan_digest,
                        "result": {
                            "status": "infrastructure_error",
                            "estimated_cost_usd": None,
                            "raw": {"error_type": type(exc).__name__},
                        },
                    }
                _verify_source(repo_root, proposal, identity)
                atomic_replace_unlocked(
                    artifact,
                    json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False)
                    + "\n",
                )
                _append(
                    evolver,
                    "paired.trial_completed",
                    candidate_id,
                    {"index": index, "receipt_digest": payload_digest(receipt)},
                    actor,
                )
            measurement = _measurement(receipt)
            measurements.append(measurement)
            if (
                measurement.estimated_cost_usd is not None
                and _cost(measurement.estimated_cost_usd) > limit
            ):
                if not any(
                    event["event_type"] == "paired.budget_violation"
                    and event["payload"].get("receipt_digest")
                    == measurement.receipt_digest
                    for event in evolver.ledger.events()
                ):
                    _append(
                        evolver,
                        "paired.budget_violation",
                        candidate_id,
                        {"receipt_digest": measurement.receipt_digest, "index": index},
                        actor,
                    )
                raise CandidateEvaluationError(
                    "paired evaluator exceeded reserved trial cost"
                )
            if (
                measurement.status != "completed"
                or measurement.estimated_cost_usd is None
            ):
                break
        _verify_source(repo_root, proposal, identity)
        decision = gate.run_measurements(
            measurements,
            task_ids=task_ids,
            repetitions=repetitions,
            candidate_budget=proposal.manifest.budget,
            fired_tasks=fired,
        )
        # Bind source/configuration even if an invalid arm has no usable score.
        decision = GateDecision(
            stage="paired",
            passed=decision.passed,
            evidence_digest=payload_digest(
                {
                    "plan_digest": plan_digest,
                    "measurement_digest": decision.evidence_digest,
                }
            ),
            observations=decision.observations,
            metrics=json.loads(
                json.dumps(
                    {
                        **dict(decision.metrics),
                        "plan_digest": plan_digest,
                        "control_commit_sha": control["commit_sha"],
                        "candidate_commit_sha": identity["commit_sha"],
                        "reserved_pair_n": pair_n,
                        "reserved_cost_usd": str(reserved_cost),
                    },
                    allow_nan=False,
                )
            ),
        )
        gates = [
            event
            for event in evolver.ledger.events()
            if event["candidate_id"] == candidate_id
            and event["event_type"] == "gate.evaluated"
            and event["payload"].get("stage") == "paired"
        ]
        if gates:
            if len(gates) != 1 or gates[0]["payload"] != decision.to_dict():
                raise CandidateEvaluationError("paired gate evidence differs")
        else:
            evolver.record_gate(candidate_id, decision, actor=actor)
        return decision


def _verify_source(repo_root, proposal, identity):
    verify_candidate_commit(repo_root, proposal, identity["commit_sha"])
    if (
        _git(repo_root, "rev-parse", candidate_ref(identity["candidate_id"]))
        != identity["commit_sha"]
    ):
        raise CandidateEvaluationError(
            "candidate reference changed during paired evaluation"
        )
