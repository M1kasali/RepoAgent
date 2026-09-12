import asyncio
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

from repoagent.tui import ConfirmationBroker
from repoagent.tui_rpc import TUIRPCServer, run_tui_rpc
from test_skills import build_agent


def request(method, params=None, request_id=1):
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    )


def test_rpc_send_subscribe_dedup_and_terminal_event(tmp_path):
    async def scenario():
        agent = build_agent(tmp_path, ["<final>done</final>"])
        frames = []

        async def send(frame):
            frames.append(frame)

        server = TUIRPCServer(agent, send)
        await server.start()
        try:
            await server.receive(request("system.initialize"))
            assert frames[-1]["result"]["session_id"] == agent.session["id"]
            await server.receive(request("turn.subscribe", request_id=2))
            subscription = frames[-1]["result"]["subscription_id"]
            await server.receive(
                request("turn.send", {"content": "work", "submission_id": "one"}, 3)
            )
            accepted = next(frame["result"] for frame in frames if frame.get("id") == 3)
            await server.host.wait(accepted["turn_id"])
            await asyncio.sleep(0)
            await server.receive(
                request("turn.send", {"content": "work", "submission_id": "one"}, 4)
            )
            assert frames[-1]["result"]["duplicate"]
            assert len(agent.model_client.prompts) == 1
            events = [
                frame["params"]["type"]
                for frame in frames
                if frame.get("method") == "turn.event"
            ]
            assert events == ["turn.accepted", "turn.terminal"]
            await server.receive(
                request("turn.send", {"content": "other", "submission_id": "one"}, 5)
            )
            assert frames[-1]["error"]["code"] == -32602
            await server.receive(
                request("turn.cancel", {"turn_id": accepted["turn_id"]}, 6)
            )
            assert not frames[-1]["result"]["cancelled"]
            await server.receive(
                request("turn.unsubscribe", {"subscription_id": subscription}, 7)
            )
            assert frames[-1]["result"]["unsubscribed"]
        finally:
            await server.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "wire,code",
    [
        ("{broken", -32700),
        ("[]", -32600),
        (request("missing"), -32601),
        (request("turn.send", {"content": "work"}), -32602),
        (
            request("confirm.respond", {"request_id": "one", "approved": "false"}),
            -32602,
        ),
        (request("turn.subscribe", {"session_id": "other"}), -32602),
    ],
)
def test_protocol_errors_are_structured(tmp_path, wire, code):
    async def scenario():
        frames = []

        async def send(frame):
            frames.append(frame)

        server = TUIRPCServer(build_agent(tmp_path, []), send)
        await server.start()
        try:
            await server.receive(wire)
            assert frames[-1]["error"]["code"] == code
        finally:
            await server.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("approved", [True, False])
def test_rpc_confirmation_controls_real_tool_execution(tmp_path, approved):
    async def scenario():
        outputs = [
            '<tool>{"name":"write_file","args":{"path":"result.txt","content":"approved"}}</tool>',
            "<final>done</final>",
        ]
        agent = build_agent(tmp_path, outputs)
        agent.approval_engine.mode = "ask"
        frames = []
        confirmation = asyncio.Event()

        async def send(frame):
            frames.append(frame)
            if frame.get("method") == "confirm.request":
                confirmation.set()

        server = TUIRPCServer(agent, send, confirmation_timeout=2)
        previous = agent.approval_engine._prompt
        await server.start()
        try:
            await server.receive(
                request("turn.send", {"content": "write file", "submission_id": "one"})
            )
            turn_id = next(
                frame["result"]["turn_id"] for frame in frames if "result" in frame
            )
            await asyncio.wait_for(confirmation.wait(), 2)
            params = next(
                frame["params"]
                for frame in frames
                if frame.get("method") == "confirm.request"
            )
            assert params["default"] is False and params["turn_id"] == turn_id
            assert not (tmp_path / "result.txt").exists()
            await server.receive(
                request(
                    "confirm.respond",
                    {"request_id": params["request_id"], "approved": approved},
                    2,
                )
            )
            await asyncio.wait_for(server.host.wait(turn_id), 3)
            assert (tmp_path / "result.txt").exists() == approved
        finally:
            await server.close()
        assert agent.approval_engine._prompt == previous

    asyncio.run(scenario())


def test_cancel_pending_confirmation_denies_and_drains(tmp_path):
    async def scenario():
        agent = build_agent(
            tmp_path,
            [
                '<tool>{"name":"write_file","args":{"path":"no.txt","content":"no"}}</tool>',
                "<final>done</final>",
            ],
        )
        agent.approval_engine.mode = "ask"
        frames, confirmation = [], asyncio.Event()

        async def send(frame):
            frames.append(frame)
            if frame.get("method") == "confirm.request":
                confirmation.set()

        server = TUIRPCServer(agent, send)
        await server.start()
        try:
            await server.receive(
                request("turn.send", {"content": "write", "submission_id": "one"})
            )
            turn_id = next(
                frame["result"]["turn_id"] for frame in frames if "result" in frame
            )
            await asyncio.wait_for(confirmation.wait(), 2)
            await asyncio.wait_for(
                server.receive(request("turn.cancel", {"turn_id": turn_id}, 2)), 3
            )
            assert not (tmp_path / "no.txt").exists()
            assert not server.confirmations.pending()
        finally:
            await server.close()

    asyncio.run(scenario())


