import asyncio
import json

import pytest

from repoagent.run_store import RunStore
from repoagent.spine import (
    Scheduler,
    SchedulerDrainingError,
    TurnOutcome,
    TurnRequest,
    TurnRuntime,
    TurnState,
    Usage,
    WorkClass,
    WorkPools,
)


def _outcome(request, state=TurnState.COMPLETED, error=None):
    return TurnOutcome(
        turn_id=request.turn_id,
        request_id=request.request_id,
        session_id=request.session_id,
        state=state,
        usage=Usage(),
        error=error,
    )


class RecordingExecutor:
    def __init__(self, delay=0):
        self.delay = delay
        self.accepted = []
        self.order = []
        self.live = 0
        self.max_live = 0

    def accept(self, request):
        self.accepted.append(request)

    async def run(self, request, emit, drain):
        self.live += 1
        self.max_live = max(self.max_live, self.live)
        self.order.append(request.text)
        try:
            await asyncio.sleep(self.delay)
            return _outcome(request)
        finally:
            self.live -= 1

    async def cancel(self, request, reason):
        return _outcome(request, TurnState.CANCELLED, reason)


class CapacityExecutor(RecordingExecutor):
    def __init__(self, target):
        super().__init__()
        self.target = target
        self.reached = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, request, emit, drain):
        self.live += 1
        self.max_live = max(self.max_live, self.live)
        if self.live >= self.target:
            self.reached.set()
        try:
            await self.release.wait()
            return _outcome(request)
        finally:
            self.live -= 1


class ClassIsolationExecutor(RecordingExecutor):
    def __init__(self):
        super().__init__()
        self.background_started = asyncio.Event()
        self.release_background = asyncio.Event()

    async def run(self, request, emit, drain):
        if request.work_class is WorkClass.BACKGROUND:
            self.background_started.set()
            await self.release_background.wait()
        return _outcome(request)


class HangingRunner:
    def __init__(self):
        self.started = asyncio.Event()

    async def run(self, request, emit, drain):
        self.started.set()
        await asyncio.Event().wait()
        return _outcome(request)


class CancellationFailingExecutor(RecordingExecutor):
    async def cancel(self, request, reason):
        raise OSError("turn evidence unavailable")


def test_work_pools_require_positive_isolated_capacities():
    with pytest.raises(ValueError, match="positive"):
        WorkPools(foreground=0, background=1)

    pools = WorkPools(foreground=1, background=1)
    assert pools.for_class(WorkClass.FOREGROUND) is not pools.for_class(
        WorkClass.BACKGROUND
    )


def test_queued_cancellation_failure_resolves_handle_with_error():
    async def scenario():
        scheduler = Scheduler(CancellationFailingExecutor())
        handle = scheduler.submit(TurnRequest.create(session_id="s", text="queued"))
        handle.cancel()

        with pytest.raises(OSError, match="evidence unavailable"):
            await asyncio.wait_for(handle.result(), timeout=1)
        await scheduler.shutdown()

    asyncio.run(scenario())


def test_scheduler_preserves_fifo_within_one_session():
    async def scenario():
        executor = RecordingExecutor(delay=0.001)
        scheduler = Scheduler(executor, foreground_capacity=8)
        handles = [
            scheduler.submit(TurnRequest.create(session_id="same", text=text))
            for text in ("a", "b", "c")
        ]

        outcomes = await asyncio.gather(*(handle.result() for handle in handles))
        await scheduler.shutdown()

        assert executor.order == ["a", "b", "c"]
        assert executor.max_live == 1
        assert all(outcome.state is TurnState.COMPLETED for outcome in outcomes)

    asyncio.run(scenario())


def test_scheduler_deduplicates_identical_turn_delivery():
    async def scenario():
        executor = RecordingExecutor(delay=0.001)
        scheduler = Scheduler(executor)
        request = TurnRequest.create(session_id="same", text="once")

        first = scheduler.submit(request)
        duplicate = scheduler.submit(request)

        assert duplicate is first
        assert await first.result() == await duplicate.result()
        assert executor.accepted == [request]
        assert executor.order == ["once"]
        await scheduler.shutdown()

    asyncio.run(scenario())


def test_scheduler_rejects_turn_id_reuse_with_different_payload():
    async def scenario():
        executor = RecordingExecutor()
        scheduler = Scheduler(executor)
        request = TurnRequest.create(session_id="same", text="original")
        scheduler.submit(request)
        changed = TurnRequest(
            turn_id=request.turn_id,
            request_id=request.request_id,
            session_id=request.session_id,
            text="changed",
            work_class=request.work_class,
        )

        with pytest.raises(ValueError, match="different request"):
            scheduler.submit(changed)

        await scheduler.shutdown()

    asyncio.run(scenario())


def test_scheduler_bounds_cross_session_concurrency():
    async def scenario():
        executor = CapacityExecutor(target=2)
        scheduler = Scheduler(executor, foreground_capacity=2)
        handles = [
            scheduler.submit(TurnRequest.create(session_id=f"s-{index}", text=str(index)))
            for index in range(4)
        ]

        await asyncio.wait_for(executor.reached.wait(), timeout=1)
        assert executor.max_live == 2
        executor.release.set()
        await asyncio.gather(*(handle.result() for handle in handles))
        await scheduler.shutdown()
        assert executor.max_live == 2

    asyncio.run(scenario())


