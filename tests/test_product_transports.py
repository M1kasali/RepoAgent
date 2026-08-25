import asyncio
import json
import sys

import pytest

import repoagent.gateway as gateway_module
from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext
from repoagent.channels import (
    ChannelIntake,
    ChannelMessage,
    DirectoryChannel,
    normalize_media,
    save_media_bytes,
)
from repoagent.gateway import GatewayAlreadyRunningError, GatewayLease, LocalGateway
from repoagent.runtime_host import RuntimeHost
from repoagent.tui import ConfirmationBroker, TUITransport, run_tui


def _agent(tmp_path, outputs):
    return RepoAgent(
        model_client=FakeModelClient(outputs),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="never",
    )


def test_channel_intake_is_deny_by_default_and_turn_shape_is_stable():
    message = ChannelMessage("chat", "room", "sender", "hello", "message-1")
    denied = ChannelIntake("chat")
    allowed = ChannelIntake("chat", allow_from=("sender",))
    seen = []
    allowed.wire(lambda item: _capture(seen, item))

    async def scenario():
        assert (await denied.publish(message))["reason"] == "sender_denied"
        assert (await allowed.publish(message))["accepted"] is True

    asyncio.run(scenario())
    request = seen[0].to_turn_request()
    assert str(request.session_id) == "chat:room"
    assert request.text == "hello"


async def _capture(target, item):
    target.append(item)
    return {"accepted": True}


def test_tui_send_subscribe_complete_and_confirmation(tmp_path):
    async def scenario():
        host = RuntimeHost(_agent(tmp_path, ["<final>done</final>"]))
        tui = TUITransport(host)
        events = []
        subscription = tui.subscribe("session", events.append)
        accepted = await tui.send("session", "work", submission_id="submission-1")
        outcome = await host.wait(accepted["turn_id"])
        await asyncio.sleep(0)
        assert outcome.final_answer == "done"
        assert [event["type"] for event in events] == [
            "turn.accepted",
            "turn.terminal",
        ]
        assert tui.unsubscribe("session", subscription) is True

        broker = ConfirmationBroker()
        pending = asyncio.create_task(broker.request("approve?", timeout=1))
        await asyncio.sleep(0)
        request_id = next(iter(broker.pending()))
        assert tui.confirm(request_id, True) is False
        assert broker.answer(request_id, True) is True
        assert (await pending)[1] is True
        await host.stop()

    asyncio.run(scenario())


def test_gateway_lease_enforces_single_instance(tmp_path):
    first = GatewayLease(tmp_path)
    second = GatewayLease(tmp_path)
    assert first.acquire()["running"] is True
    with pytest.raises(GatewayAlreadyRunningError):
        second.acquire()
    assert first.release() is True
    assert second.acquire()["running"] is True
    second.release()


def test_pid_probe_avoids_os_kill_on_windows(monkeypatch):
    calls = []
    monkeypatch.setattr(gateway_module.os, "name", "nt")
    monkeypatch.setattr(
        gateway_module, "_windows_pid_alive", lambda pid: calls.append(pid) or True
    )
    monkeypatch.setattr(
        gateway_module.os,
        "kill",
        lambda *_args: (_ for _ in ()).throw(AssertionError("os.kill must not run")),
    )

    assert gateway_module._pid_alive(42) is True
    assert calls == [42]


def test_directory_channel_runs_through_gateway_and_delivers(tmp_path):
    async def scenario():
        agent = _agent(tmp_path / "workspace", ["<final>reply</final>"])
        host = RuntimeHost(agent)
        channel = DirectoryChannel(tmp_path / "channel", poll_interval=0.02)
        gateway = LocalGateway(
            host,
            state_root=tmp_path / "state",
            channels=(channel,),
        )
        await gateway.start()
        inbound = channel.inbox / "one.json"
        inbound.write_text(
            json.dumps(
                {
                    "chat_id": "room",
                    "sender_id": "local",
                    "message_id": "one",
                    "text": "work",
                }
            ),
            encoding="utf-8",
        )
        for _ in range(100):
            if tuple(channel.outbox.glob("*.json")):
                break
            await asyncio.sleep(0.02)
        outbound = json.loads(next(channel.outbox.glob("*.json")).read_text())
        assert outbound["content"] == "reply"
        (channel.inbox / "duplicate.json").write_text(
            json.dumps(
                {
                    "chat_id": "room",
                    "sender_id": "local",
                    "message_id": "one",
                    "text": "work again",
                }
            ),
            encoding="utf-8",
        )
        await asyncio.sleep(0.08)
        assert len(tuple(channel.outbox.glob("*.json"))) == 1
        assert gateway.health()["status"] == "healthy"
        await gateway.stop()

    asyncio.run(scenario())


def test_media_persistence_is_safe_and_transcription_is_optional(tmp_path):
    attachment = save_media_bytes(
        tmp_path,
        "directory",
        b"voice",
        "../../voice.wav",
        mime="audio/wav",
    )
    audio = type(attachment)(**{**attachment.__dict__, "kind": "audio"})
    assert ".." not in audio.path

    class Transcriber:
        async def transcribe(self, item):
            return "transcribed"

    result = asyncio.run(normalize_media((audio,), transcriber=Transcriber()))
    assert result["transcripts"][0]["text"] == "transcribed"


def test_runnable_tui_uses_runtime_host_scheduler(tmp_path):
    inputs = iter(("do work", "/exit"))
    output = []
    result = asyncio.run(
        run_tui(
            _agent(tmp_path, ["<final>done</final>"]),
            input_fn=lambda _prompt: next(inputs),
            output_fn=output.append,
        )
    )
    assert result == 0
    assert output == ["done"]


def test_tui_cancel_reaches_tool_process_and_terminal_state(tmp_path):
    async def scenario():
        command = f'{sys.executable} -c "import time; time.sleep(5)"'
        agent = RepoAgent(
            model_client=FakeModelClient(
                [
                    "<tool>"
                    + json.dumps(
                        {
                            "name": "run_shell",
                            "args": {"command": command, "timeout": 10},
                        }
                    )
                    + "</tool>"
                ]
            ),
            workspace=WorkspaceContext.build(tmp_path),
            session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
            approval_policy="auto",
        )
        host = RuntimeHost(agent)
        tui = TUITransport(host)
        accepted = await tui.send("session", "run slow command", submission_id="slow")
        deadline = asyncio.get_running_loop().time() + 2
        while agent.current_task_state is None:
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("TUI tool turn did not start")
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        assert tui.cancel(accepted["turn_id"]) is True
        outcome = await asyncio.wait_for(host.wait(accepted["turn_id"]), timeout=2)
        assert outcome.state.value == "cancelled"
        assert agent.current_task_state.stop_reason == "tool_cancelled"
        await host.stop()

    asyncio.run(scenario())
