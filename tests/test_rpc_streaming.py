import asyncio
import threading
from dataclasses import replace

import pytest

from repoagent import FakeModelClient
from repoagent.providers.base import ModelEvent
from repoagent.stream_redaction import SecretTextStream
from repoagent.tui_rpc import TUIRPCServer
from test_skills import build_agent
from test_tui_rpc import request


@pytest.mark.parametrize("split", range(1, 17))
def test_secret_split_at_any_boundary_is_redacted(split):
    text = "before sk-secret-value after"
    stream = SecretTextStream(["sk-secret-value"])
    output = stream.feed(text[:split]) + stream.feed(text[split:])
    assert "sk-secret-value" not in output
    assert output == "before <redacted> after"


def test_overlapping_secrets_and_cancelled_suffix():
    stream = SecretTextStream(["abc", "abcdef"])
    assert stream.feed("abc") == ""
    assert stream.feed("def!") == "<redacted>!"
    assert stream.feed("ab") == ""
    stream.discard()
    assert stream.feed("safe!") == "safe!"


def test_successful_end_flushes_nonsecret_suffix_but_redacts_complete_match():
    stream = SecretTextStream(["example-private-key"])
    assert stream.feed("done") == "don"
    assert stream.finish(complete=True) == "e"
    assert stream.finish(complete=True) == ""
    assert stream.feed("late") == ""
    overlap = SecretTextStream(["abc", "abcdef"])
    assert overlap.feed("abc") == ""
    assert overlap.finish(complete=True) == "<redacted>"


def test_abnormal_end_discards_possible_secret_prefix():
    stream = SecretTextStream(["example-private-key"])
    assert stream.feed("example-pri") == ""
    assert stream.finish(complete=False) == ""
    assert stream.feed("vate-key") == ""


def test_complete_runtime_answer_keeps_ordinary_secret_prefix_suffix(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "example-private-key")
    agent = build_agent(tmp_path, ["<final>done</final>"])
    assert agent.ask("finish") == "done"
    events = agent.run_store.load_turn_events(agent.current_task_state.run_id)
    assert "".join(event["payload"]["content"] for event in events
                   if event["kind"] == "runner.text") == "done"


@pytest.mark.parametrize("reason,expected", [
    ("stop", "done"), ("end_turn", "done"), ("length", "don"), ("unknown", "don"),
])
def test_runtime_tail_requires_confirmed_normal_end(tmp_path, monkeypatch, reason, expected):
    monkeypatch.setenv("TEST_API_KEY", "example-private-key")

    class Client(FakeModelClient):
        def generate(self, request):
            return replace(super().generate(request), finish_reason=reason)

    agent = build_agent(tmp_path, [])
    agent.model_client = Client(["<final>done</final>"])
    assert agent.ask("finish") == expected


@pytest.mark.parametrize("split", range(1, 20))
def test_finalized_stream_matches_full_literal_redaction(split):
    text = "abc abcdef done"
    stream = SecretTextStream(["abc", "abcdef", "example-private-key"])
    output = stream.feed(text[:split]) + stream.feed(text[split:])
    output += stream.finish(complete=True)
    assert output == "<redacted> <redacted> done"


def test_rpc_delivers_increment_before_model_finishes(tmp_path):
    async def scenario():
        release = threading.Event()
        arrived = asyncio.Event()
        frames = []

        class StreamingClient(FakeModelClient):
            def stream(self, request):
                result = self.generate(request)
                yield ModelEvent(kind="text_delta", text="<final>hello ")
                if not release.wait(timeout=3):
                    raise RuntimeError("test failed to release model")
                yield ModelEvent(kind="text_delta", text="world</final>")
                yield ModelEvent(kind="completed", result=result)

        agent = build_agent(tmp_path, [])
        agent.model_client = StreamingClient(["<final>hello world</final>"])

        async def send(frame):
            frames.append(frame)
            if (
                frame.get("method") == "turn.event"
                and frame["params"]["type"] == "turn.text.delta"
            ):
                arrived.set()

        server = TUIRPCServer(agent, send)
        await server.start()
        try:
            await server.receive(request("turn.subscribe", {"stream": True}))
            await server.receive(
                request("turn.send", {"content": "hello", "submission_id": "one"}, 2)
            )
            await asyncio.wait_for(arrived.wait(), 2)
            assert server.host.busy
            assert not any(
                frame.get("params", {}).get("type") == "turn.terminal"
                for frame in frames
            )
            turn_id = next(
                frame["result"]["turn_id"] for frame in frames if frame.get("id") == 2
            )
            release.set()
            await server.host.wait(turn_id)
            await asyncio.sleep(0)
            events = [
                frame["params"]
                for frame in frames
                if frame.get("method") == "turn.event"
            ]
            deltas = [event for event in events if event["type"] == "turn.text.delta"]
            assert "".join(event["text"] for event in deltas) == "hello world"
            assert [event["sequence"] for event in deltas] == list(
                range(1, len(deltas) + 1)
            )
            assert events[-1]["type"] == "turn.terminal"
            assert events[-1]["outcome"]["final_answer"] == "hello world"
        finally:
            release.set()
            await server.close()

    asyncio.run(scenario())


def test_rpc_stream_does_not_leak_split_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-private-key")

    async def scenario():
        class Client(FakeModelClient):
            def stream(self, request):
                result = self.generate(request)
                for part in ("<final>key: sk-", "private", "-key!</final>"):
                    yield ModelEvent(kind="text_delta", text=part)
                yield ModelEvent(kind="completed", result=result)

        agent = build_agent(tmp_path, [])
        agent.model_client = Client(["<final>key: sk-private-key!</final>"])
        frames = []

        async def send(frame):
            frames.append(frame)

        server = TUIRPCServer(agent, send)
        await server.start()
        try:
            await server.receive(request("turn.subscribe", {"stream": True}))
            await server.receive(
                request("turn.send", {"content": "show key", "submission_id": "one"}, 2)
            )
            turn = next(
                frame["result"]["turn_id"] for frame in frames if frame.get("id") == 2
            )
            await server.host.wait(turn)
            await asyncio.sleep(0)
            text = "".join(
                frame["params"]["text"]
                for frame in frames
                if frame.get("params", {}).get("type") == "turn.text.delta"
            )
            assert text == "key: <redacted>!"
            recorded = agent.run_store.load_turn_events(turn)
            persisted = "".join(
                event["payload"]["content"]
                for event in recorded
                if event["kind"] == "runner.text"
            )
            assert persisted == text
        finally:
            await server.close()

    asyncio.run(scenario())


def test_late_preview_after_terminal_is_ignored(tmp_path):
    async def scenario():
        from types import SimpleNamespace

        agent = build_agent(tmp_path, ["<final>done</final>"])
        frames = []

        async def send(frame):
            frames.append(frame)

        server = TUIRPCServer(agent, send)
        await server.start()
        try:
            await server.receive(request("turn.subscribe", {"stream": True}))
            await server.receive(
                request("turn.send", {"content": "work", "submission_id": "one"}, 2)
            )
            turn = next(
                frame["result"]["turn_id"] for frame in frames if frame.get("id") == 2
            )
            await server.host.wait(turn)
            await asyncio.sleep(0)
            count = len(frames)
            await server.host._publish_text(
                SimpleNamespace(
                    turn_id=turn, session_id=server.session_id, request_id="old"
                ),
                "late",
            )
            assert len(frames) == count
            await server.receive(request("turn.subscribe", {"stream": "false"}, 3))
            assert frames[-1]["error"]["code"] == -32602
        finally:
            await server.close()

    asyncio.run(scenario())
