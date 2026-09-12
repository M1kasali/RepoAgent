import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from repoagent.qq_channel import QQChannel, make_sdk_client, normalize_message
from repoagent.gateway import LocalGateway
from repoagent.runtime_host import RuntimeHost
from test_product_transports import _agent


def message(message_id="one", sender="allowed", target="room", content="work"):
    return NS(
        id=message_id,
        author=NS(id=sender, user_openid=sender, member_openid=sender),
        group_openid=target,
        guild_id=target,
        content=content,
        attachments=[],
    )


class FakeClient:
    def __init__(self, channel):
        self.channel = channel
        self.api = self
        self.calls = []
        self.closed = False
        self.fail = False
        self.exit = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self, **kwargs):
        self.channel.ready.set()
        await self.exit.wait()

    async def close(self):
        self.closed = True

    async def record(self, kind, kwargs):
        if self.fail:
            raise RuntimeError("private-sdk-secret")
        self.calls.append((kind, kwargs))

    async def post_c2c_message(self, **kwargs):
        await self.record("c2c", kwargs)

    async def post_group_message(self, **kwargs):
        await self.record("group", kwargs)

    async def post_dms(self, **kwargs):
        await self.record("guild_dm", kwargs)


def channel(**kwargs):
    return QQChannel(
        app_id="id",
        secret="private-sdk-secret",
        allow_from=["allowed"],
        client_factory=FakeClient,
        **kwargs,
    )


@pytest.mark.parametrize("kind", ["c2c", "group", "guild_dm"])
def test_platform_event_runtime_reply_routing_and_duplicate_suppression(tmp_path, kind):
    async def scenario():
        agent = _agent(tmp_path, ["<final>reply</final>", "<final>second</final>"])
        adapter = channel()
        host = RuntimeHost(agent)
        gateway = LocalGateway(host, state_root=tmp_path / "state", channels=[adapter])
        await gateway.start()
        client = adapter.client
        try:
            denied = await adapter.receive(message(sender="denied"), kind)
            assert denied["reason"] == "sender_denied"
            first = await adapter.receive(message(), kind)
            duplicate = await adapter.receive(message(), kind)
            assert duplicate["turn_id"] == first["turn_id"]
            second = await adapter.receive(message("two"), kind)
            await host.wait(first["turn_id"])
            await host.wait(second["turn_id"])
            assert len(client.calls) == 2
            assert [call[1]["msg_id"] for call in client.calls] == ["one", "two"]
            assert all(call[0] == kind for call in client.calls)
            assert [call[1]["content"] for call in client.calls] == ["reply", "second"]
        finally:
            await gateway.stop()
        assert client.closed and adapter.task is None
        assert not gateway.lease.held

    asyncio.run(scenario())


def test_normalization_separates_sessions_and_never_downloads_attachments():
    data = message()
    data.attachments = [
        NS(
            filename="..\\private/file.png",
            url="https://private",
            content_type="image/png",
        )
    ]
    normalized = normalize_message(data, "group")
    assert "metadata only" in normalized.text
    assert "https://" not in normalized.text and "private/" not in normalized.text
    other = normalize_message(message("two"), "group")
    assert normalized.session_id == other.session_id
    assert normalized.chat_id != other.chat_id
    assert normalized.session_id != normalize_message(message(), "guild_dm").session_id
    with pytest.raises(ValueError, match="identity"):
        normalize_message(message(sender=""), "group")


@pytest.mark.parametrize("failure", ["early_exit", "exception", "timeout"])
def test_failed_start_unwinds_without_exposing_sdk_error(failure):
    async def scenario():
        adapter = channel(startup_timeout=0.02)
        client = FakeClient(adapter)

        async def start(**kwargs):
            if failure == "exception":
                raise RuntimeError("private-sdk-secret")
            if failure == "timeout":
                await asyncio.Event().wait()

        client.start = start
        adapter.client_factory = lambda _: client
        with pytest.raises(RuntimeError, match="connection failed") as error:
            await adapter.start()
        assert "private-sdk-secret" not in str(error.value)
        assert client.closed and adapter.task is None and adapter.intake._sealed

    asyncio.run(scenario())


