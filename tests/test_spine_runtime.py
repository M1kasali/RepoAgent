import asyncio
import json

from repoagent.spine import Text, TurnOutcome, TurnRequest, TurnRuntime, TurnState, Usage
from repoagent.run_store import RunStore


class CompletingRunner:
    async def run(self, request, emit, drain):
        await emit(Text("done"))
        return TurnOutcome(
            turn_id=request.turn_id,
            request_id=request.request_id,
            session_id=request.session_id,
            state=TurnState.COMPLETED,
            final_answer="done",
            usage=Usage(10, 2, 12),
            explicit_reply=True,
        )


class FailingRunner:
    async def run(self, request, emit, drain):
        raise ValueError("provider unavailable")


class MismatchedRunner:
    async def run(self, request, emit, drain):
        other = TurnRequest.create(session_id="other-session", text="other")
        return TurnOutcome(
            turn_id=other.turn_id,
            request_id=other.request_id,
            session_id=other.session_id,
            state=TurnState.COMPLETED,
        )


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_turn_runtime_persists_one_completed_terminal_outcome(tmp_path):
    store = RunStore(tmp_path / "runs")
    request = TurnRequest.create(session_id="session-1", text="do it")
    emitted = []

    async def emit(event):
        emitted.append(event)

    outcome = asyncio.run(TurnRuntime(CompletingRunner(), store).execute(request, emit))

    assert outcome.state is TurnState.COMPLETED
    assert emitted == [Text("done")]
    turn = json.loads(store.turn_path(request.turn_id).read_text(encoding="utf-8"))
    assert turn["state"] == "completed"
    assert turn["outcome"]["final_answer"] == "done"
    assert turn["request"]["work_class"] == "foreground"
    events = _read_jsonl(store.turn_events_path(request.turn_id))
    assert [event["kind"] for event in events] == [
        "turn.accepted",
        "turn.started",
        "runner.text",
        "turn.completed",
    ]
    assert [event["sequence"] for event in events] == [1, 2, 3, 4]
    terminal_events = [
        event
        for event in events
        if event["kind"] in {"turn.completed", "turn.failed", "turn.cancelled"}
    ]
    assert len(terminal_events) == 1


def test_turn_runtime_accept_is_durable_and_idempotent(tmp_path):
    store = RunStore(tmp_path / "runs")
    runtime = TurnRuntime(CompletingRunner(), store)
    request = TurnRequest.create(session_id="session-1", text="queued")

    runtime.accept(request)
    runtime.accept(request)

    events = _read_jsonl(store.turn_events_path(request.turn_id))
    snapshot = json.loads(
        store.turn_path(request.turn_id).read_text(encoding="utf-8")
    )
    assert [event["kind"] for event in events] == ["turn.accepted"]
    assert snapshot["state"] == "accepted"


def test_turn_runtime_converts_runner_exception_to_persisted_failure(tmp_path):
    store = RunStore(tmp_path / "runs")
    request = TurnRequest.create(session_id="session-1", text="do it")

    async def emit(event):
        raise AssertionError("failure path must not deliver output")

    outcome = asyncio.run(TurnRuntime(FailingRunner(), store).execute(request, emit))

    assert outcome.state is TurnState.FAILED
    assert outcome.error == "ValueError: provider unavailable"
    turn = json.loads(store.turn_path(request.turn_id).read_text(encoding="utf-8"))
    assert turn["state"] == "failed"
    assert turn["outcome"]["error"] == "ValueError: provider unavailable"
    events = _read_jsonl(store.turn_events_path(request.turn_id))
    assert [event["kind"] for event in events] == [
        "turn.accepted",
        "turn.started",
        "turn.failed",
    ]


def test_turn_runtime_rejects_an_outcome_for_another_request(tmp_path):
    store = RunStore(tmp_path / "runs")
    request = TurnRequest.create(session_id="session-1", text="do it")

    async def emit(event):
        raise AssertionError("mismatched result must not deliver output")

    outcome = asyncio.run(TurnRuntime(MismatchedRunner(), store).execute(request, emit))

    assert outcome.state is TurnState.FAILED
    assert outcome.error == "RuntimeError: TurnRunner returned an outcome for a different request"
    turn = json.loads(store.turn_path(request.turn_id).read_text(encoding="utf-8"))
    assert turn["turn_id"] == request.turn_id
    assert turn["state"] == "failed"
