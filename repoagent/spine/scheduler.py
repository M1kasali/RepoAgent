"""Per-session FIFO scheduling with isolated foreground/background capacity."""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Protocol

from ._barrier import finish_barrier
from .runner import Drain, Emit, RunnerEvent, TurnOutcome
from .turn import TurnRequest, WorkClass


class SchedulerDrainingError(RuntimeError):
    pass


class TurnExecutor(Protocol):
    def accept(self, request: TurnRequest) -> None: ...

    async def run(
        self, request: TurnRequest, emit: Emit, drain: Drain
    ) -> TurnOutcome: ...

    async def cancel(self, request: TurnRequest, reason: str) -> TurnOutcome: ...


EventSink = Callable[[RunnerEvent], Awaitable[None]]


class WorkPools:
    def __init__(self, *, foreground: int, background: int) -> None:
        if foreground < 1 or background < 1:
            raise ValueError("work pool capacities must be positive")
        self._foreground = asyncio.Semaphore(foreground)
        self._background = asyncio.Semaphore(background)

    def for_class(self, work_class: WorkClass) -> asyncio.Semaphore:
        if work_class is WorkClass.FOREGROUND:
            return self._foreground
        if work_class is WorkClass.BACKGROUND:
            return self._background
        raise ValueError(f"no pool mapping for work class {work_class!r}")


class _QueuedTurn:
    def __init__(self, request: TurnRequest, future: asyncio.Future) -> None:
        self.request = request
        self.future = future
        self.cancel_requested = asyncio.Event()
        self.events: list[RunnerEvent] = []