def test_outbound_failure_is_not_reported_as_delivery_success(tmp_path):
    async def scenario():
        adapter = channel()
        host = RuntimeHost(_agent(tmp_path, ["<final>reply</final>"]))
        gateway = LocalGateway(host, state_root=tmp_path / "state", channels=[adapter])
        events = []
        delivered = asyncio.Event()

        def observe(event):
            events.append(event)
            if event["type"] == "turn.terminal":
                delivered.set()

        host.subscribe(normalize_message(message(), "c2c").session_id, observe)
        await gateway.start()
        try:
            adapter.client.fail = True
            accepted = await adapter.receive(message(), "c2c")
            await host.wait(accepted["turn_id"])
            await asyncio.wait_for(delivered.wait(), 2)
            terminal = events[-1]
            assert terminal["delivery"]["status"] == "failed"
            assert "private-sdk-secret" not in json.dumps(terminal)
        finally:
            await gateway.stop()

    asyncio.run(scenario())


def test_gateway_service_supervises_connection_exit_and_cleans_lease(
    tmp_path, monkeypatch
):
    from repoagent.gateway_service import run_gateway
    from repoagent.gateway import GatewayLease
    from repoagent.paths import workspace_state_root

    async def scenario():
        adapter = channel()
        monkeypatch.setenv("REPOAGENT_QQ_APP_ID", "id")
        monkeypatch.setenv("REPOAGENT_QQ_SECRET", "private-sdk-secret")
        monkeypatch.setattr("repoagent.qq_channel.QQChannel", lambda **kwargs: adapter)
        agent = _agent(tmp_path, [])

        def ready(report):
            assert report["channels"] == ["qq"]
            assert "private-sdk-secret" not in json.dumps(report)
            adapter.client.exit.set()

        with pytest.raises(RuntimeError, match="connection stopped"):
            await run_gateway(
                agent,
                channel_kind="qq",
                allow_from=["allowed"],
                stop_event=asyncio.Event(),
                on_ready=ready,
            )
        assert not GatewayLease(workspace_state_root(agent.root)).status()["running"]
        assert not agent._memory_backend_started and adapter.task is None

    asyncio.run(scenario())


def test_qq_cli_selects_platform_without_directory(monkeypatch):
    import repoagent.cli as cli

    captured = {}
    monkeypatch.setattr(cli, "build_agent", lambda args: object())

    async def run(agent, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("repoagent.gateway_service.run_gateway", run)
    assert (
        cli.main(["gateway", "run", "--channel", "qq", "--allow-from", "allowed"]) == 0
    )
    assert captured["channel_kind"] == "qq" and captured["directory"] is None


def test_official_sdk_client_instantiates_and_callbacks_are_wired():
    pytest.importorskip("botpy")

    async def scenario():
        adapter = channel()
        client = make_sdk_client(adapter)
        captured = []

        async def receive(data, kind):
            captured.append(kind)

        adapter.receive = receive
        await client.on_ready()
        assert adapter.ready.is_set()
        await client.on_c2c_message_create(message())
        await client.on_group_at_message_create(message())
        await client.on_direct_message_create(message())
        assert captured == ["c2c", "group", "guild_dm"]
        await client.close()

    asyncio.run(scenario())


def test_sdk_bridge_owns_orphan_tasks_and_keeps_callbacks_on_runtime_loop():
    import threading
    from repoagent.qq_sdk import QQSDKBridge

    async def scenario():
        main_loop = asyncio.get_running_loop()
        previous_handler = main_loop.get_exception_handler()
        heartbeat_stopped = threading.Event()
        received = asyncio.Event()
        worker_threads = []

        class SDK(FakeClient):
            async def start(self, **kwargs):
                worker_threads.append(threading.get_ident())
                asyncio.get_running_loop().set_exception_handler(lambda *args: None)

                async def heartbeat():
                    try:
                        await asyncio.Event().wait()
                    finally:
                        heartbeat_stopped.set()

                asyncio.create_task(heartbeat())
                self.channel.ready.set()
                await self.channel.receive(message(), "group")
                await asyncio.Event().wait()

        adapter = QQChannel(
            app_id="id",
            secret="secret",
            allow_from=["allowed"],
            client_factory=lambda c: QQSDKBridge(c, sdk_factory=SDK),
        )

        async def submit(event):
            assert asyncio.get_running_loop() is main_loop
            received.set()
            return {"accepted": True}

        adapter.intake.wire(submit)
        await adapter.start()
        bridge = adapter.client
        try:
            await asyncio.wait_for(received.wait(), 2)
            await adapter.send(normalize_message(message(), "group").chat_id, "reply")
            assert bridge.client.calls[0][1]["content"] == "reply"
            assert worker_threads[0] != threading.get_ident()
            assert main_loop.get_exception_handler() is previous_handler
        finally:
            await adapter.stop()
        assert bridge.thread is None
        assert heartbeat_stopped.is_set()
        assert main_loop.get_exception_handler() is previous_handler

    asyncio.run(scenario())
