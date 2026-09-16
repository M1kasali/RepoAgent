"""Bounded repair completion metrics, not proof that unfinished patches are wrong."""

import math


def summarize_reliability(rows, *, task_ids, repetitions, minimum_candidate_passes):
    tasks = tuple(task_ids)
    if (not tasks or len(set(tasks)) != len(tasks)
            or type(repetitions) is not int or repetitions < 1
            or type(minimum_candidate_passes) is not int
            or not 1 <= minimum_candidate_passes <= len(tasks) * repetitions):
        raise ValueError("invalid reliability plan")
    expected = {(task, rep, arm) for task in tasks for rep in range(repetitions)
                for arm in ("control", "treatment")}
    seen = set()
    totals = {arm: {"trials": 0, "accepted": 0, "verified_failure": 0,
                   "unfinished": 0, "invalid": 0, "calls": 0,
                   "known_estimated_cost_usd": 0.0} for arm in ("control", "treatment")}
    per_task = {task: {"control": 0, "treatment": 0} for task in tasks}
    for row in rows:
        task, rep, arm = row["task_id"], row["repetition"], row["arm"]
        key = (task, rep, arm)
        if type(rep) is not int or key not in expected or key in seen:
            raise ValueError("unexpected or duplicate reliability trial")
        seen.add(key)
        result = row["result"]
        raw = result.get("raw", {})
        total = totals[arm]
        total["trials"] += 1
        calls, cost = raw.get("calls"), result.get("estimated_cost_usd")
        known_estimate = raw.get("known_estimated_cost_usd", cost)
        known_calls = type(calls) is int and calls > 0
        known_cost = type(cost) in (int, float) and math.isfinite(cost) and cost >= 0
        if known_calls:
            total["calls"] += calls
        if type(known_estimate) in (int, float) and math.isfinite(known_estimate) and known_estimate >= 0:
            total["known_estimated_cost_usd"] += known_estimate
        category = "invalid"
        if known_calls and known_cost:
            if result.get("status") == "completed":
                if result.get("passed") is True and raw.get("case_status") == "candidate_ready":
                    category = "accepted"
                    per_task[task][arm] += 1
                elif result.get("passed") is False and raw.get("case_status") == "verification_failed":
                    category = "verified_failure"
            elif (result.get("status") == "inconclusive" and result.get("passed") is None
                  and raw.get("case_status") in {"budget_exhausted", "repair_incomplete"}):
                category = "unfinished"
        total[category] += 1
    complete = seen == expected
    valid = complete and all(t["invalid"] == 0 for t in totals.values())
    nonregression = valid and all(t["treatment"] >= t["control"] for t in per_task.values())
    lift = totals["treatment"]["accepted"] - totals["control"]["accepted"]
    return {"matrix_complete": complete, "comparison_valid": valid,
            "totals": totals, "per_task": per_task, "accepted_count_difference": lift,
            "eligible_for_heldout_pilot": bool(nonregression and lift > 0
                and totals["treatment"]["accepted"] >= minimum_candidate_passes),
            "minimum_candidate_passes": minimum_candidate_passes,
            "independent_tasks": len(tasks), "repetitions": repetitions,
            "primary_metric": "end-to-end independent acceptance within fixed resources",
            "statistically_proven": False, "automatic_activation": False}
