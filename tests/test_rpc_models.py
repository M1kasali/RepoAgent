import asyncio

import pytest

from repoagent import FakeModelClient
from repoagent.model_selection import ModelSelection
from repoagent.providers import get_model_profile
from repoagent.tui_rpc import RpcError, TUIRPCServer
from test_rpc_questions import question_agent
from test_rpc_sessions import factory


def selection():
    def make(profile):
        client = FakeModelClient(["<final>new model</final>"])
        client.profile = profile
        return client

    return ModelSelection(
        {
            "openai": get_model_profile("openai").with_overrides(
                model="test-a", context_window_tokens=8000, max_output_tokens=1000
            ),
            "anthropic": get_model_profile("anthropic").with_overrides(
                model="test-b", context_window_tokens=5000, max_output_tokens=500
            ),
        },
        make,
    )


def test_model_selection_updates_budget_and_survives_session_switch(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-selection")

    async def scenario():
        make = factory(tmp_path)
        frames = []

        async def send(frame):
            frames.append(frame)

        server = TUIRPCServer(
            make(), send, session_factory=make, model_selection=selection()
        )
        await server.start()
        try:
            options = await server.dispatch("model.options", {})
            assert "sk-secret-selection" not in str(options)
            assert options["profiles"][0]["credential_present"]
            assert not options["profiles"][0]["remote_verified"]
            old_id = server.session_id
            result = await server.dispatch("model.select", {"profile": "anthropic"})
            assert result["changed"] and result["model"] == "test-b"
            assert server.session_id == old_id
            assert server.agent.max_new_tokens == 500
            assert server.agent.context_window_budget.context_window_tokens == 5000
            assert server.agent.context_manager.total_token_budget <= 4500
            assert "test-b" in server.agent.context_manager.token_counter.identity
            assert not (
                await server.dispatch("model.select", {"profile": "anthropic"})
            )["changed"]
            accepted = await server.dispatch(
                "turn.send", {"content": "test", "submission_id": "one"}
            )
            outcome = await server.host.wait(accepted["turn_id"])
            assert outcome.final_answer == "new model"
            await server.dispatch("session.create", {})
            assert server.agent.model_client.profile.model == "test-b"
            await server.dispatch("session.resume", {"session_id": old_id})
            assert server.agent.model_client.profile.model == "test-b"
        finally:
            await server.close()

    asyncio.run(scenario())


def test_failed_factory_is_atomic_and_busy_selection_is_rejected(tmp_path):
    async def scenario():
        arrived = asyncio.Event()

        async def send(frame):
            if frame.get("method") == "clarify.request":
                arrived.set()

        choices = selection()
        agent = question_agent(tmp_path)
        server = TUIRPCServer(agent, send, model_selection=choices)
        await server.start()
        try:
            client, budget = agent.model_client, agent.context_window_budget
            with pytest.raises(RpcError, match="unknown"):
                await server.dispatch("model.select", {"profile": "other"})
            choices.client_factory = lambda profile: (_ for _ in ()).throw(
                ValueError("secret error")
            )
            with pytest.raises(RpcError, match="previous model retained"):
                await server.dispatch("model.select", {"profile": "openai"})
            assert (
                agent.model_client is client and agent.context_window_budget is budget
            )
            assert choices.selected is None
            await server.dispatch(
                "turn.send", {"content": "question", "submission_id": "one"}
            )
            await asyncio.wait_for(arrived.wait(), 2)
            with pytest.raises(RpcError, match="active Turn"):
                await server.dispatch("model.select", {"profile": "openai"})
        finally:
            await server.close()

    asyncio.run(scenario())


def test_selection_preserves_explicit_counter_and_validates_new_counter(tmp_path):
    from repoagent.tokenization import Utf8TokenEstimator

    agent = factory(tmp_path)()
    counter = Utf8TokenEstimator(model="explicit-counter")
    agent.context_manager.explicit_token_counter = counter
    choices = selection()
    choices.select(agent, "anthropic")
    assert agent.context_manager.token_counter is counter
    agent.context_manager.explicit_token_counter = None
    previous_client = agent.model_client
    previous_budget = agent.context_window_budget

    def invalid(profile):
        client = FakeModelClient([])
        client.profile = profile
        client.token_counter = "invalid"
        return client

    choices.client_factory = invalid
    with pytest.raises(TypeError):
        choices.select(agent, "openai")
    assert agent.model_client is previous_client
    assert agent.context_window_budget is previous_budget
    asyncio.run(agent.aclose())
