"""Durable single-Turn lifecycle orchestration."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .events import RuntimeEvent
from .runner import Drain, Emit, RunnerEvent, TurnOutcome, TurnRunner, Usage
from .turn import TurnLifecycle, TurnRequest, TurnState


EventSink = Callable[[RuntimeEvent], Awaitable[None]]
Redactor = Callable[[Any], Any]


class TurnRuntime:
    """Own lifecycle events and persistence around an Agent-side runner."""

    def __init__(
        self, runner: TurnRunner, run_store, redactor: Redactor | None = None
    ) -> None:
        self._runner = runner
        self._run_store = run_store
        self._redact = redactor or (lambda value: value)
        self._accepted: dict[str, TurnRequest] = {}

    def accept(self, request: TurnRequest) -> None:
        """Durably admit a Turn before the scheduler returns its handle."""
        turn_id = str(request.turn_id)
        existing = self._accepted.get(turn_id)
        if existing is not None:
            if existing != request:
                raise ValueError(
                    f"turn id {turn_id} was accepted with a different request"
                )
            return

        lifecycle = TurnLifecycle(request)
        event = RuntimeEvent(
            kind="turn.accepted",
            turn_id=request.turn_id,
            session_id=request.session_id,
            request_id=request.request_id,
            sequence=lifecycle.next_sequence(),
            payload=self._redact(
                {
                    "request": {
                        "text": request.text,
                        "work_class": request.work_class.value,
                    }
                }
            ),
        )
        self._run_store.commit_turn_event(
            request.turn_id,
            event.to_dict(),
            self._snapshot(lifecycle),
        )
        self._accepted[turn_id] = request

    async def execute(
        self,
        request: TurnRequest,
        emit: Emit,
        drain: Drain | None = None,
        event_sink: EventSink | None = None,
    ) -> TurnOutcome:
        lifecycle = TurnLifecycle(request)
        self.accept(request)
        lifecycle.sequence = 1
        drain = drain or (lambda: [])

        async def record(
            kind: str,
            payload: dict[str, Any] | None = None,
            snapshot: dict[str, Any] | None = None,
        ) -> None:
            event = RuntimeEvent(
                kind=kind,
                turn_id=request.turn_id,
                session_id=request.session_id,
                request_id=request.request_id,
                sequence=lifecycle.next_sequence(),
                payload=self._redact(payload or {}),
            )
            self._run_store.commit_turn_event(
                request.turn_id, event.to_dict(), snapshot
            )
            if event_sink is not None:
                await event_sink(event)

        async def emit_and_record(event: RunnerEvent) -> None:
            await record(
                "runner.text",
                {"content": event.content},
            )
            await emit(event)

        lifecycle.transition(TurnState.RUNNING)
        await record("turn.started", snapshot=self._snapshot(lifecycle))

        try:
            outcome = await self._runner.run(request, emit_and_record, drain)
            if not outcome.terminal:
                raise RuntimeError("TurnRunner returned a non-terminal outcome")
            if (
                outcome.turn_id != request.turn_id
                or outcome.request_id != request.request_id
                or outcome.session_id != request.session_id
            ):
                raise RuntimeError("TurnRunner returned an outcome for a different request")
            lifecycle.transition(outcome.state)
        except asyncio.CancelledError:
            lifecycle.transition(TurnState.CANCELLED)
            outcome = self._cancelled_outcome(request, "cancelled during execution")
        except Exception as exc:
            lifecycle.transition(TurnState.FAILED)
            outcome = TurnOutcome(
                turn_id=request.turn_id,
                request_id=request.request_id,
                session_id=request.session_id,
                state=TurnState.FAILED,
                usage=Usage(),
                error=f"{type(exc).__name__}: {exc}",
            )

        terminal_kind = {
            TurnState.COMPLETED: "turn.completed",
            TurnState.FAILED: "turn.failed",
            TurnState.CANCELLED: "turn.cancelled",
        }[outcome.state]
        await record(
            terminal_kind,
            outcome.to_dict(),
            self._snapshot(lifecycle, outcome),
        )
        self._accepted.pop(str(request.turn_id), None)
        return outcome

    async def run(
        self,
        request: TurnRequest,
        emit: Emit,
        drain: Drain,
    ) -> TurnOutcome:
        return await self.execute(request, emit, drain)

    async def cancel(
        self,
        request: TurnRequest,
        reason: str = "cancelled before execution",
    ) -> TurnOutcome:
        lifecycle = TurnLifecycle(request)
        self.accept(request)
        lifecycle.sequence = 1
        lifecycle.transition(TurnState.CANCELLED)
        outcome = self._cancelled_outcome(request, reason)
        await self._record(
            lifecycle,
            "turn.cancelled",
            outcome.to_dict(),
            self._snapshot(lifecycle, outcome),
        )
        self._accepted.pop(str(request.turn_id), None)
        return outcome

    async def _record(
        self,
        lifecycle: TurnLifecycle,
        kind: str,
        payload: dict[str, Any] | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        request = lifecycle.request
        event = RuntimeEvent(
            kind=kind,
            turn_id=request.turn_id,
            session_id=request.session_id,
            request_id=request.request_id,
            sequence=lifecycle.next_sequence(),
            payload=self._redact(payload or {}),
        )
        self._run_store.commit_turn_event(
            request.turn_id, event.to_dict(), snapshot
        )
        return event

    @staticmethod
    def _cancelled_outcome(request: TurnRequest, reason: str) -> TurnOutcome:
        return TurnOutcome(
            turn_id=request.turn_id,
            request_id=request.request_id,
            session_id=request.session_id,
            state=TurnState.CANCELLED,
            usage=Usage(),
            error=reason,
        )

    def _snapshot(
        self, lifecycle: TurnLifecycle, outcome: TurnOutcome | None = None
    ) -> dict[str, Any]:
        request = lifecycle.request
        return self._redact(
            {
                "format_version": 1,
                "turn_id": str(request.turn_id),
                "session_id": str(request.session_id),
                "request_id": str(request.request_id),
                "state": lifecycle.state.value,
                "request": {
                    "text": request.text,
                    "work_class": request.work_class.value,
                },
                "outcome": outcome.to_dict() if outcome else None,
            }
        )
