"""Bounded single-WHY search with per-parent frozen training measurements."""

from dataclasses import asdict, dataclass, replace
from decimal import Decimal
import hashlib
import json
import re

from ..atomic_io import atomic_replace_unlocked, file_lock
from .contracts import BenchmarkTarget, CandidateBudget, FailureEvidence
from .evaluation import CandidateCheck, CandidateEvaluationError, payload_digest
from .focused_fisher import FocusedBenchmarkEvaluator, FocusedFisherGate, TaskTrialSummary, _validity
from .gates import TerminationTracker
from .model_budget import BudgetedEvaluationClient
from .model_proposer import ModelCandidateProposer
from .module_repair import ModuleRepairProtocol
from .search import SearchLimits
from .workspace import verify_benchmark_target


@dataclass(frozen=True)
class RepairTask:
    task_id: str
    why: str
    description: str
    why_definition: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip() for value in asdict(self).values()):
            raise ValueError("repair task metadata must be nonempty text")


def _copy(value):
    return json.loads(json.dumps(value, allow_nan=False))


def select_sentinels(baseline, candidate_id, count=12):
    """Rotate stable/fragile pools from cold start, not the changing parent."""
    stable = sorted(tid for tid, row in baseline.items() if row.passes == row.attempts and row.passes)
    fragile = sorted(tid for tid, row in baseline.items() if 0 < row.passes < row.attempts)

    def pick(pool, n, salt):
        if not pool or n <= 0:
            return []
        start = int(hashlib.sha256(f"{salt}:{candidate_id}".encode()).hexdigest(), 16) % len(pool)
        return [pool[(start + i) % len(pool)] for i in range(min(n, len(pool)))]

    return tuple(pick(stable, count - count // 2, "stable") + pick(fragile, count // 2, "fragile"))


def run_focused_search(evolver, repo_root, *, run_id, target, tasks, public_names,
                       backend, client, gate=None, limits=None, candidate_budget=None,
                       max_scoring_trials=200, sentinel_count=12, resume=False, sealed_plan=None):
    """One lexicographically selected WHY and one candidate per round.

    backend.score follows FocusedBenchmarkEvaluator. backend.failure_cases takes
    (repo_root, identity, task_ids, k, phase) and returns only requested training
    failure cases from that retained scoring phase. Backend descriptors must pin
    graders and execution settings. No test tasks or activation APIs are used.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
        raise ValueError("unsafe focused search run id")
    if not isinstance(target, BenchmarkTarget) or len(target.mutable_paths) != 1:
        raise ValueError("focused search requires exactly one benchmark module")
    if not isinstance(client, BudgetedEvaluationClient):
        raise TypeError("focused search requires the budgeted model gateway")
    tasks, public_names = tuple(tasks), tuple(public_names)
    if (not tasks or any(not isinstance(task, RepairTask) for task in tasks)
            or len({task.task_id for task in tasks}) != len(tasks)):
        raise ValueError("focused search requires unique typed training tasks")
    definitions = {}
    for task in tasks:
        if definitions.setdefault(task.why, task.why_definition) != task.why_definition:
            raise ValueError("conflicting WHY definitions")
    if not public_names or any(not isinstance(name, str) or not name.isidentifier() for name in public_names):
        raise ValueError("focused search requires public function names")
    if type(max_scoring_trials) is not int or max_scoring_trials < 1:
        raise ValueError("invalid scoring trial budget")
    if type(sentinel_count) is not int or sentinel_count < 0:
        raise ValueError("invalid sentinel count")
    gate, limits = gate or FocusedFisherGate(k=2), limits or SearchLimits()
    budget = candidate_budget or CandidateBudget(max_changed_bytes=32768)
    if not isinstance(gate, FocusedFisherGate) or not isinstance(limits, SearchLimits) or not isinstance(budget, CandidateBudget):
        raise TypeError("focused search requires typed policy, limits and budget")
    verify_benchmark_target(repo_root, target)
    ids = tuple(task.task_id for task in tasks)
    if sealed_plan is not None:
        from .focused_finalization import validate_focused_sealed_plan
        sealed_plan = validate_focused_sealed_plan(sealed_plan, ids)
    pricing = client.pricing
    call_ceiling = (
        Decimal(client.limits.max_input_tokens) * Decimal(str(max(
            pricing.input_per_1m_usd, pricing.cache_read_per_1m_usd, pricing.cache_write_per_1m_usd)))
        + Decimal(client.limits.max_output_tokens) * Decimal(str(pricing.output_per_1m_usd))
    ) / Decimal(1_000_000)

    def current_plan():
        plan = {"schema": "repoagent.focused-search-plan/v1", "target": target.to_dict(),
            "tasks": [asdict(task) for task in tasks], "public_names": public_names,
            "gate": gate.descriptor(), "limits": asdict(limits), "candidate_budget": asdict(budget),
            "backend": backend.descriptor(repo_root), "model": client.descriptor(),
            "max_scoring_trials": max_scoring_trials, "why_selection": "lexicographic-first",
            "sentinel_count": sentinel_count,
            "candidates_per_round": 1, "max_parse_retries": 2}
        if sealed_plan is not None:
            plan["sealed_plan"] = sealed_plan
        return _copy(plan)

    plan = current_plan()
    root = evolver.ledger.path.parent / "focused-searches" / run_id
    with file_lock(evolver.ledger.path.parent / ".lock" / "focused-search.lock"):
        history = [e for e in evolver.ledger.events() if e["event_type"].startswith("focused_search.")
                   and e["payload"].get("run_id") == run_id]
        if history:
            if not resume:
                raise FileExistsError(str(root))
            state = _copy(history[-1]["payload"]["state"])
            if state["plan"] != plan:
                raise CandidateEvaluationError("focused search resume configuration changed")
            if state["status"] == "finished":
                return state
            if state["phase"] != "ready":
                raise CandidateEvaluationError("focused search has uncertain work; automatic replay refused")
        else:
            if root.exists():
                raise CandidateEvaluationError("focused search has no anchored state")
            root.mkdir(parents=True)
            state = {"schema": "repoagent.focused-search/v1", "plan": plan, "run_id": run_id,
                "status": "running", "phase": "cold_start", "rounds": [],
                "parent_commit": target.base_commit, "parent_results": None, "cold_results": None,
                "parent_phase": "cold_start",
                "reserved_scoring_trials": 0, "reserved_model_calls": 0,
                "reserved_generation_usd": "0", "stop_reason": None}

        def persist(event):
            # Ledger is authoritative; the JSON file is a convenience projection.
            evolver.ledger.append("focused_search." + event, actor="focused-search",
                payload={"run_id": run_id, "state": _copy(state)})
            atomic_replace_unlocked(root / "state.json", json.dumps(state, indent=2, allow_nan=False) + "\n")

        def unchanged():
            if current_plan() != plan:
                raise CandidateEvaluationError("focused search configuration drifted")

        def finish(reason):
            state.update(status="finished", stop_reason=reason, phase="finished")
            persist("finished")
            return _copy(state)

        def reserve_trials(count):
            if state["reserved_scoring_trials"] + count > max_scoring_trials:
                raise CandidateEvaluationError("focused scoring budget exhausted")
            state["reserved_scoring_trials"] += count
            persist("scoring_reserved")

        state["status"] = "running"
        persist("resumed" if history else "started")
        tracker = TerminationTracker(max_rounds=limits.max_rounds, patience=limits.patience,
                                     max_consecutive_errors=limits.max_generation_errors)
        for row in state["rounds"]:
            tracker.record(promoted=row["status"] == "qualified", errored=row["status"] == "generation_failed")
        try:
            if state["parent_results"] is None:
                reserve_trials(len(ids) * gate.k)
                unchanged()
                measured = backend.score(repo_root, {"commit_sha": target.base_commit}, ids, gate.k, "cold_start")
                if set(measured) != set(ids) or _validity(measured, ids, gate.k)["status"] != "measured":
                    raise CandidateEvaluationError("cold start measurement incomplete or invalid")
                unchanged()
                state["parent_results"] = {tid: asdict(row) for tid, row in measured.items()}
                state["cold_results"] = _copy(state["parent_results"])
                state["phase"] = "ready"
                persist("cold_start_completed")

            while not tracker.decision()[0]:
                unchanged()
                parent = {tid: TaskTrialSummary(**row) for tid, row in state["parent_results"].items()}
                failing = [task for task in tasks if parent[task.task_id].passes < parent[task.task_id].attempts]
                if not failing:
                    return finish("no_training_failures")
                why = sorted({task.why for task in failing})[0]
                selected = [task for task in failing if task.why == why]
                focused = tuple(sorted(task.task_id for task in selected))
                cold = {tid: TaskTrialSummary(**row) for tid, row in state["cold_results"].items()}
                sentinel_n = len(select_sentinels(cold, "reservation", sentinel_count))
                # Reserve the entire possible probe+confirm before generating.
                trial_ceiling = (min(len(ids), len(focused) + sentinel_n) + len(ids)) * gate.k
                generation_ceiling = call_ceiling * 3
                if trial_ceiling > budget.max_trials or generation_ceiling > Decimal(str(budget.max_estimated_cost_usd)):
                    return finish("candidate_budget_exhausted")
                if state["reserved_scoring_trials"] + trial_ceiling > max_scoring_trials:
                    return finish("scoring_budget_exhausted")
                if (state["reserved_model_calls"] + 3 > client.limits.max_calls
                        or Decimal(state["reserved_generation_usd"]) + generation_ceiling > Decimal(str(client.limits.max_estimated_cost_usd))):
                    return finish("generation_budget_exhausted")
                index = len(state["rounds"])
                prefix = f"r{index:04d}"
                child_target = replace(target, base_commit=state["parent_commit"])
                state["phase"] = "diagnosing"
                persist("diagnosis_started")
                failures = backend.failure_cases(repo_root, {"commit_sha": state["parent_commit"]},
                    focused, gate.k, state["parent_phase"])
                if set(failures) - set(focused):
                    raise CandidateEvaluationError("failure cases contain nontraining task ids")
                failure_records = {task.task_id: {"description": task.description,
                    "cases": failures.get(task.task_id) or [{"detail": "no case detail recorded"}]} for task in selected}
                protocol = ModuleRepairProtocol(target.mutable_paths[0], public_names, why,
                    definitions[why], json.dumps(failure_records, allow_nan=False))
                evidence = [FailureEvidence(f"{prefix}-{i}", task.task_id, why, task.description,
                    payload_digest({"parent": state["parent_commit"], "measurement": asdict(parent[task.task_id]),
                                    "failure": failure_records[task.task_id]})) for i, task in enumerate(selected)]
                unchanged()
                row = {"round": index, "why": why, "focused_ids": list(focused),
                       "parent_commit": state["parent_commit"], "status": "generating"}
                state["rounds"].append(row)
                state["reserved_model_calls"] += 3
                state["reserved_generation_usd"] = str(Decimal(state["reserved_generation_usd"]) + generation_ceiling)
                state["phase"] = "generating"
                reserve_trials(trial_ceiling)
                (root / prefix).mkdir(exist_ok=False)
                try:
                    proposer = ModelCandidateProposer(repo_root, base_commit=child_target.base_commit,
                        label="benchmark", paths=child_target.mutable_paths, evidence=evidence,
                        client=client, journal_directory=root / prefix / "model", candidate_budget=budget,
                        benchmark_target=child_target, repair_protocol=protocol)
                    proposal = proposer({"base_commit": child_target.base_commit})
                except Exception as exc:
                    row.update(status="generation_failed", error_type=type(exc).__name__)
                    tracker.record(promoted=False, errored=True)
                    state["phase"] = "ready"
                    persist("round_completed")
                    continue
                unchanged()
                row["candidate_id"] = proposal.manifest.candidate_id
                sentinels = select_sentinels(cold, proposal.manifest.candidate_id, sentinel_count)
                row["sentinel_ids"] = list(sentinels)
                row["status"], state["phase"] = "evaluating", "evaluating"
                persist("evaluation_started")

                class ParentBackend:
                    def descriptor(self, root):
                        return {"backend": backend.descriptor(root), "prefix": prefix,
                                "parent": child_target.base_commit,
                                "parent_measurements": {tid: asdict(value) for tid, value in parent.items()}}

                    def score(self, root, identity, task_ids, k, phase):
                        if phase == "baseline":
                            return {tid: parent[tid] for tid in task_ids}
                        return backend.score(root, identity, task_ids, k, prefix + "_" + phase)

                evaluator = FocusedBenchmarkEvaluator(target=child_target, backend=ParentBackend(),
                    train_ids=ids, focused_ids=focused, sentinel_ids=sentinels, gate=gate)
                decision = evolver.evaluate_candidate(repo_root, proposal,
                    [CandidateCheck("focused_fisher", "focused probe and full confirmation")], evaluator)
                if any(item.status == "error" for item in decision.observations):
                    raise CandidateEvaluationError("focused evaluation requires review")
                artifact = evolver.ledger.path.parent / "evidence" / f"{proposal.manifest.candidate_id}-deterministic.json"
                receipt = json.loads(artifact.read_text())
                if payload_digest(receipt) != decision.evidence_digest:
                    raise CandidateEvaluationError("focused receipt changed")
                row.update(status="qualified" if decision.passed else "rejected",
                           decision=receipt["results"][0]["result"], receipt_digest=decision.evidence_digest)
                if decision.passed:
                    confirmed = [part for part in receipt["results"][0]["measurements"] if part["phase"] == "confirm"]
                    if len(confirmed) != 1:
                        raise CandidateEvaluationError("promoted candidate lacks confirmation")
                    identity = evolver.materialize_candidate(repo_root, proposal)
                    state.update(parent_commit=identity["commit_sha"], parent_results=confirmed[0]["results"],
                                 parent_phase=prefix + "_confirm")
                unchanged()
                tracker.record(promoted=decision.passed)
                state["phase"] = "ready"
                persist("round_completed")
            return finish(tracker.decision()[1])
        except BaseException as exc:
            state.update(status="blocked", error_type=type(exc).__name__)
            persist("blocked")
            raise