def test_confirmation_timeout_disconnect_and_strict_boolean():
    async def scenario():
        broker = ConfirmationBroker()
        assert (await broker.request("approve", timeout=0.01))[1] is False
        task = asyncio.create_task(broker.request("approve", timeout=10))
        await asyncio.sleep(0)
        key = next(iter(broker.pending()))
        with pytest.raises(ValueError):
            broker.answer(key, "false")
        broker.close()
        assert (await task)[1] is False
        assert not broker.answer(key, True)
        assert (await broker.request("late"))[1] is False

    asyncio.run(scenario())


def test_stdio_initialize_and_eof_cleanup(tmp_path):
    reader = io.BytesIO((request("system.initialize") + "\n").encode())
    writer = io.StringIO()
    agent = build_agent(tmp_path, [])
    assert asyncio.run(run_tui_rpc(agent, reader=reader, writer=writer)) == 0
    assert json.loads(writer.getvalue())["result"]["protocol"] == "repoagent.tui-rpc/v1"
    assert not agent._memory_backend_started


def test_stdio_oversized_frame_fails_closed(tmp_path):
    writer = io.StringIO()
    assert (
        asyncio.run(
            run_tui_rpc(
                build_agent(tmp_path, []),
                reader=io.BytesIO(b"x" * (1024 * 1024 + 1)),
                writer=writer,
            )
        )
        == 2
    )
    assert json.loads(writer.getvalue())["error"]["code"] == -32600


def test_broker_task_cancellation_propagates_and_cleans_pending():
    async def scenario():
        broker = ConfirmationBroker()
        task = asyncio.create_task(broker.request("approve"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not broker.pending()

    asyncio.run(scenario())


def test_connection_close_denies_pending_real_approval(tmp_path):
    async def scenario():
        agent = build_agent(
            tmp_path,
            [
                '<tool>{"name":"write_file","args":{"path":"no.txt","content":"no"}}</tool>',
                "<final>done</final>",
            ],
        )
        agent.approval_engine.mode = "ask"
        confirmation = asyncio.Event()

        async def send(frame):
            if frame.get("method") == "confirm.request":
                confirmation.set()

        server = TUIRPCServer(agent, send)
        await server.start()
        await server.receive(
            request("turn.send", {"content": "write", "submission_id": "one"})
        )
        await asyncio.wait_for(confirmation.wait(), 2)
        await asyncio.wait_for(server.close(), 3)
        assert not (tmp_path / "no.txt").exists()
        assert not server.confirmations.pending()
        assert not agent._memory_backend_started

    asyncio.run(scenario())


def test_broken_output_denies_confirmation_and_marks_transport_failed(tmp_path):
    async def scenario():
        async def broken(frame):
            raise BrokenPipeError()

        server = TUIRPCServer(build_agent(tmp_path, []), broken)
        await server.start()
        try:
            result = await server.confirmations.request("approve", timeout=1)
            assert result[1] is False
            assert server.closed and server.transport_failed
        finally:
            await server.close()

    asyncio.run(scenario())


def test_cli_rpc_entry_forwards_runtime_options(monkeypatch, tmp_path):
    import repoagent.cli as cli

    captured = {}

    def build(args):
        captured["args"] = args
        return object()

    async def run(agent, *, session_factory, model_selection):
        captured["ran"] = True
        captured["factory"] = session_factory
        return 0

    monkeypatch.setattr(cli, "build_agent", build)
    monkeypatch.setattr("repoagent.tui_rpc.run_tui_rpc", run)
    assert cli.main(["tui", "--rpc", "--", "--cwd", str(tmp_path)]) == 0
    assert captured["ran"] and captured["args"].cwd == str(tmp_path)
    assert captured["args"].approval == "ask"
    captured["factory"]("saved-session")
    assert captured["args"].resume == "saved-session"


def test_stdio_runs_in_separate_process_and_exits_on_eof(tmp_path):
    code = """
import asyncio, sys
from pathlib import Path
from repoagent import RepoAgent, FakeModelClient, WorkspaceContext, SessionStore
from repoagent.tui_rpc import run_tui_rpc
root = Path(sys.argv[1])
agent = RepoAgent(model_client=FakeModelClient([]), workspace=WorkspaceContext.build(root),
    session_store=SessionStore(root / ".repoagent" / "sessions"), approval_policy="ask")
raise SystemExit(asyncio.run(run_tui_rpc(agent)))
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        input=request("system.initialize") + "\n",
        text=True,
        capture_output=True,
        timeout=10,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["result"]["protocol"] == "repoagent.tui-rpc/v1"
