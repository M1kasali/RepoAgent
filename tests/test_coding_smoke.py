import pytest

from repoagent.evaluation.coding_smoke import classify_coding_smoke


def report(status="completed", reason="final_answer_returned"):
    return {
        "status": status,
        "stop_reason": reason,
        "final_answer": "done",
        "task_state": {"status": status, "stop_reason": reason},
    }


def test_passing_tests_do_not_turn_call_exhaustion_into_success():
    result = classify_coding_smoke(
        report("stopped", "step_limit_reached"), check_exit_code=0, tests_unchanged=True
    )
    assert result["code_passed"] and not result["runtime_completed"]
    assert result["status"] == "incomplete"


def test_success_requires_independent_tests_integrity_and_terminal_success():
    assert (
        classify_coding_smoke(report(), check_exit_code=0, tests_unchanged=True)[
            "status"
        ]
        == "pass"
    )
    assert (
        classify_coding_smoke(report(), check_exit_code=1, tests_unchanged=True)[
            "status"
        ]
        == "fail"
    )
    assert (
        classify_coding_smoke(report(), check_exit_code=0, tests_unchanged=False)[
            "status"
        ]
        == "fail"
    )
    assert (
        classify_coding_smoke(
            report(), check_exit_code=0, tests_unchanged=True, error="failure"
        )["status"]
        == "error"
    )


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"status": "completed"},
        report("completed", "step_limit_reached"),
        {**report(), "task_state": {}},
        {**report(), "final_answer": ""},
    ],
)
def test_missing_or_inconsistent_terminal_evidence_never_passes(value):
    assert (
        classify_coding_smoke(value, check_exit_code=0, tests_unchanged=True)["status"]
        != "pass"
    )


@pytest.mark.parametrize(
    "exit_code,unchanged", [(False, True), (0, "yes"), ("0", True), (0, 1)]
)
def test_verifier_facts_are_strictly_typed(exit_code, unchanged):
    with pytest.raises(ValueError):
        classify_coding_smoke(
            report(), check_exit_code=exit_code, tests_unchanged=unchanged
        )


def test_transcript_replay_reports_dropped_edits_without_returning_tool_payloads():
    from repoagent.evaluation.coding_smoke import replay_history_retention

    history = [{"role": "user", "content": "edit"}]
    for index in range(5):
        name = "patch_file" if index == 0 else "read_file"
        history.extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": str(index),
                            "name": name,
                            "args": {"path": "file", "payload": "x" * 1000},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "name": name,
                    "tool_call_id": str(index),
                    "content": "private-tool-output" * 100,
                },
            ]
        )
    result = replay_history_retention(history, budgets=(800, 10000))
    small = result["rows"][-2]
    large = result["rows"][-1]
    assert small["tool_results"][0]["retention"] == "dropped"
    assert large["tool_results"][0]["retention"] == "unchanged"
    assert "private-tool-output" not in str(result)


def test_smoke_inspector_rejects_corrupt_and_unverified_inputs(tmp_path):
    import hashlib
    import json
    from scripts.inspect_coding_smoke import inspect

    path = tmp_path / "summary.json"
    path.write_text("{}")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"sha256": {"summary.json": "wrong"}})
    )
    with pytest.raises(ValueError, match="digest"):
        inspect(tmp_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(
        json.dumps({"sha256": {"../outside": digest}})
    )
    with pytest.raises(ValueError, match="unsafe"):
        inspect(tmp_path)
