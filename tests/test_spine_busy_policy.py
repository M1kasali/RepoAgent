import asyncio
import json

import pytest

from repoagent.run_store import RunStore
from repoagent.spine import Scheduler, TurnOutcome, TurnRequest, TurnRuntime, TurnState, Usage


class ControlledRunner:
    def __init__(self, *, consume=True, fail=False):
        self.consume = consume
        self.fail = fail
        self.entered = asyncio.Event()
        self.read = asyncio.Event()
        self.drained = asyncio.Event()
        self.release = asyncio.Event()
        self.order = []
        self.injected = []

    async def run(self, request, emit, drain):
        self.order.append(request.text)
        if request.text == "host":
            self.entered.set()
            await self.read.wait()
            if self.consume:
                self.injected.extend(drain())
            self.drained.set()
            await self.release.wait()
            if self.fail:
                raise ValueError("host failed")
        return TurnOutcome(
            turn_id=request.turn_id, request_id=request.request_id,
            session_id=request.session_id, state=TurnState.COMPLETED,
            final_answer="done", explicit_reply=True, usage=Usage(10, 2, 12),
        )


def request(text, busy="append"):
    from repoagent.spine import BusyPolicy
    return TurnRequest.create(session_id="same", text=text, busy=BusyPolicy(busy))


@pytest.mark.parametrize("ending", ["complete", "fail", "cancel"])
def test_injected_request_follows_host_terminal_without_double_accounting(tmp_path, ending):
    async def scenario():
        runner = ControlledRunner(fail=ending == "fail")
        store = RunStore(tmp_path / "runs")
        scheduler = Scheduler(TurnRuntime(runner, store))
        host = scheduler.submit(request("host"))
        try:
            await asyncio.wait_for(runner.entered.wait(), 1)
            injected = scheduler.submit(request("injected", "inject"))
            runner.read.set()
            await asyncio.wait_for(runner.drained.wait(), 1)
            injected.cancel()  # Already merged: must not cancel its host.
            assert not host.done and not injected.done
            if ending == "cancel":
                host.cancel()
            else:
                runner.release.set()
            left, right = await asyncio.wait_for(asyncio.gather(host.result(), injected.result()), 1)
            assert left.state == right.state
            assert right.turn_id == injected.turn_id
            assert right.usage.total_tokens == 0
            assert not right.explicit_reply and right.final_answer == ""
            assert runner.order == ["host"]
            assert [item.text for item in runner.injected] == ["injected"]
            saved = json.loads(store.turn_path(right.turn_id).read_text())
            assert saved["state"] == right.state.value
            events = [json.loads(line) for line in store.turn_events_path(right.turn_id).read_text().splitlines()]
            assert [event["kind"] for event in events] == ["turn.accepted", "turn.merged", "turn." + right.state.value]
            assert events[1]["payload"]["host_turn_id"] == host.turn_id
        finally:
            runner.read.set()
            runner.release.set()
            await scheduler.shutdown(grace=0)
    asyncio.run(scenario())


@pytest.mark.parametrize("busy", ["inject", "interrupt"])
def test_idle_busy_policy_executes_as_normal_turn(tmp_path, busy):
    async def scenario():
        runner = ControlledRunner()
        scheduler = Scheduler(TurnRuntime(runner, RunStore(tmp_path / "runs")))
        handle = scheduler.submit(request("idle", busy))
        assert (await asyncio.wait_for(handle.result(), 1)).explicit_reply
        await scheduler.shutdown()
        assert runner.order == ["idle"]
    asyncio.run(scenario())


def test_injection_requires_terminal_owner_before_acceptance():
    async def scenario():
        class Unsupported:
            def accept(self, request):
                raise AssertionError("unsupported request must not be accepted")
        scheduler = Scheduler(Unsupported())
        with pytest.raises(TypeError, match="complete_injected"):
            scheduler.submit(request("injected", "inject"))
        await scheduler.shutdown()
    asyncio.run(scenario())


def test_busy_policy_rejects_unknown_value():
    with pytest.raises(ValueError):
        TurnRequest.create(session_id="s", text="x", busy="typo")


def test_injected_completion_failure_resolves_handle_with_error(tmp_path):
    async def scenario():
        class BrokenCompletion(TurnRuntime):
            async def complete_injected(self, request, host_outcome):
                raise OSError("injected terminal unavailable")
        runner = ControlledRunner()
        scheduler = Scheduler(BrokenCompletion(runner, RunStore(tmp_path / "runs")))
        host = scheduler.submit(request("host"))
        await asyncio.wait_for(runner.entered.wait(), 1)
        injected = scheduler.submit(request("injected", "inject"))
        runner.read.set()
        await asyncio.wait_for(runner.drained.wait(), 1)
        runner.release.set()
        with pytest.raises(OSError, match="terminal unavailable"):
            await asyncio.wait_for(injected.result(), 1)
        assert (await asyncio.wait_for(host.result(), 1)).state is TurnState.COMPLETED
        await scheduler.shutdown()
        assert not scheduler.has_work("same")
    asyncio.run(scenario())


