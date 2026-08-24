import json

import pytest

from repoagent.atomic_io import StorageCorruptionError
from repoagent.run_store import (
    DuplicateTerminalEventError,
    RunStore,
    TurnEventSequenceError,
)
from repoagent.spine import RuntimeEvent, TurnRequest
from repoagent.task_state import STOP_REASON_FINAL_ANSWER_RETURNED, TaskState


def test_run_store_creates_run_directory_and_state_file(tmp_path):
    store = RunStore(tmp_path / ".repoagent" / "runs")
    state = TaskState.create(run_id="run_001", task_id="task_001", user_request="Inspect the repo.")

    run_dir = store.start_run(state)

    assert run_dir == store.run_dir(state.run_id)
    assert run_dir.exists()
    persisted = json.loads((run_dir / "task_state.json").read_text(encoding="utf-8"))
    assert persisted["task_id"] == "task_001"
    assert persisted["run_id"] == "run_001"
    assert persisted["user_request"] == "Inspect the repo."


def test_run_store_appends_trace_jsonl(tmp_path):
    store = RunStore(tmp_path / ".repoagent" / "runs")
    state = TaskState.create(run_id="run_002", task_id="task_002", user_request="Trace the run.")
    store.start_run(state)

    store.append_trace(state, {"event": "run_started", "created_at": "2026-04-07T00:00:00+00:00"})
    store.append_trace(
        state.run_id,
        {
            "event": "prompt_built",
            "created_at": "2026-04-07T00:00:01+00:00",
            "prompt_metadata": {"prompt_chars": 128, "secret_env_count": 1},
        },
    )
    store.append_trace(state.run_id, {"event": "run_finished", "created_at": "2026-04-07T00:00:02+00:00"})

    lines = (store.trace_path(state.run_id)).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0])["event"] == "run_started"
    assert json.loads(lines[1])["event"] == "prompt_built"
    assert json.loads(lines[2])["event"] == "run_finished"


def test_run_store_appends_model_call_ledger(tmp_path):
    store = RunStore(tmp_path / ".repoagent" / "runs")
    state = TaskState.create(
        run_id="run_calls",
        task_id="task_calls",
        user_request="Account for model calls.",
    )
    store.start_run(state)

    store.append_model_call(
        state,
        {"provider_call_id": "task_calls:1:0", "status": "completed"},
    )

    rows = store.call_ledger_path(state).read_text(encoding="utf-8").splitlines()
    assert json.loads(rows[0]) == {
        "provider_call_id": "task_calls:1:0",
        "status": "completed",
    }


def test_run_store_writes_report_json(tmp_path):
    store = RunStore(tmp_path / ".repoagent" / "runs")
    state = TaskState.create(run_id="run_003", task_id="task_003", user_request="Report the run.")
    store.start_run(state)
    state.finish_success("Done.")

    store.write_task_state(state)
    store.write_report(state, {"task_state": state.to_dict(), "stop_reason": state.stop_reason})

    report = json.loads(store.report_path(state.run_id).read_text(encoding="utf-8"))
    assert report["stop_reason"] == STOP_REASON_FINAL_ANSWER_RETURNED
    assert report["task_state"]["final_answer"] == "Done."


def test_run_store_tolerates_missing_final_report(tmp_path):
    store = RunStore(tmp_path / ".repoagent" / "runs")
    state = TaskState.create(run_id="run_004", task_id="task_004", user_request="Crash before finalize.")

    store.start_run(state)
    store.append_trace(state, {"event": "run_started"})

    assert store.trace_path(state.run_id).exists()
    assert not store.report_path(state.run_id).exists()


def _event(request, kind, sequence, payload=None):
    return RuntimeEvent(
        kind=kind,
        turn_id=request.turn_id,
        session_id=request.session_id,
        request_id=request.request_id,
        sequence=sequence,
        payload=payload or {},
    ).to_dict()


def _snapshot(request, state):
    return {
        "format_version": 1,
        "turn_id": str(request.turn_id),
        "session_id": str(request.session_id),
        "request_id": str(request.request_id),
        "state": state,
        "request": {"text": request.text, "work_class": "foreground"},
        "outcome": None,
    }


def test_run_store_rejects_sequence_gap_and_duplicate_terminal(tmp_path):
    store = RunStore(tmp_path / "runs")
    request = TurnRequest.create(session_id="session", text="task")
    store.commit_turn_event(
        request.turn_id,
        _event(
            request,
            "turn.accepted",
            1,
            {"request": _snapshot(request, "accepted")["request"]},
        ),
        _snapshot(request, "accepted"),
    )

    with pytest.raises(TurnEventSequenceError, match="sequence 2"):
        store.commit_turn_event(
            request.turn_id, _event(request, "turn.started", 3)
        )

    store.commit_turn_event(
        request.turn_id, _event(request, "turn.cancelled", 2)
    )
    with pytest.raises(DuplicateTerminalEventError, match="terminal"):
        store.commit_turn_event(
            request.turn_id, _event(request, "turn.failed", 3)
        )


def test_recovery_repairs_partial_tail_and_fails_accepted_turn_once(tmp_path):
    store = RunStore(tmp_path / "runs")
    request = TurnRequest.create(session_id="session", text="queued")
    store.commit_turn_event(
        request.turn_id,
        _event(
            request,
            "turn.accepted",
            1,
            {"request": _snapshot(request, "accepted")["request"]},
        ),
        _snapshot(request, "accepted"),
    )
    with store.turn_events_path(request.turn_id).open("ab") as handle:
        handle.write(b'{"format_version": 1')

    assert store.recover_incomplete_turns() == [str(request.turn_id)]
    events = store.load_turn_events(request.turn_id)
    assert [event["kind"] for event in events] == [
        "turn.accepted",
        "turn.failed",
    ]
    assert events[-1]["payload"]["error"] == "interrupted by process restart"
    assert store.recover_incomplete_turns() == []
    assert len(store.load_turn_events(request.turn_id)) == 2


def test_recovery_reprojects_terminal_event_over_stale_snapshot(tmp_path):
    store = RunStore(tmp_path / "runs")
    request = TurnRequest.create(session_id="session", text="running")
    store.commit_turn_event(
        request.turn_id,
        _event(
            request,
            "turn.accepted",
            1,
            {"request": _snapshot(request, "accepted")["request"]},
        ),
        _snapshot(request, "accepted"),
    )
    store.commit_turn_event(
        request.turn_id,
        _event(request, "turn.started", 2),
        _snapshot(request, "running"),
    )
    outcome = {
        "turn_id": str(request.turn_id),
        "session_id": str(request.session_id),
        "request_id": str(request.request_id),
        "state": "completed",
        "final_answer": "done",
    }
    store.commit_turn_event(
        request.turn_id,
        _event(request, "turn.completed", 3, outcome),
    )

    assert store.recover_incomplete_turns() == []
    snapshot = json.loads(
        store.turn_path(request.turn_id).read_text(encoding="utf-8")
    )
    assert snapshot["state"] == "completed"
    assert snapshot["outcome"]["final_answer"] == "done"


def test_recovery_rejects_snapshot_stored_under_another_turn_id(tmp_path):
    store = RunStore(tmp_path / "runs")
    request = TurnRequest.create(session_id="session", text="task")
    wrong = TurnRequest.create(session_id="session", text="other")
    store.write_turn(request.turn_id, _snapshot(wrong, "accepted"))

    with pytest.raises(
        StorageCorruptionError, match="does not match its run directory"
    ):
        store.recover_incomplete_turns()
