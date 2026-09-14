"""One-way held-out validation and reports for focused training searches."""

import json
import math
from statistics import mean, stdev

from ..atomic_io import atomic_replace_unlocked, file_lock
from .evaluation import CandidateEvaluationError, payload_digest
from .focused_fisher import TaskTrialSummary, _validity
from .ledger import _PROTOCOL_AUTHORITY
from .sealed import SealedEvaluationVault, assert_disjoint_splits
from .workspace import _git, _git_bytes, candidate_ref


def freeze_focused_sealed_plan(repo_root, *, vault, backend, k, max_estimated_cost_usd):
    if not isinstance(vault, SealedEvaluationVault) or getattr(backend, "is_isolated", None) is not True:
        raise TypeError("focused finalization requires an isolated sealed backend and vault")
    plan = {
        "schema": "repoagent.focused-sealed-plan/v1", "tasks": list(vault._sealed_task_ids),
        "grader_digest": vault.grader_digest, "vault_root": str(vault.root), "k": k,
        "max_estimated_cost_usd": max_estimated_cost_usd,
        "backend": backend.descriptor(repo_root),
    }
    return validate_focused_sealed_plan(plan, vault.training_task_ids)


def validate_focused_sealed_plan(plan, training_ids):
    plan = json.loads(json.dumps(plan, allow_nan=False))
    required = {"schema", "tasks", "grader_digest", "vault_root", "k", "max_estimated_cost_usd", "backend"}
    if (set(plan) != required or plan["schema"] != "repoagent.focused-sealed-plan/v1"
            or not isinstance(plan["tasks"], list) or not plan["tasks"]
            or any(not isinstance(tid, str) or not tid for tid in plan["tasks"])
            or len(set(plan["tasks"])) != len(plan["tasks"])
            or type(plan["k"]) is not int or plan["k"] < 1
            or not isinstance(plan["backend"], dict)):
        raise ValueError("invalid focused sealed plan")
    amount = plan["max_estimated_cost_usd"]
    if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
        raise ValueError("sealed budget must be finite and nonnegative")
    assert_disjoint_splits(training_ids, plan["tasks"])
    # Reuse vault validation for the grader digest and storage representation.
    SealedEvaluationVault(plan["vault_root"], training_task_ids=training_ids,
                          sealed_task_ids=plan["tasks"], grader_digest=plan["grader_digest"])
    return plan


def retention_summary(baseline_train, candidate_train, baseline, candidate, task_ids, k):
    if any(_validity(rows, task_ids, k)["status"] != "measured" for rows in (baseline, candidate)):
        raise CandidateEvaluationError("sealed measurements are invalid")
    base_mean = mean(baseline[tid].pass_rate for tid in task_ids)
    candidate_mean = mean(candidate[tid].pass_rate for tid in task_ids)
    differences = [candidate[tid].pass_rate - baseline[tid].pass_rate for tid in task_ids]
    lift = mean(differences)
    se = stdev(differences) / math.sqrt(len(differences)) if len(differences) > 1 else 0.0
    z = lift / se if se else (0.0 if lift == 0 else math.copysign(math.inf, lift))
    train_lift = candidate_train - baseline_train
    return {"baseline_train": baseline_train, "candidate_train": candidate_train,
            "baseline_test": base_mean, "candidate_test": candidate_mean,
            "training_lift": train_lift, "test_lift": lift,
            "retention": (candidate_mean - base_mean) / train_lift if train_lift > 0 else None,
            "sealed_z": z if math.isfinite(z) else str(z),
            "sealed_credited_2sigma": lift > 0 and z >= 2.0,
            "test_task_count": len(task_ids), "repetitions": k}


