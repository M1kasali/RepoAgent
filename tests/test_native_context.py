import asyncio
from dataclasses import asdict
from datetime import datetime
from types import SimpleNamespace

import pytest

from repoagent.context_engine.assembler import ContextAssembler
from repoagent.context_engine.base import AssembledPrefix, AssemblyContext, Segment
from repoagent.context_engine.config import ContextConfig
from repoagent.context_engine.curator import TurnContext
from repoagent.context_engine.curator_segment import CuratorSegmentBuilder
from repoagent.context_engine.history_trimmer import HistoryTrimmer
from repoagent.context_engine.values import TokenBudget


def test_two_phase_assembly_uses_exact_prefix_and_one_user():
    observations = []

    class Builder:
        def __init__(self, order, needs_prefix, text):
            self.order, self.needs_prefix, self.text = order, needs_prefix, text

        async def build(self, ctx):
            observations.append((self.order, ctx.prefix))
            if self.needs_prefix:
                assert ctx.prefix.system_prefix == "identity\n\n---\n\nbootstrap"
                assert ctx.prefix.tool_defs == [{"name": "probe"}]
                return Segment(text=self.text, history=[{"role": "user", "content": "old"}, {"role": "assistant", "content": "answer"}])
            return Segment(text=self.text)

    engine = ContextAssembler([Builder(6, True, "state"), Builder(2, False, "bootstrap"),
                               Builder(1, False, "identity")], lambda: [{"name": "probe"}],
                              now_fn=lambda: datetime(2026, 8, 6))
    result = asyncio.run(engine.assemble("one", [], TokenBudget(1000, 100, 100, 100, 700), turn=TurnContext("current")))
    assert [row["role"] for row in result.messages] == ["system", "user", "assistant", "user"]
    assert result.messages[0]["content"] == "identity\n\n---\n\nbootstrap\n\n---\n\nstate"
    assert result.messages[-1]["content"].endswith("\n\ncurrent")
    assert "2026-08-06" in result.messages[-1]["content"]
    assert observations[0][1] is None and observations[1][1] is None


def test_trimmer_closes_tool_pairs_and_preserves_reasoning():
    history = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": None, "reasoning_content": "reason", "tool_calls": [
            {"id": "one", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "one", "name": "read", "content": "result", "internal": "not a provider field"},
    ]
    ids = HistoryTrimmer.canonical_ids(history, [0, 2])
    assert ids == [0, 1, 2]
    clean = HistoryTrimmer.history_from_ids(history, ids)
    assert clean[1]["reasoning_content"] == "reason"
    assert "internal" not in clean[2]
    assert HistoryTrimmer.structural_errors(clean) == []
    assert HistoryTrimmer.structural_errors(clean[:-1])


def test_trimmer_protected_overflow_is_explicit():
    provider = SimpleNamespace(estimate_prompt_tokens=lambda messages, tools, model: (len(messages) * 100, "test"))
    trimmer = HistoryTrimmer(provider, "test", lambda: [], 250)
    history = [{"role": "user", "content": "protected"}, {"role": "assistant", "content": "old"},
               {"role": "user", "content": "drop"}, {"role": "assistant", "content": "old"}]
    _, result = trimmer.trim(session_messages=history, ids=list(range(4)), protected_ids={0},
                            reserved_output=50, build_messages=lambda h: [{"role": "system", "content": "fixed"}, *h, {"role": "user", "content": "current"}])
    assert result.included_ids == [0, 1]
    assert not result.ok
    assert len(result.warnings) == 1


@pytest.mark.parametrize("mode", ["fast", "slow", "failure", "invalid", "timeout"])
def test_curator_paths_and_after_turn_are_persisted(tmp_path, mode):
    class Provider:
        calls = 0

        async def chat_with_retry(self, **kwargs):
            self.calls += 1
            if mode == "timeout":
                await asyncio.sleep(1)
            if mode == "failure":
                raise RuntimeError("provider unavailable")
            calls = [] if mode == "invalid" else [SimpleNamespace(
                id="plan", name="curator_build_context", arguments={"include_message_ids": [0, 1]},
                to_openai_tool_call=lambda: {"id": "plan", "type": "function", "function": {"name": "curator_build_context", "arguments": '{"include_message_ids":[0,1]}'}})]
            return SimpleNamespace(content="", finish_reason="stop", tool_calls=calls,
                                   has_tool_calls=bool(calls), reasoning_content=None, thinking_blocks=[])

    provider = Provider()
    config = ContextConfig(fast_path_threshold=1.0 if mode == "fast" else 0, curator_timeout_seconds=0.01)
    curator = CuratorSegmentBuilder(tmp_path, config, provider, "test", 10000, lambda: [], memory_enabled=False)
    budget = TokenBudget(10000, 100, 0, 100, 9800)
    history = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "answer"}]
    ctx = AssemblyContext(session_key="session", current_message="current", media=None, channel=None,
                          chat_id=None, session_messages=history, budget=budget,
                          prefix=AssembledPrefix("identity", {"role": "user", "content": "current"}, []))
    result = asyncio.run(curator.build(ctx))
    assert result.meta["path"] == (mode if mode in {"fast", "slow"} else "fallback")
    assert provider.calls == (0 if mode == "fast" else 1)
    assert result.history == history
    asyncio.run(curator.after_turn("session", {"final_content": "done"}))
    trace = next((tmp_path / "memory/.curator/traces").rglob("*.jsonl")).read_text()
    assert "main_agent_result" in trace
    assert asdict(config)["protect_first_n"] == 3


