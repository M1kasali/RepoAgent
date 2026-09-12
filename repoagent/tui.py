"""Transport contract used by a terminal UI without owning Agent semantics."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from .channels import ChannelMessage


class ConfirmationBroker:
    def __init__(self, notify=None):
        self._pending = {}
        self._notify = notify
        self._closed = False
        self._cancelled_turns = set()

    async def request(self, prompt, *, timeout=60.0, turn_id=""):
        if self._closed or turn_id in self._cancelled_turns:
            return "", False
        request_id = "confirm_" + uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (str(prompt), future, turn_id)
        async def wait():
            if self._notify is not None:
                await self._notify({"request_id": request_id, "prompt": str(prompt),
                                    "turn_id": turn_id, "default": False})
            return await future
        try:
            return request_id, await asyncio.wait_for(wait(), timeout=float(timeout))
        except Exception:
            return request_id, False
        finally:
            self._pending.pop(request_id, None)

    def answer(self, request_id, approved):
        if type(approved) is not bool:
            raise ValueError("confirmation answer must be boolean")
        pending = self._pending.get(str(request_id))
        if pending is None or pending[1].done():
            return False
        pending[1].set_result(bool(approved))
        return True

    def pending(self):
        return {key: value[0] for key, value in self._pending.items()}

    def cancel(self, turn_id=None):
        if turn_id is not None:
            self._cancelled_turns.add(turn_id)
        for _, future, owner in self._pending.values():
            if (turn_id is None or owner == turn_id) and not future.done():
                future.set_result(False)

    def close(self):
        self._closed = True
        self.cancel()


class TUITransport:
    def __init__(self, host, *, confirmation_broker=None):
        self.host = host
        self.confirmations = confirmation_broker or ConfirmationBroker()

    async def send(self, session_id, content, *, submission_id=None):
        submission_id = submission_id or "submission_" + uuid4().hex
        return await self.host.submit(
            ChannelMessage(
                channel="tui",
                chat_id=str(session_id),
                sender_id="local-user",
                text=str(content),
                message_id=submission_id,
                conversation=str(session_id),
            )
        )

    def subscribe(self, session_id, callback):
        return self.host.subscribe(session_id, callback)

    def unsubscribe(self, session_id, subscription_id):
        return self.host.unsubscribe(session_id, subscription_id)

    def cancel(self, turn_id):
        return self.host.cancel(turn_id)

    def confirm(self, request_id, approved):
        return self.confirmations.answer(request_id, approved)


async def run_tui(agent, *, input_fn=input, output_fn=print):
    from .runtime_host import RuntimeHost

    host = RuntimeHost(agent)
    transport = TUITransport(host)
    await host.start()
    try:
        while True:
            try:
                content = await asyncio.to_thread(input_fn, "repoagent> ")
            except (EOFError, KeyboardInterrupt, StopIteration):
                return 0
            content = str(content).strip()
            if content in {"/exit", "/quit"}:
                return 0
            if not content:
                continue
            accepted = await transport.send(agent.session["id"], content)
            outcome = await host.wait(accepted["turn_id"])
            if outcome.error:
                output_fn(outcome.error)
            else:
                output_fn(outcome.final_answer)
    finally:
        await host.stop()


__all__ = ["ConfirmationBroker", "TUITransport", "run_tui"]
