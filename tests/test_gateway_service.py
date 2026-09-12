import asyncio
import json

import pytest

from repoagent.channels import ChannelIntake, DirectoryChannel
from repoagent.gateway import GatewayLease, LocalGateway
from repoagent.gateway_service import run_gateway
from repoagent.paths import workspace_state_root
from repoagent.runtime_host import RuntimeHost
from test_product_transports import _agent


def payload(**changes):
    return {
        "chat_id": "room",
        "sender_id": "local",
        "message_id": "one",
        "text": "work",
        **changes,
    }


def prepare(root):
    channel = DirectoryChannel(root)
    for directory in (
        channel.inbox,
        channel.outbox,
        channel.processed,
        channel.rejected,
    ):
        directory.mkdir(parents=True)
    return channel


def test_bad_and_denied_messages_do_not_block_valid_messages(tmp_path):
    async def scenario():
        channel = prepare(tmp_path)
        seen = []

        async def submit(message):
            seen.append(message.text)
            return {"accepted": True}

        channel.intake.wire(submit)
        (channel.inbox / "bad.json").write_text("{bad")
        (channel.inbox / "denied.json").write_text(
            json.dumps(payload(sender_id="other"))
        )
        (channel.inbox / "null.json").write_text(json.dumps(payload(sender_id=None)))
        (channel.inbox / "valid.json").write_text(json.dumps(payload()))
        assert await channel.poll_once() == 1
        assert seen == ["work"]
        assert len(list(channel.rejected.glob("*.json"))) == 3
        assert len(list(channel.processed.glob("*.json"))) == 1

    asyncio.run(scenario())


def test_transient_submit_failure_preserves_inbox_for_retry(tmp_path):
    async def scenario():
        channel = prepare(tmp_path)
        path = channel.inbox / "one.json"
        path.write_text(json.dumps(payload()))
        calls = 0

        async def submit(message):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("private failure")
            return {"accepted": True}

        channel.intake.wire(submit)
        assert await channel.poll_once() == 0
        assert path.exists() and channel.last_error == "RuntimeError"
        assert await channel.poll_once() == 1
        assert not path.exists()

    asyncio.run(scenario())


def test_unwired_message_stays_pending_and_channel_restart_reopens_intake(tmp_path):
    async def scenario():
        channel = prepare(tmp_path)
        path = channel.inbox / "one.json"
        path.write_text(json.dumps(payload()))
        assert await channel.poll_once() == 0
        assert path.exists()
        await channel.start()
        task = channel._task
        assert await channel.start() is False
        assert channel._task is task
        await channel.stop()
        await channel.start()
        assert not channel.intake._sealed
        await channel.stop()

    asyncio.run(scenario())


def test_gateway_failed_start_unwinds_all_started_resources(tmp_path):
    events = []

    class Host:
        async def start(self):
            events.append("host.start")

        async def stop(self, grace=5):
            events.append("host.stop")

    class Channel:
        def __init__(self, name, fail=False):
            self.name, self.fail = name, fail
            self.intake = ChannelIntake(name)

        async def start(self):
            events.append(self.name + ".start")
            if self.fail:
                raise ValueError("start failed")

        async def stop(self):
            events.append(self.name + ".stop")
            if self.fail:
                raise RuntimeError("stop failed")

    gateway = LocalGateway(
        Host(), state_root=tmp_path, channels=[Channel("one"), Channel("two", True)]
    )
    with pytest.raises(ValueError, match="start failed"):
        asyncio.run(gateway.start())
    assert events == [
        "host.start",
        "one.start",
        "two.start",
        "two.stop",
        "one.stop",
        "host.stop",
    ]
    assert not gateway.lease.held and not gateway.running


def test_subscriber_failure_does_not_prevent_delivery(tmp_path):
    async def scenario():
        from repoagent.channels import ChannelMessage

        host = RuntimeHost(_agent(tmp_path, ["<final>reply</final>"]))
        delivered, events = [], []

        def broken(event):
            raise ValueError("observer failure")

        async def deliver(chat_id, content, media):
            delivered.append(content)

        host.subscribe("session", broken)
        host.subscribe("session", events.append)
        try:
            accepted = await host.submit(
                ChannelMessage("test", "room", "local", "work", "one", "session"),
                deliver=deliver,
            )
            await host.wait(accepted["turn_id"])
        finally:
            await host.stop()
        assert delivered == ["reply"]
        assert host.subscriber_failure_count == 2
        assert [event["type"] for event in events] == ["turn.accepted", "turn.terminal"]

    asyncio.run(scenario())


