"""Descriptive efficiency evidence, separate from quality promotion authority."""

import math


def summarize_efficiency(rows, *, task_ids, repetitions, minimum_call_reduction=0.1):
    task_ids = tuple(task_ids)
    if (not task_ids or len(set(task_ids)) != len(task_ids)
            or type(repetitions) is not int or repetitions < 1
            or not 0 <= minimum_call_reduction < 1):
        raise ValueError("invalid frozen efficiency plan")
    expected = {(task, rep, arm) for task in task_ids for rep in range(repetitions)
                for arm in ("control", "treatment")}
    indexed = {}
    for row in rows:
        key = (row["task_id"], row["repetition"], row["arm"])
        if type(row["repetition"]) is not int or key not in expected or key in indexed:
            raise ValueError("unexpected or duplicate efficiency trial")
        indexed[key] = row["result"]
    valid = set(indexed) == expected
    totals = {arm: {"trials": 0, "passes": 0, "calls": 0, "estimated_cost_usd": 0.0,
                   "accounting_complete": True}
              for arm in ("control", "treatment")}
    for (_, _, arm), result in indexed.items():
        total = totals[arm]
        total["trials"] += 1
        calls = result.get("raw", {}).get("calls")
        cost = result.get("estimated_cost_usd")
        known_cost = result.get("raw", {}).get("known_estimated_cost_usd", cost)
        calls_known = type(calls) is int and calls > 0
        cost_known = type(cost) in (int, float) and math.isfinite(cost) and cost >= 0
        measured = (result.get("status") == "completed"
                    and type(result.get("passed")) is bool and calls_known and cost_known)
        valid = valid and measured
        total["accounting_complete"] = total["accounting_complete"] and calls_known and cost_known
        if calls_known:
            total["calls"] += calls
        if type(known_cost) in (int, float) and math.isfinite(known_cost) and known_cost >= 0:
            total["estimated_cost_usd"] += known_cost
        if measured:
            total["passes"] += int(result["passed"])
    for total in totals.values():
        total["accounting_complete"] = total["accounting_complete"] and total["trials"] == len(task_ids) * repetitions
    all_passed = valid and all(t["passes"] == len(task_ids) * repetitions for t in totals.values())
    reductions = {"calls": None, "estimated_cost": None}
    if all_passed:
        control_calls = totals["control"]["calls"]
        reductions["calls"] = (control_calls - totals["treatment"]["calls"]) / control_calls
        control_cost = totals["control"]["estimated_cost_usd"]
        if control_cost > 0:
            reductions["estimated_cost"] = 1 - totals["treatment"]["estimated_cost_usd"] / control_cost
    return {"measurement_complete": valid, "all_tasks_passed_in_both_arms": all_passed,
            "totals": totals, "reductions": reductions,
            "eligible_for_heldout_pilot": all_passed and reductions["calls"] >= minimum_call_reduction,
            "minimum_call_reduction": minimum_call_reduction,
            "independent_tasks": len(task_ids), "repetitions": repetitions,
            "statistically_proven": False, "automatic_activation": False}