class _Lane:
    def __init__(
        self,
        executor: TurnExecutor,
        pools: WorkPools,
        sink: EventSink,
    ) -> None:
        self._executor = executor
        self._pools = pools
        self._sink = sink
        self._pending: deque[_QueuedTurn] = deque()
        self._worker: asyncio.Task | None = None
        self._running: _QueuedTurn | None = None
        self._running_task: asyncio.Task | None = None
        self._finalizers: set[asyncio.Task] = set()

    def submit(self, request: TurnRequest) -> tuple[asyncio.Future, _QueuedTurn]:
        loop = asyncio.get_running_loop()
        item = _QueuedTurn(request, loop.create_future())
        self._pending.append(item)
        if self._worker is None or self._worker.done():
            self._worker = loop.create_task(self._run_worker())
        return item.future, item

    def cancel(self, item: _QueuedTurn) -> None:
        if item.future.done():
            return
        for index, queued in enumerate(self._pending):
            if queued is item:
                del self._pending[index]
                self._schedule_cancel(item, "cancelled while queued")
                return
        if self._running is item:
            item.cancel_requested.set()

    def cancel_running(self) -> int:
        if self._running is None or self._running.future.done():
            return 0
        self._running.cancel_requested.set()
        return 1

    def drain_pending(self) -> int:
        count = 0
        while self._pending:
            self._schedule_cancel(self._pending.popleft(), "cancelled during shutdown")
            count += 1
        return count

    def has_work(self) -> bool:
        return self._running is not None or bool(self._pending) or bool(self._finalizers)

    def running_future(self) -> asyncio.Future | None:
        return self._running.future if self._running is not None else None

    def worker_task(self) -> asyncio.Task | None:
        return self._worker

    async def wait_finalizers(self) -> None:
        if self._finalizers:
            await asyncio.gather(*tuple(self._finalizers))

    def _schedule_cancel(self, item: _QueuedTurn, reason: str) -> None:
        task = asyncio.create_task(self._finish_cancel(item, reason))
        self._finalizers.add(task)
        task.add_done_callback(self._finalizers.discard)

    async def _finish_cancel(self, item: _QueuedTurn, reason: str) -> None:
        try:
            outcome = await self._executor.cancel(item.request, reason)
        except Exception as exc:
            if not item.future.done():
                item.future.set_exception(exc)
        else:
            if not item.future.done():
                item.future.set_result(outcome)

    async def _run_worker(self) -> None:
        while self._pending:
            item = self._pending.popleft()
            self._running = item
            self._running_task = asyncio.create_task(self._run_item(item))
            try:
                outcome = await self._running_task
            except Exception as exc:
                if not item.future.done():
                    item.future.set_exception(exc)
            else:
                if not item.future.done():
                    item.future.set_result(outcome)
            finally:
                self._running_task = None
                self._running = None

    async def _run_item(self, item: _QueuedTurn) -> TurnOutcome:
        semaphore = self._pools.for_class(item.request.work_class)
        acquire = asyncio.create_task(semaphore.acquire())
        cancellation = asyncio.create_task(item.cancel_requested.wait())
        try:
            done, _pending = await asyncio.wait(
                {acquire, cancellation}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancellation in done:
                if not acquire.done():
                    acquire.cancel()
                    await asyncio.gather(acquire, return_exceptions=True)
                elif acquire.result():
                    semaphore.release()
                return await self._executor.cancel(
                    item.request, "cancelled before execution"
                )

            acquired = acquire.result()
            if not acquired:
                raise RuntimeError("work pool acquisition failed")
            if item.cancel_requested.is_set():
                semaphore.release()
                return await self._executor.cancel(
                    item.request, "cancelled before execution"
                )

            run_task = asyncio.create_task(
                self._executor.run(
                    item.request, self._emit_for(item), lambda: []
                )
            )
            done, _pending = await asyncio.wait(
                {run_task, cancellation}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancellation in done and not run_task.done():
                run_task.cancel()
            try:
                return await run_task
            except asyncio.CancelledError:
                return await self._executor.cancel(
                    item.request, "cancelled during execution"
                )
            finally:
                semaphore.release()
        finally:
            for task in (acquire, cancellation):
                if not task.done():
                    task.cancel()
            await asyncio.gather(acquire, cancellation, return_exceptions=True)

    def _emit_for(self, item: _QueuedTurn) -> Emit:
        async def emit(event: RunnerEvent) -> None:
            item.events.append(event)
            await self._sink(event)

        return emit


class TurnHandle:
    def __init__(self, lane: _Lane, item: _QueuedTurn) -> None:
        self._lane = lane
        self._item = item

    @property
    def turn_id(self) -> str:
        return str(self._item.request.turn_id)

    @property
    def events(self) -> tuple[RunnerEvent, ...]:
        return tuple(self._item.events)

    async def result(self) -> TurnOutcome:
        return await asyncio.shield(self._item.future)

    def cancel(self) -> None:
        self._lane.cancel(self._item)


class Scheduler:
    def __init__(
        self,
        executor: TurnExecutor,
        *,
        foreground_capacity: int = 4,
        background_capacity: int = 2,
        sink: EventSink | None = None,
    ) -> None:
        self._loop = asyncio.get_running_loop()
        self._executor = executor
        self._pools = WorkPools(
            foreground=foreground_capacity,
            background=background_capacity,
        )
        self._sink = sink or self._discard
        self._lanes: dict[str, _Lane] = {}
        self._handles_by_turn_id: dict[str, tuple[TurnRequest, TurnHandle]] = {}
        self._draining = False
        self._shutdown_task: asyncio.Task[None] | None = None

    @staticmethod
    async def _discard(event: RunnerEvent) -> None:
        return None

    def submit(self, request: TurnRequest) -> TurnHandle:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("submit must be called from the scheduler event loop")
        if self._draining:
            raise SchedulerDrainingError(
                "scheduler is draining; new turns are not accepted"
            )
        turn_id = str(request.turn_id)
        existing = self._handles_by_turn_id.get(turn_id)
        if existing is not None:
            existing_request, handle = existing
            if existing_request != request:
                raise ValueError(
                    f"turn id {turn_id} was already submitted with a different request"
                )
            return handle
        self._executor.accept(request)
        lane_id = str(request.session_id)
        lane = self._lanes.get(lane_id)
        if lane is None:
            lane = _Lane(self._executor, self._pools, self._sink)
            self._lanes[lane_id] = lane
        _future, item = lane.submit(request)
        handle = TurnHandle(lane, item)
        self._handles_by_turn_id[turn_id] = (request, handle)
        return handle

    def cancel_session(self, session_id: str) -> int:
        lane = self._lanes.get(str(session_id))
        if lane is None:
            return 0
        count = lane.cancel_running()
        count += lane.drain_pending()
        return count

    def has_work(self, session_id: str) -> bool:
        lane = self._lanes.get(str(session_id))
        return lane is not None and lane.has_work()

    async def shutdown(self, grace: float = 5.0) -> None:
        if grace < 0:
            raise ValueError("shutdown grace must not be negative")
        if self._shutdown_task is None:
            self._draining = True
            self._shutdown_task = asyncio.create_task(self._finish_shutdown(grace))
        await finish_barrier(self._shutdown_task)

    async def _finish_shutdown(self, grace: float) -> None:
        for lane in self._lanes.values():
            lane.drain_pending()
        running = [
            future
            for lane in self._lanes.values()
            if (future := lane.running_future()) is not None
        ]
        if running:
            await asyncio.wait(running, timeout=grace)
        for lane in self._lanes.values():
            lane.cancel_running()
        workers = [
            task
            for lane in self._lanes.values()
            if (task := lane.worker_task()) is not None
        ]
        if workers:
            await asyncio.gather(*workers)
        await asyncio.gather(
            *(lane.wait_finalizers() for lane in self._lanes.values())
        )
