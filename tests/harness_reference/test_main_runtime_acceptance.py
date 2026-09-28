"""Offline production assembly: parent tool call, background child, new Turn."""

import asyncio

from repoagent.harness.agent.spine_runner import AgentTurnRunner
from repoagent.harness.call_efficiency import CallEfficiencyProvider
from repoagent.harness.cli._runtime_assembly import assemble_runtime
from repoagent.harness.config.pico import PicoConfig
from repoagent.harness.config.schema import Config
from repoagent.harness.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from repoagent.harness.spine import ChatType, Origin, OriginPools, Scheduler, Source, TurnRequest


class ScriptedProvider(LLMProvider):
    def __init__(self):
        super().__init__()
        self.requests = []

    def get_default_model(self):
        return "offline-model"

    async def chat(self, messages, **kwargs):
        self.requests.append(messages)
        last = str(messages[-1].get("content", ""))
        usage = {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}
        if "Start background lookup" in last:
            return LLMResponse(
                content=None, finish_reason="tool_calls", usage=usage,
                tool_calls=[ToolCallRequest("spawn-1", "spawn", {"task": "offline-child"})],
            )
        return LLMResponse(content="Offline lookup completed", usage=usage)


async def test_production_assembly_spawn_reenters_same_session(tmp_path):
    config = Config()
    config.agents.defaults.workspace = str(tmp_path / "workspace")
    config.agents.defaults.model = "offline-model"
    config.workspace_path.mkdir(parents=True)
    features = PicoConfig()
    features.memory.backend = None
    provider = ScriptedProvider()
    runtime = assemble_runtime(
        config, features, provider=provider, cron_service=None, interactive=False,
    )
    assert isinstance(runtime.agent_loop.provider, CallEfficiencyProvider)
    received = []
    reinjected = []
    completed = asyncio.Event()
    real_runner = AgentTurnRunner(runtime.agent_loop, stream=False)

    class RecordingRunner:
        async def run(self, req, emit, drain):
            result = await real_runner.run(req, emit, drain)
            if req.origin == Origin.SUBAGENT:
                reinjected.append((req, result))
                completed.set()
            return result

    async def sink(event):
        received.append(event)

    scheduler = Scheduler(RecordingRunner(), OriginPools(user=1, system=1), sink)
    runtime.agent_loop.subagents.set_submit(scheduler.submit)
    try:
        outcome = await asyncio.wait_for(scheduler.submit(TurnRequest(
            origin=Origin.USER,
            source=Source(channel="cli", chat_id="acceptance", sender_id="user", chat_type=ChatType.DM),
            text="Start background lookup", conversation="cli:acceptance",
        )).result(), 15)
        assert outcome is not None and outcome.tool_calls == 1 and outcome.tool_failures == 0
        await asyncio.wait_for(completed.wait(), 15)
        assert len(reinjected) == 1
        req, result = reinjected[0]
        assert req.conversation == "cli:acceptance"
        assert req.source.sender_id == "subagent"
        assert "Offline lookup completed" in req.text
        assert result.tool_failures == 0
        assert len(provider.requests) == 4  # parent tool call + reply + child + reinjected reply
        saved = runtime.session_manager.get_or_create("cli:acceptance")
        assert saved.messages
        assert list(tmp_path.rglob("*.jsonl"))
    finally:
        await scheduler.shutdown(grace=1)
        await runtime.close()