def finalize_focused_search(evolver, repo_root, *, run_id, vault, backend):
    with file_lock(evolver.ledger.path.parent / ".lock" / "focused-search.lock"):
        events = evolver.ledger.events()
        finished = [event for event in events if event["event_type"] == "focused_search.finished"
                    and event["payload"].get("run_id") == run_id]
        if len(finished) != 1:
            raise CandidateEvaluationError("focused search is not finished")
        state = finished[0]["payload"]["state"]
        stored = state["plan"].get("sealed_plan")
        if stored is None:
            raise CandidateEvaluationError("sealed plan was not frozen before training")
        training = [task["task_id"] for task in state["plan"]["tasks"]]
        if set(vault.training_task_ids) != set(training):
            raise CandidateEvaluationError("sealed training split differs")
        actual = freeze_focused_sealed_plan(repo_root, vault=vault, backend=backend,
            k=stored["k"], max_estimated_cost_usd=stored["max_estimated_cost_usd"])
        if actual != stored:
            raise CandidateEvaluationError("sealed plan changed since training")
        if any(event["event_type"] == "sealed.started" and event["payload"].get("run_id") == run_id for event in events):
            raise CandidateEvaluationError("focused finalization already started; no automatic retry")

        root_commit = state["plan"]["target"]["base_commit"]
        baseline_train = mean(row["passes"] / row["attempts"] for row in state["cold_results"].values())
        eligible = []
        for row in state["rounds"]:
            if row["status"] != "qualified":
                continue
            cid = row["candidate_id"]
            artifact = evolver.ledger.path.parent / "evidence" / f"{cid}-deterministic.json"
            receipt = json.loads(artifact.read_text())
            if (payload_digest(receipt) != row["receipt_digest"]
                    or receipt["results"][0]["result"] != row["decision"]
                    or not row["decision"]["promoted"]):
                raise CandidateEvaluationError("focused training evidence changed")
            materialized = [event for event in events if event["candidate_id"] == cid
                            and event["event_type"] == "candidate.materialized"]
            if len(materialized) != 1 or materialized[0]["payload"] != receipt["plan"]["identity"]:
                raise CandidateEvaluationError("focused finalist identity differs")
            identity = materialized[0]["payload"]
            if _git(repo_root, "rev-parse", candidate_ref(cid)) != identity["commit_sha"]:
                raise CandidateEvaluationError("focused finalist reference changed")
            eligible.append((row, identity))
        selected = max(eligible, key=lambda pair: (pair[0]["decision"]["score"], -pair[0]["round"])) if eligible else None
        cid = selected[0]["candidate_id"] if selected else "candidate_baseline_" + payload_digest({"run_id": run_id})[7:31]
        commit = selected[1]["commit_sha"] if selected else root_commit
        candidate_train = selected[0]["decision"]["score"] if selected else baseline_train
        sources = {arm: {"commit_sha": sha, "tree_sha": _git(repo_root, "rev-parse", sha + "^{tree}")}
                   for arm, sha in (("baseline", root_commit), ("candidate", commit))}
        changed = set(_git_bytes(repo_root, "diff", "--name-only", "-z", root_commit, commit).decode().split("\0")) - {""}
        if not changed <= set(state["plan"]["target"]["mutable_paths"]):
            raise CandidateEvaluationError("finalist changed files outside frozen target")
        plan = {"run_id": run_id, "mode": "focused", "sealed_plan": stored,
                "training_state_digest": payload_digest(state), "sources": sources,
                "candidate_id": cid, "baseline_selected": selected is None}

        def record(kind, payload):
            evolver.ledger._append_protocol(kind, actor="focused-finalizer", candidate_id=cid,
                payload=payload, authority=_PROTOCOL_AUTHORITY)

        record("sealed.started", plan)

        class BoundedBackend:
            is_isolated = True

            def evaluate(self, **kwargs):
                return backend.evaluate(**kwargs, baseline_ref=root_commit, k=stored["k"],
                    max_estimated_cost_usd=stored["max_estimated_cost_usd"])

        try:
            receipt = vault.score(cid, commit, BoundedBackend(), candidate_workspace=repo_root)
            payload = json.loads((vault.root / f"{cid}.json").read_text())
            if payload_digest(payload) != receipt.artifact_digest:
                raise CandidateEvaluationError("sealed artifact changed")
            measurements = payload["measurements"]
            if not isinstance(measurements, list) or len(measurements) != len(stored["tasks"]):
                raise CandidateEvaluationError("sealed paired matrix incomplete")
            arms = {"baseline": {}, "candidate": {}}
            cost = 0.0
            for tid, row in zip(stored["tasks"], measurements):
                if row.get("task_id") != tid or set(row.get("arms", {})) != set(arms):
                    raise CandidateEvaluationError("sealed task order or arms differ")
                for arm in arms:
                    value = row["arms"][arm]
                    amount = value.get("estimated_cost_usd")
                    if (value.get("source") != sources[arm] or type(amount) not in (int, float)
                            or not math.isfinite(amount) or amount < 0):
                        raise CandidateEvaluationError("sealed source invalid or cost missing")
                    try:
                        summary = TaskTrialSummary(**value["measurement"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise CandidateEvaluationError("invalid sealed trial summary") from exc
                    if summary.task_id != tid or summary.attempts != stored["k"]:
                        raise CandidateEvaluationError("sealed trial identity or count differs")
                    arms[arm][tid] = summary
                    cost += amount
            if not math.isfinite(cost) or cost > stored["max_estimated_cost_usd"]:
                raise CandidateEvaluationError("sealed cost exceeded frozen budget")
            if freeze_focused_sealed_plan(repo_root, vault=vault, backend=backend,
                    k=stored["k"], max_estimated_cost_usd=stored["max_estimated_cost_usd"]) != stored:
                raise CandidateEvaluationError("sealed backend configuration drifted")
            if selected and _git(repo_root, "rev-parse", candidate_ref(cid)) != commit:
                raise CandidateEvaluationError("finalist reference changed during scoring")
            metrics = retention_summary(baseline_train, candidate_train, arms["baseline"], arms["candidate"],
                                        stored["tasks"], stored["k"])
            result = {**plan, "receipt": receipt.to_dict(), "metrics": metrics,
                      "estimated_cost_usd": cost, "automatic_activation": False,
                      "execution_completed": True,
                      "passed": selected is not None and metrics["sealed_credited_2sigma"]}
            directory = evolver.ledger.path.parent / "focused-searches" / run_id
            atomic_replace_unlocked(directory / "report.json", json.dumps(result, indent=2, allow_nan=False) + "\n")
            markdown = (f"# Focused Evolution Report\n\nRun: `{run_id}`\n\n"
                f"Training: {baseline_train:.2%} -> {candidate_train:.2%}\n\n"
                f"Held out: {metrics['baseline_test']:.2%} -> {metrics['candidate_test']:.2%}\n\n"
                f"Independent test tasks: {metrics['test_task_count']}; repeats: {stored['k']}.\n\n"
                f"Two-sigma credit: {metrics['sealed_credited_2sigma']}.\n\n"
                "Execution completion is not proof of improvement. No automatic activation.\n")
            atomic_replace_unlocked(directory / "report.md", markdown)
            record("sealed.completed", result)
            return result
        except BaseException as exc:
            record("sealed.failed", {"run_id": run_id, "mode": "focused", "error_type": type(exc).__name__})
            raise