def test_archive_retrieve_rejects_traversal_and_symlinks(tmp_path):
    from repoagent.context_engine.curator import CuratorArchiveStore

    archive = CuratorArchiveStore(tmp_path / "state", ContextConfig())
    outside = tmp_path / "private.jsonl"
    outside.write_text('{}\n{"id":0,"message":{"role":"user","content":"private"}}\n')
    link = archive.archive_dir / "link.jsonl"
    link.symlink_to(outside)
    for ref in (str(outside), "../private.jsonl", str(link.relative_to(archive.workspace))):
        result = archive.retrieve([ref])
        assert "private\"" not in str(result)
        assert "outside archive" in str(result)


def test_curator_cancellation_joins_worker():
    import threading
    from repoagent.context_engine.runtime import CuratorProvider

    started, finished = threading.Event(), threading.Event()

    def invoke(*, context_cancellation_token):
        started.set()
        try:
            while not context_cancellation_token.cancelled:
                finished.wait(0.001)
            context_cancellation_token.raise_if_cancelled()
        finally:
            finished.set()

    async def run():
        task = asyncio.create_task(CuratorProvider(invoke).chat_with_retry())
        while not started.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(run())


def test_native_curator_call_is_accounted_and_recovery_consumed_once(tmp_path):
    from repoagent import RepoAgent, SessionStore
    from repoagent.providers.base import ModelEvent, ModelResult, ModelUsage, ToolCall
    from repoagent.workspace import WorkspaceContext

    class Provider:
        supports_native_tools = supports_structured_messages = True
        model = "test"
        requests = []

        def stream(self, request):
            self.requests.append(request)
            result = ModelResult(text="done", model=self.model, usage=ModelUsage(input_tokens=10, output_tokens=2))
            if request.call_kind == "compaction":
                result = ModelResult(tool_calls=(ToolCall("plan", "curator_build_context", {"include_message_ids": []}),),
                                     model=self.model, usage=ModelUsage(input_tokens=10, output_tokens=2))
            yield ModelEvent(kind="completed", result=result)

    provider = Provider()
    agent = RepoAgent(model_client=provider, workspace=WorkspaceContext.build(tmp_path),
                      session_store=SessionStore(tmp_path / "sessions"), max_provider_calls=3,
                      native_context_config=ContextConfig(fast_path_threshold=0, curator_model="test"))
    agent.session["pending_context_recovery"] = {"files": ["changed.py"], "checkpoint_id": "snapshot"}
    try:
        assert agent.ask("continue") == "done"
        main = [r for r in provider.requests if r.call_kind != "compaction"][-1]
        assert "Files modified last turn: changed.py" in main.messages[-1].content
        assert "pending_context_recovery" not in agent.session
        rows = agent.run_store.load_model_calls(agent.current_task_state.run_id)
        assert len(rows) == 2
        assert agent.ask("next") == "done"
        main = [r for r in provider.requests if r.call_kind != "compaction"][-1]
        assert "[Recovery" not in main.messages[-1].content
    finally:
        asyncio.run(agent.aclose())


def test_host_memory_selects_relevant_sections_and_notes(tmp_path):
    from repoagent.context_engine.host_memory import MemoryStore

    profile = tmp_path / "user_memory/profile/user.md"
    profile.parent.mkdir(parents=True)
    profile.write_text("# Profile\n## Projects\nPython runtime\n## Food\nRice\n## Notes\nCheck tests\n")
    result = MemoryStore(tmp_path).get_memory_context("Python")
    assert result.startswith("## Long-term Memory")
    assert "## Projects" in result and "## Notes" in result
    assert "## Food" not in result


def test_native_always_skills_have_distinct_rendering():
    from repoagent.context_engine.runtime import _render_always

    result = _render_always([SimpleNamespace(name="testing", content="Run tests.", meta={"skill_dir": "/skills/testing"})])
    assert result.startswith("### Skill: testing\n**Skill directory**:")
    assert "[local:" not in result
    assert result.endswith("\n\nRun tests.")