def test_background_capacity_cannot_block_foreground_work():
    async def scenario():
        executor = ClassIsolationExecutor()
        scheduler = Scheduler(
            executor, foreground_capacity=1, background_capacity=1
        )
        background = scheduler.submit(
            TurnRequest.create(
                session_id="background",
                text="background",
                work_class=WorkClass.BACKGROUND,
            )
        )
        await executor.background_started.wait()
        foreground = scheduler.submit(
            TurnRequest.create(session_id="foreground", text="foreground")
        )

        outcome = await asyncio.wait_for(foreground.result(), timeout=1)
        assert outcome.state is TurnState.COMPLETED
        executor.release_background.set()
        await background.result()
        await scheduler.shutdown()

    asyncio.run(scenario())


def test_running_and_queued_cancellation_persist_terminal_outcomes(tmp_path):
    async def scenario():
        runner = HangingRunner()
        store = RunStore(tmp_path / "runs")
        runtime = TurnRuntime(runner, store)
        scheduler = Scheduler(runtime, foreground_capacity=1)
        running_request = TurnRequest.create(session_id="same", text="running")
        queued_request = TurnRequest.create(session_id="same", text="queued")
        running = scheduler.submit(running_request)
        await runner.started.wait()
        queued = scheduler.submit(queued_request)

        queued.cancel()
        queued_outcome = await asyncio.wait_for(queued.result(), timeout=1)
        running.cancel()
        running_outcome = await asyncio.wait_for(running.result(), timeout=1)
        await scheduler.shutdown()

        assert queued_outcome.state is TurnState.CANCELLED
        assert running_outcome.state is TurnState.CANCELLED
        for request in (running_request, queued_request):
            turn = json.loads(
                store.turn_path(request.turn_id).read_text(encoding="utf-8")
            )
            assert turn["state"] == "cancelled"
            events = store.turn_events_path(request.turn_id).read_text(encoding="utf-8")
            assert events.count('"kind": "turn.cancelled"') == 1

    asyncio.run(scenario())


def test_shutdown_seals_drains_and_cancels_without_unresolved_handles(tmp_path):
    async def scenario():
        runner = HangingRunner()
        runtime = TurnRuntime(runner, RunStore(tmp_path / "runs"))
        scheduler = Scheduler(runtime, foreground_capacity=1)
        running = scheduler.submit(
            TurnRequest.create(session_id="same", text="running")
        )
        await runner.started.wait()
        queued = scheduler.submit(
            TurnRequest.create(session_id="same", text="queued")
        )

        await asyncio.wait_for(scheduler.shutdown(grace=0), timeout=2)
        assert (await running.result()).state is TurnState.CANCELLED
        assert (await queued.result()).state is TurnState.CANCELLED
        assert scheduler.has_work("same") is False
        with pytest.raises(SchedulerDrainingError):
            scheduler.submit(TurnRequest.create(session_id="new", text="rejected"))

    asyncio.run(scenario())


def test_concurrent_shutdown_callers_share_one_cleanup_barrier(tmp_path):
    async def scenario():
        runner = HangingRunner()
        runtime = TurnRuntime(runner, RunStore(tmp_path / "runs"))
        scheduler = Scheduler(runtime, foreground_capacity=1)
        handle = scheduler.submit(
            TurnRequest.create(session_id="same", text="running")
        )
        await runner.started.wait()

        first = asyncio.create_task(scheduler.shutdown(grace=0))
        second = asyncio.create_task(scheduler.shutdown(grace=10))
        await asyncio.gather(first, second)

        assert (await handle.result()).state is TurnState.CANCELLED
        with pytest.raises(SchedulerDrainingError):
            scheduler.submit(TurnRequest.create(session_id="new", text="rejected"))

    asyncio.run(scenario())


def test_cancelled_shutdown_waiter_does_not_interrupt_cleanup(tmp_path):
    async def scenario():
        runner = HangingRunner()
        runtime = TurnRuntime(runner, RunStore(tmp_path / "runs"))
        scheduler = Scheduler(runtime, foreground_capacity=1)
        handle = scheduler.submit(
            TurnRequest.create(session_id="same", text="running")
        )
        await runner.started.wait()

        waiter = asyncio.create_task(scheduler.shutdown(grace=0.05))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        outcome = await asyncio.wait_for(handle.result(), timeout=1)
        assert outcome.state is TurnState.CANCELLED
        with pytest.raises(SchedulerDrainingError):
            scheduler.submit(TurnRequest.create(session_id="new", text="rejected"))

    asyncio.run(scenario())


def test_scheduler_accounts_for_10000_accepted_requests():
    async def scenario():
        executor = RecordingExecutor()
        scheduler = Scheduler(executor, foreground_capacity=32)
        handles = [
            scheduler.submit(
                TurnRequest.create(
                    session_id=f"session-{index % 100}", text=str(index)
                )
            )
            for index in range(10_000)
        ]

        outcomes = await asyncio.gather(*(handle.result() for handle in handles))
        await scheduler.shutdown()

        assert len(outcomes) == 10_000
        assert len({outcome.turn_id for outcome in outcomes}) == 10_000
        assert all(outcome.state is TurnState.COMPLETED for outcome in outcomes)

    asyncio.run(scenario())
