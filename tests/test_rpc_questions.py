import asyncio
import json

import pytest

from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext
from repoagent.questions import QuestionBroker
from repoagent.tui_rpc import RpcError, TUIRPCServer


def question_agent(root, questions=None):
    questions = questions or [{"question": "Which color?", "options": ["red", "green"]}]
    return RepoAgent(
        model_client=FakeModelClient(
            [
                "<tool>"
                + json.dumps({"name": "ask_user", "args": {"questions": questions}})
                + "</tool>",
                "<final>done</final>",
            ]
        ),
        workspace=WorkspaceContext.build(root),
        session_store=SessionStore(root / ".repoagent" / "sessions"),
        approval_policy="never",
        enable_questions=True,
    )


def test_question_broker_overlap_timeout_duplicate_and_cancel():
    async def scenario():
        frames = []

        async def send(params):
            frames.append(params)

        broker = QuestionBroker(send)
        first = asyncio.create_task(broker.ask("session", "one", []))
        await asyncio.sleep(0.01)
        second = asyncio.create_task(broker.ask("session", "two", []))
        await asyncio.sleep(0.01)
        assert await first == ""
        assert not broker.reply(frames[0]["request_id"], "stale")
        assert broker.reply(frames[1]["request_id"], "custom")
        assert not broker.reply(frames[1]["request_id"], "duplicate")
        assert await second == "custom"
        assert await broker.ask("session", "timeout", [], timeout=0.01) == ""
        pending = asyncio.create_task(broker.ask("session", "cancel", []))
        await asyncio.sleep(0.01)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not broker.pending
        broker.close()
        assert await broker.ask("session", "closed", []) == ""

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["answer", "skip", "timeout", "cancel", "close"])
def test_rpc_real_question_tool_round_trip(tmp_path, action):
    async def scenario():
        frames = []
        arrived = asyncio.Event()

        async def send(frame):
            frames.append(frame)
            if frame.get("method") == "clarify.request":
                arrived.set()

        agent = question_agent(tmp_path)
        server = TUIRPCServer(
            agent, send, question_timeout=0.2 if action == "timeout" else 5
        )
        await server.start()
        try:
            assert "ask_user" in agent.capability_scope()["tools"]
            accepted = await server.dispatch(
                "turn.send", {"content": "choose", "submission_id": "one"}
            )
            await asyncio.wait_for(arrived.wait(), 2)
            question = frames[-1]["params"]
            assert question["choices"] == ["red", "green"]
            assert server.host.busy
            if action in {"answer", "skip"}:
                for invalid in (True, 1, [], None, "x" * 16001):
                    with pytest.raises(RpcError):
                        await server.dispatch(
                            "clarify.respond",
                            {"request_id": question["request_id"], "answer": invalid},
                        )
                result = await server.dispatch(
                    "clarify.respond",
                    {
                        "request_id": question["request_id"],
                        "answer": "blue" if action == "answer" else "",
                    },
                )
                assert result == {"ok": True}
                assert await server.dispatch(
                    "clarify.respond",
                    {"request_id": question["request_id"], "answer": "again"},
                ) == {"ok": False}
            elif action == "cancel":
                assert await server.dispatch(
                    "turn.cancel", {"turn_id": accepted["turn_id"]}
                ) == {"cancelled": True}
            elif action == "close":
                await server.close()
            if action not in {"cancel", "close"}:
                outcome = await server.host.wait(accepted["turn_id"])
                assert outcome.final_answer == "done"
                events = json.dumps(agent.session["history"])
                assert (
                    ("blue" in events)
                    if action == "answer"
                    else ("user did not answer" in events)
                )
                assert (
                    ("blue" in agent.model_client.prompts[-1])
                    if action == "answer"
                    else ("user did not answer" in agent.model_client.prompts[-1])
                )
        finally:
            await server.close()
        await asyncio.sleep(0.01)
        assert not server.questions.pending
        assert agent.question_tool.handler is None

    asyncio.run(scenario())


def test_queued_turn_cancel_does_not_answer_running_question(tmp_path):
    async def scenario():
        arrived = asyncio.Event()

        async def send(frame):
            if frame.get("method") == "clarify.request":
                arrived.set()

        server = TUIRPCServer(question_agent(tmp_path), send)
        await server.start()
        try:
            await server.dispatch(
                "turn.send", {"content": "one", "submission_id": "one"}
            )
            await asyncio.wait_for(arrived.wait(), 2)
            queued = await server.dispatch(
                "turn.send", {"content": "two", "submission_id": "two"}
            )
            await server.dispatch("turn.cancel", {"turn_id": queued["turn_id"]})
            assert server.questions.pending
            assert not next(iter(server.questions.pending.values())).future.done()
        finally:
            await server.close()

    asyncio.run(scenario())


def test_batch_answers_are_tool_results_not_write_authorization(tmp_path):
    async def scenario():
        agent = question_agent(
            tmp_path, [{"question": "First?"}, {"question": "Second?"}]
        )
        agent.model_client.outputs.insert(
            1,
            '<tool>{"name":"write_file","args":{"path":"blocked.txt","content":"no"}}</tool>',
        )
        asked = []

        async def send(frame):
            if frame.get("method") == "clarify.request":
                asked.append(frame["params"]["question"])
                await server.dispatch(
                    "clarify.respond",
                    {"request_id": frame["params"]["request_id"], "answer": "yes"},
                )

        server = TUIRPCServer(agent, send)
        await server.start()
        try:
            accepted = await server.dispatch(
                "turn.send", {"content": "do it", "submission_id": "one"}
            )
            outcome = await server.host.wait(accepted["turn_id"])
            assert outcome.final_answer == "done"
            assert asked == ["First?", "Second?"]
            assert not (tmp_path / "blocked.txt").exists()
        finally:
            await server.close()

    asyncio.run(scenario())


def test_questions_are_opt_in_and_allowlisted_before_capability_issue(tmp_path):
    from test_rpc_sessions import factory

    ordinary = factory(tmp_path)()
    assert "ask_user" not in ordinary.tools
    assert "ask_user" not in ordinary.capability_scope()["tools"]
    asyncio.run(ordinary.aclose())
    agent = RepoAgent(
        model_client=FakeModelClient([]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / "sessions"),
        enable_questions=True,
        allowed_tools=["read_file"],
    )
    assert "ask_user" not in agent.tools
    assert "ask_user" not in agent.capability_scope()["tools"]
    asyncio.run(agent.aclose())


def test_question_notification_failure_unblocks_without_fabricated_answer(tmp_path):
    async def scenario():
        async def send(frame):
            raise OSError("closed transport")

        server = TUIRPCServer(question_agent(tmp_path), send)
        await server.start()
        try:
            turn = await server.dispatch(
                "turn.send", {"content": "question", "submission_id": "one"}
            )
            await asyncio.wait_for(server.host.wait(turn["turn_id"]), 3)
            assert server.closed and server.transport_failed
            assert not server.questions.pending
        finally:
            await server.close()

    asyncio.run(scenario())
