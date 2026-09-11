"""Bounded candidate search over a fixed baseline, never automatic activation."""

from dataclasses import asdict, dataclass
import json
import re

from ..atomic_io import atomic_replace_unlocked, file_lock
from .contracts import CandidateProposal
from .evaluation import CandidateEvaluationError, payload_digest
from .gates import TerminationTracker
from .workspace import _git


@dataclass(frozen=True)
class SearchLimits:
    max_rounds: int = 5
    patience: int = 3
    max_generation_errors: int = 2

    def __post_init__(self):
        if any(
            type(value) is not int or value < 1
            for value in (self.max_rounds, self.patience, self.max_generation_errors)
        ):
            raise ValueError("search limits must be positive integers")


def run_search(
    evolver,
    repo_root,
    *,
    run_id,
    base_commit,
    propose,
    deterministic_checks,
    deterministic_evaluator,
    paired_checks,
    paired_evaluator,
    gate,
    run_budget,
    max_trial_cost_usd=0.0,
    repetitions=1,
    limits=None,
    resume=False,
):
    """propose(context) returns a CandidateProposal; context contains train history.

    Generation is trusted host code and its own model spending must be bounded by
    its caller. This function owns candidate evaluation budgets, not generation
    costs. Explicit resume accepts only finished runs or completed-round boundaries.
    """
    limits = limits or SearchLimits()
    if not isinstance(limits, SearchLimits) or not callable(propose):
        raise TypeError("search requires typed limits and a proposal callback")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("unsafe search run id")
    deterministic_checks, paired_checks = (
        tuple(deterministic_checks),
        tuple(paired_checks),
    )
    if not deterministic_checks or not paired_checks:
        raise ValueError("search requires deterministic and paired checks")
    if any(
        item.task_id not in {check.check_id for check in paired_checks}
        for item in getattr(propose, "evidence", ())
    ):
        raise ValueError("proposal failure evidence must belong to training tasks")
    base_commit = _git(repo_root, "rev-parse", base_commit + "^{commit}")

    def current_plan():
        return {
            "deterministic": deterministic_evaluator.descriptor(repo_root),
            "paired": paired_evaluator.descriptor(repo_root),
            "checks": [asdict(check) for check in deterministic_checks],
            "paired_checks": [asdict(check) for check in paired_checks],
            "gate": vars(gate),
            "budget": asdict(run_budget),
            "trial_cost_limit": max_trial_cost_usd,
            "repetitions": repetitions,
            "termination": asdict(limits),
            "proposal_strategy": propose.descriptor()
            if callable(getattr(propose, "descriptor", None))
            else None,
        }

    plan = json.loads(json.dumps(current_plan(), allow_nan=False))
    plan_digest = payload_digest(plan)
    root = evolver.ledger.path.parent / "searches" / run_id
    with file_lock(evolver.ledger.path.parent / ".lock" / "search.lock"):
        existing = root.exists()
        if existing and not resume:
            raise FileExistsError(str(root))
        if not existing:
            root.mkdir(parents=True, exist_ok=False)
        tracker = TerminationTracker(
            patience=limits.patience,
            max_rounds=limits.max_rounds,
            max_consecutive_errors=limits.max_generation_errors,
        )
        state = {
            "schema": "repoagent.evolution-search/v1",
            "run_id": run_id,
            "base_commit": base_commit,
            "status": "running",
            "stop_reason": None,
            "rounds": [],
            "qualified_candidates": [],
            "plan": plan,
        }
        if existing:
            history = [
                e
                for e in evolver.ledger.events()
                if e["event_type"].startswith("search.")
                and e["payload"].get("run_id") == run_id
            ]
            if not history:
                raise CandidateEvaluationError("search has no anchored recovery state")
            restored = history[-1]["payload"]["state"]
            if restored["base_commit"] != base_commit or restored["plan"] != plan:
                raise CandidateEvaluationError("search resume configuration changed")
            if restored["status"] == "finished":
                return restored
            if any(
                row["status"] not in {"qualified", "rejected", "generation_failed"}
                for row in restored["rounds"]
            ):
                raise CandidateEvaluationError(
                    "search has an uncertain round; automatic rerun refused"
                )
            if any(
                e["event_type"] == "sealed.started" and e["payload"]["run_id"] == run_id
                for e in evolver.ledger.events()
            ):
                raise CandidateEvaluationError("search is frozen for sealed validation")
            state = restored
            state.update(status="running", stop_reason=None)
            state.pop("error_type", None)
            for row in state["rounds"]:
                tracker.record(
                    promoted=row["status"] == "qualified",
                    errored=row["status"] == "generation_failed",
                )

        def persist(event):
            atomic_replace_unlocked(
                root / "state.json",
                json.dumps(state, sort_keys=True, indent=2, allow_nan=False) + "\n",
            )
            evolver.ledger.append(
                "search." + event,
                actor="evolution-search",
                payload={"run_id": run_id, "state": json.loads(json.dumps(state))},
            )

        persist("resumed" if existing else "started")
        seen = {row["candidate_id"] for row in state["rounds"] if "candidate_id" in row}
        try:
            while not tracker.decision()[0]:
                index = len(state["rounds"])
                row = {"round": index, "status": "generating"}
                state["rounds"].append(row)
                persist("round_started")
                # Pass a detached view; no sealed tasks or mutable controller state.
                context = {
                    "base_commit": base_commit,
                    "round": index,
                    "history": json.loads(json.dumps(state["rounds"][:-1])),
                }
                try:
                    proposal = propose(context)
                    if not isinstance(proposal, CandidateProposal):
                        raise TypeError(
                            "proposal callback must return CandidateProposal"
                        )
                    if proposal.manifest.base_commit != base_commit:
                        raise ValueError("search candidate changed frozen baseline")
                    candidate_id = proposal.manifest.candidate_id
                    if candidate_id in seen:
                        raise ValueError("search repeated a candidate id")
                    seen.add(candidate_id)
                except Exception as exc:
                    row.update(
                        status="generation_failed", error_type=type(exc).__name__
                    )
                    tracker.record(promoted=False, errored=True)
                    persist("round_completed")
                    continue
                row.update(candidate_id=candidate_id, status="deterministic")
                if payload_digest(current_plan()) != plan_digest:
                    raise CandidateEvaluationError(
                        "search evaluation configuration changed"
                    )
                persist("candidate_generated")
                deterministic = evolver.evaluate_candidate(
                    repo_root, proposal, deterministic_checks, deterministic_evaluator
                )
                row["deterministic"] = deterministic.to_dict()
                if any(item.status == "error" for item in deterministic.observations):
                    raise CandidateEvaluationError(
                        "deterministic execution requires review"
                    )
                passed = False
                if deterministic.passed:
                    row["status"] = "paired"
                    persist("deterministic_passed")
                    paired = evolver.evaluate_paired_checks(
                        repo_root,
                        proposal,
                        paired_checks,
                        paired_evaluator,
                        gate=gate,
                        run_budget=run_budget,
                        max_trial_cost_usd=max_trial_cost_usd,
                        repetitions=repetitions,
                    )
                    row["paired"] = paired.to_dict()
                    if any(item.status == "error" for item in paired.observations):
                        raise CandidateEvaluationError(
                            "paired execution requires review"
                        )
                    passed = paired.passed
                    if passed:
                        state["qualified_candidates"].append(candidate_id)
                row["status"] = "qualified" if passed else "rejected"
                tracker.record(promoted=passed)
                persist("round_completed")
            state.update(status="finished", stop_reason=tracker.decision()[1])
            persist("finished")
        except BaseException as exc:
            state.update(
                status="blocked",
                stop_reason="execution_requires_review",
                error_type=type(exc).__name__,
            )
            persist("blocked")
            raise
        return json.loads(json.dumps(state))
