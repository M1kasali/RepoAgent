"""Separate independent code checks from successful Runtime completion."""

from ..task_state import STATUS_COMPLETED, STOP_REASON_FINAL_ANSWER_RETURNED
from ..conversation import build_structured_history
from ..context_overflow import fit_messages_to_token_budget
from ..tokenization import Utf8TokenEstimator


def classify_coding_smoke(report, *, check_exit_code, tests_unchanged, error=""):
    if type(check_exit_code) is not int or type(tests_unchanged) is not bool:
        raise ValueError(
            "smoke verification requires an integer exit code and boolean test integrity"
        )
    state = report.get("task_state", {})
    completed = (
        report.get("status") == state.get("status") == STATUS_COMPLETED
        and report.get("stop_reason")
        == state.get("stop_reason")
        == STOP_REASON_FINAL_ANSWER_RETURNED
        and isinstance(report.get("final_answer"), str)
        and bool(report["final_answer"].strip())
    )
    code_passed = check_exit_code == 0
    status = (
        "error"
        if error
        else "fail"
        if not code_passed or not tests_unchanged
        else "pass"
        if completed
        else "incomplete"
    )
    return {
        "schema": "repoagent.coding-smoke-verdict/v1",
        "status": status,
        "code_passed": code_passed,
        "tests_unchanged": tests_unchanged,
        "runtime_completed": completed,
        "runtime_status": report.get("status", "unknown"),
        "runtime_stop_reason": report.get("stop_reason", "unknown"),
    }


def replay_history_retention(history, budgets=(2000, 3000, 5000)):
    """Transcript-only diagnostic, not an exact replay of the original prompt."""
    counter = Utf8TokenEstimator()
    rows = []
    for index, item in enumerate(history):
        if item.get("role") != "assistant" or not item.get("tool_calls"):
            continue
        messages = build_structured_history(history[:index], counter, 10**9).messages
        original = {m.tool_call_id: m for m in messages if m.role == "tool"}
        if not original:
            continue
        for budget in budgets:
            fitted, metadata = fit_messages_to_token_budget(messages, counter, budget)
            remaining = {m.tool_call_id: m for m in fitted if m.role == "tool"}
            rows.append(
                {
                    "before_history_index": index,
                    "token_budget": budget,
                    "reduction": metadata,
                    "tool_results": [
                        {
                            "call_id": call_id,
                            "name": message.name,
                            "retention": "dropped"
                            if call_id not in remaining
                            else "unchanged"
                            if remaining[call_id].content == message.content
                            else "elided_or_clipped",
                        }
                        for call_id, message in original.items()
                    ],
                }
            )
    return {
        "scope": "transcript_only_without_original_system_prefix",
        "rows": rows,
        "limitation": "Shows deterministic reducer behavior, not causal proof of model repetition or live-call token equivalence.",
    }