def test_gateway_service_end_to_end_and_clean_stop(tmp_path):
    async def scenario():
        agent = _agent(tmp_path / "workspace", ["<final>reply</final>"])
        stopped, ready = asyncio.Event(), asyncio.Event()
        root = tmp_path / "channel"

        def on_ready(report):
            assert report["status"] == "healthy"
            (root / "inbox" / "one.json").write_text(json.dumps(payload()))
            ready.set()

        task = asyncio.create_task(
            run_gateway(
                agent,
                directory=root,
                allow_from=["local"],
                stop_event=stopped,
                on_ready=on_ready,
            )
        )
        try:
            await asyncio.wait_for(ready.wait(), 2)
            for _ in range(100):
                files = list((root / "outbox").glob("*.json"))
                if files:
                    assert json.loads(files[0].read_text())["content"] == "reply"
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("no reply delivered")
        finally:
            stopped.set()
            assert await asyncio.wait_for(task, 3) == 0
        assert not GatewayLease(workspace_state_root(agent.root)).status()["running"]
        assert not agent._memory_backend_started

    asyncio.run(scenario())


def test_gateway_cli_routes_agent_options_and_safe_default(monkeypatch, tmp_path):
    import repoagent.cli as cli

    captured = {}

    def build(args):
        captured["args"] = args
        return object()

    async def run(agent, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "build_agent", build)
    monkeypatch.setattr("repoagent.gateway_service.run_gateway", run)
    assert (
        cli.main(
            [
                "gateway",
                "run",
                "--directory",
                "queue",
                "--allow-from",
                "local",
                "--",
                "--cwd",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert captured["args"].approval == "never"
    assert captured["args"].cwd == str(tmp_path)
    assert captured["allow_from"] == ["local"]
    assert (
        cli.main(
            [
                "gateway",
                "run",
                "--directory",
                "queue",
                "--allow-from",
                "local",
                "--",
                "--approval",
                "ask",
            ]
        )
        == 2
    )


def test_competing_gateway_leases_have_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from repoagent.gateway import GatewayAlreadyRunningError

    leases = [GatewayLease(tmp_path) for _ in range(4)]
    barrier = Barrier(len(leases))

    def acquire(lease):
        barrier.wait(timeout=3)
        try:
            lease.acquire()
            return True
        except GatewayAlreadyRunningError:
            return False

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert sum(pool.map(acquire, leases)) == 1
    finally:
        for lease in leases:
            lease.release()


def test_gateway_service_cancellation_releases_resources(tmp_path):
    async def scenario():
        agent = _agent(tmp_path / "workspace", [])
        ready = asyncio.Event()
        task = asyncio.create_task(
            run_gateway(
                agent,
                directory=tmp_path / "queue",
                allow_from=["local"],
                stop_event=asyncio.Event(),
                on_ready=lambda report: ready.set(),
            )
        )
        await asyncio.wait_for(ready.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert not GatewayLease(workspace_state_root(agent.root)).status()["running"]
        assert not agent._memory_backend_started

    asyncio.run(scenario())


def test_inbox_symlink_is_quarantined_without_reading_target(tmp_path):
    async def scenario():
        channel = prepare(tmp_path / "queue")
        target = tmp_path / "private.json"
        target.write_text(json.dumps(payload()))
        (channel.inbox / "link.json").symlink_to(target)
        assert await channel.poll_once() == 0
        assert (channel.rejected / "link.json").is_symlink()
        assert target.exists()

    asyncio.run(scenario())


def test_media_generator_is_not_consumed_before_serialization(tmp_path):
    channel = prepare(tmp_path)
    asyncio.run(channel.send("room", "reply", (x for x in ["a.png", "b.png"])))
    result = json.loads(next(channel.outbox.glob("*.json")).read_text())
    assert result["media"] == ["a.png", "b.png"]
