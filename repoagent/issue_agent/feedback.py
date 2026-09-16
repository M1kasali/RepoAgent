"""Evidence-backed triage; never authorizes evolution or claims root cause."""

from .cases import digest


def diagnose(state):
    runs = state.get("runs", [])
    run = runs[-1] if runs else {}
    verification = run.get("verification", {})
    candidate = verification.get("candidate", {})
    category = "unknown"
    evidence = ["/status"]
    if state.get("status") == "candidate_ready":
        category = "accepted_candidate"
    elif state.get("status") == "environment_blocked":
        category = "baseline_blocked"
    elif state.get("status") == "budget_exhausted":
        category = "budget_exhausted"
    elif state.get("status") == "needs_information":
        category = "missing_information"
    elif state.get("status") == "not_reproduced":
        category = "not_reproduced"
    elif (
        state.get("status") == "verification_failed"
        and run.get("phase") == "fix"
        and run.get("status") == "completed"
        and verification.get("baseline", {}).get("reproduced") is True
        and candidate.get("reproduced") is True
        and candidate.get("passed") is False
        and candidate.get("status") == "completed"
        and candidate.get("exit_code") == 1
        and candidate.get("output_truncated") is False
    ):
        category = "known_failure_persists"
        evidence += [
            f"/runs/{len(runs) - 1}/verification/baseline",
            f"/runs/{len(runs) - 1}/verification/candidate",
        ]
    return {
        "schema": "repoagent.issue-feedback/v1",
        "case_id": state["case_id"],
        "state_digest": digest(state),
        "category": category,
        "evidence_pointers": evidence,
        "eligible_for_manual_training_review": category == "known_failure_persists",
        "automatic_evolution": False,
        "root_cause": None,
    }