def test_unread_injection_falls_back_after_existing_queue(tmp_path):
    async def scenario():
        runner = ControlledRunner(consume=False)
        scheduler = Scheduler(TurnRuntime(runner, RunStore(tmp_path / "runs")))
        host = scheduler.submit(request("host"))
        await asyncio.wait_for(runner.entered.wait(), 1)
        queued = scheduler.submit(request("queued"))
        injected = scheduler.submit(request("injected", "inject"))
        runner.read.set()
        runner.release.set()
        await asyncio.wait_for(asyncio.gather(host.result(), queued.result(), injected.result()), 1)
        await scheduler.shutdown()
        assert runner.order == ["host", "queued", "injected"]
    asyncio.run(scenario())


def test_interrupt_precedes_backlog_and_waits_for_host_cancellation(tmp_path):
    async def scenario():
        runner = ControlledRunner()
        scheduler = Scheduler(TurnRuntime(runner, RunStore(tmp_path / "runs")))
        host = scheduler.submit(request("host"))
        await asyncio.wait_for(runner.entered.wait(), 1)
        queued = scheduler.submit(request("queued"))
        interrupt = scheduler.submit(request("interrupt", "interrupt"))
        outcomes = await asyncio.wait_for(asyncio.gather(host.result(), queued.result(), interrupt.result()), 1)
        await scheduler.shutdown()
        assert outcomes[0].state is TurnState.CANCELLED
        assert runner.order == ["host", "interrupt", "queued"]
    asyncio.run(scenario())


@pytest.mark.parametrize("shutdown", [False, True])
def test_unread_injection_cancel_and_shutdown_resolve_handle(tmp_path, shutdown):
    async def scenario():
        runner = ControlledRunner()
        scheduler = Scheduler(TurnRuntime(runner, RunStore(tmp_path / "runs")))
        host = scheduler.submit(request("host"))
        await asyncio.wait_for(runner.entered.wait(), 1)
        injected = scheduler.submit(request("injected", "inject"))
        if shutdown:
            await asyncio.wait_for(scheduler.shutdown(grace=0), 1)
        else:
            injected.cancel()
            runner.read.set()
            runner.release.set()
        assert (await asyncio.wait_for(injected.result(), 1)).state is TurnState.CANCELLED
        await asyncio.wait_for(host.result(), 1)
        await scheduler.shutdown()
        assert not scheduler.has_work("same")
        assert runner.injected == []
    asyncio.run(scenario())


@pytest.mark.parametrize("structured", [False, True])
def test_real_agent_consumes_injection_at_next_model_boundary(tmp_path, structured):
    import threading
    from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext
    from repoagent.agent_turn_runner import AgentTurnRunner

    class PausedClient(FakeModelClient):
        def __init__(self):
            super().__init__([
                '<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":1}}</tool>',
                "<final>done</final>",
            ])
            self.supports_structured_messages = structured
            self.entered = threading.Event()
            self.release = threading.Event()
            self.requests = []

        def stream(self, model_request):
            self.requests.append(model_request)
            if len(self.requests) == 1:
                self.entered.set()
                assert self.release.wait(3)
            yield from super().stream(model_request)

    async def scenario():
        (tmp_path / "README.md").write_text("sample\n")
        client = PausedClient()
        agent = RepoAgent(
            model_client=client,
            workspace=WorkspaceContext.build(tmp_path, repo_root_override=tmp_path),
            session_store=SessionStore(tmp_path / "sessions"), approval_policy="auto",
        )
        await agent.memory_backend.start()
        scheduler = Scheduler(TurnRuntime(AgentTurnRunner(agent), agent.run_store))
        host = scheduler.submit(request("host"))
        try:
            if not await asyncio.to_thread(client.entered.wait, 3):
                pytest.fail(str(await asyncio.wait_for(host.result(), 1)))
            injected = scheduler.submit(request("INJECTION_MARKER_123", "inject"))
            client.release.set()
            outcomes = await asyncio.wait_for(asyncio.gather(host.result(), injected.result()), 5)
            assert all(outcome.state is TurnState.COMPLETED for outcome in outcomes)
            assert len(client.requests) == 2
            second = client.requests[1]
            assert "INJECTION_MARKER_123" in (
                "\n".join(message.content for message in second.messages)
                if structured else second.prompt
            )
            history = agent.session["history"]
            assert sum(item.get("content") == "INJECTION_MARKER_123" for item in history) == 1
            assert outcomes[1].usage.total_tokens == 0
        finally:
            client.release.set()
            await scheduler.shutdown(grace=0)
            agent.skill_watcher.stop()
            await agent.memory_backend.stop()
    asyncio.run(scenario())
