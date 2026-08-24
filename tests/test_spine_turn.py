import dataclasses

import pytest

from repoagent.spine import (
    EVENT_FORMAT_VERSION,
    IllegalTurnTransition,
    RuntimeEvent,
    TurnLifecycle,
    TurnRequest,
    TurnState,
)


def test_turn_request_has_distinct_correlated_identifiers():
    request = TurnRequest.create(session_id="session-1", text="inspect the repo")

    assert str(request.turn_id).startswith("turn_")
    assert str(request.request_id).startswith("request_")
    assert request.turn_id != request.request_id
    assert request.session_id == "session-1"
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.text = "changed"


def test_turn_lifecycle_accepts_only_legal_transitions():
    lifecycle = TurnLifecycle(
        TurnRequest.create(session_id="session-1", text="inspect")
    )

    assert lifecycle.state is TurnState.ACCEPTED
    assert lifecycle.transition(TurnState.RUNNING) is TurnState.RUNNING
    assert lifecycle.transition(TurnState.COMPLETED) is TurnState.COMPLETED
    with pytest.raises(IllegalTurnTransition, match="completed -> running"):
        lifecycle.transition(TurnState.RUNNING)


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (TurnState.ACCEPTED, TurnState.COMPLETED),
        (TurnState.ACCEPTED, TurnState.FAILED),
        (TurnState.COMPLETED, TurnState.FAILED),
        (TurnState.FAILED, TurnState.COMPLETED),
        (TurnState.CANCELLED, TurnState.RUNNING),
    ],
)
def test_turn_lifecycle_rejects_illegal_transition(start, target):
    lifecycle = TurnLifecycle(
        TurnRequest.create(session_id="session-1", text="inspect"), state=start
    )

    with pytest.raises(IllegalTurnTransition):
        lifecycle.transition(target)


def test_runtime_event_is_versioned_correlated_and_immutable():
    request = TurnRequest.create(session_id="session-1", text="inspect")
    event = RuntimeEvent(
        kind="turn.accepted",
        turn_id=request.turn_id,
        session_id=request.session_id,
        request_id=request.request_id,
        sequence=1,
        payload={"key": "value"},
    )

    assert event.format_version == EVENT_FORMAT_VERSION == 1
    assert event.to_dict()["turn_id"] == request.turn_id
    with pytest.raises(TypeError):
        event.payload["key"] = "changed"
