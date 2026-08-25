"""One scheduler-backed ingress for CLI, TUI, channels, and cron."""

from __future__ import annotations

import asyncio
from collections import defaultdict
import inspect

from .agent_turn_runner import AgentTurnRunner
from .channels import DeliveryResult
from .spine import Scheduler, TurnRuntime


class RuntimeHost:
    def __init__(self, agent, *, foreground_capacity=1, background_capacity=1):
        self.agent = agent
        self.foreground_capacity = foreground_capacity
        self.background_capacity = background_capacity
        self.scheduler = None
        self._handles = {}
        self._dedup = {}
        self._watchers = set()
        self._subscribers = defaultdict(dict)
        self._next_subscriber = 0

    async def start(self):
        if self.scheduler is not None:
            return False
        if not self.agent._memory_backend_started:
            await self.agent.memory_backend.start()
            self.agent._memory_backend_started = True
            self.agent.skill_watcher.start()
        runtime = TurnRuntime(
            AgentTurnRunner(self.agent),
            self.agent.run_store,
            redactor=self.agent.redact_artifact,
        )
        self.scheduler = Scheduler(
            runtime,
            foreground_capacity=self.foreground_capacity,
            background_capacity=self.background_capacity,
        )
        return True

    def subscribe(self, session_id, callback):
        if not callable(callback):
            raise TypeError("runtime subscriber must be callable")
        self._next_subscriber += 1
        key = self._next_subscriber
        self._subscribers[str(session_id)][key] = callback
        return key

    def unsubscribe(self, session_id, subscription_id):
        return self._subscribers[str(session_id)].pop(subscription_id, None) is not None

    async def _publish(self, session_id, event):
        for callback in tuple(self._subscribers[str(session_id)].values()):
            result = callback(dict(event))
            if inspect.isawaitable(result):
                await result

    async def submit(self, message, *, deliver=None):
        if self.scheduler is None:
            await self.start()
        existing = self._dedup.get(message.dedup_key)
        if existing is not None:
            return {"accepted": True, "duplicate": True, "turn_id": existing}
        request = message.to_turn_request()
        handle = self.scheduler.submit(request)
        turn_id = str(request.turn_id)
        self._handles[turn_id] = handle
        self._dedup[message.dedup_key] = turn_id
        await self._publish(
            message.session_id,
            {"type": "turn.accepted", "turn_id": turn_id, "request_id": str(request.request_id)},
        )
        watcher = asyncio.create_task(self._finish(message, handle, deliver))
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watchers.discard)
        return {"accepted": True, "duplicate": False, "turn_id": turn_id}

    async def _finish(self, message, handle, deliver):
        outcome = await handle.result()
        delivery = None
        if deliver is not None and outcome.final_answer:
            try:
                await deliver(message.chat_id, outcome.final_answer, ())
                delivery = DeliveryResult(message.channel, message.chat_id, "delivered")
            except Exception as exc:
                delivery = DeliveryResult(
                    message.channel,
                    message.chat_id,
                    "failed",
                    f"{type(exc).__name__}: {exc}",
                )
        await self._publish(
            message.session_id,
            {
                "type": "turn.terminal",
                "turn_id": str(outcome.turn_id),
                "outcome": outcome.to_dict(),
                "delivery": delivery.__dict__ if delivery else None,
            },
        )
        return outcome

    def cancel(self, turn_id):
        handle = self._handles.get(str(turn_id))
        if handle is None:
            return False
        handle.cancel()
        return True

    async def wait(self, turn_id):
        handle = self._handles.get(str(turn_id))
        if handle is None:
            raise KeyError(f"unknown turn: {turn_id}")
        return await handle.result()

    async def stop(self, grace=5.0):
        if self.scheduler is not None:
            await self.scheduler.shutdown(grace=grace)
        if self._watchers:
            await asyncio.gather(*tuple(self._watchers), return_exceptions=True)
        self.scheduler = None
        await self.agent.aclose(grace=grace)


__all__ = ["RuntimeHost"]
