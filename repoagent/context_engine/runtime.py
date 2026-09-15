"""Adapt the context engine's dictionary contract to RepoAgent's typed runtime."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

from .assembler import ContextAssembler
from .base import Segment
from .curator import TurnContext
from .curator_segment import CuratorSegmentBuilder
from .helpers import estimate_prompt_tokens
from .render import load_bootstrap_files, render_recalled_memory, render_router_skills, render_skill_references
from .values import TokenBudget
from .host_memory import MemoryStore
from .skill_resolver import LocalSkillResolver
from ..skill_refs import resolve_skill_refs
from ..skills import ActivatedSkill
from ..providers.base import CancellationToken, ModelMessage, ToolCall
from ..providers.tool_schema import model_tools_from_registry


def message_dict(message):
    row = {"role": message.role, "content": message.content}
    if message.tool_calls:
        row["tool_calls"] = [call_dict(call) for call in message.tool_calls]
    if message.tool_call_id:
        row.update(tool_call_id=message.tool_call_id, name=message.name)
    if message.reasoning_content:
        row["reasoning_content"] = message.reasoning_content
    if message.thinking_blocks:
        row["thinking_blocks"] = [dict(block) for block in message.thinking_blocks]
    return row


def call_dict(call):
    return {"id": call.id, "type": "function", "function": {
        "name": call.name, "arguments": json.dumps(dict(call.arguments), ensure_ascii=False),
    }}


def typed_message(row):
    calls = []
    for call in row.get("tool_calls") or ():
        function = call.get("function") or call
        arguments = function.get("arguments", call.get("args", {}))
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        calls.append(ToolCall(str(call["id"]), str(function["name"]), arguments))
    return ModelMessage(
        role=row["role"], content=row.get("content") or "", tool_calls=tuple(calls),
        tool_call_id=row.get("tool_call_id") or "", name=row.get("name") or "",
        reasoning_content=row.get("reasoning_content") or "",
        thinking_blocks=tuple(row.get("thinking_blocks") or ()),
    )


def definitions(agent):
    return [{"type": "function", "function": {"name": tool.name,
             "description": tool.description, "parameters": dict(tool.parameters)}}
            for tool in model_tools_from_registry(agent.tools)]


class _Segment:
    needs_prefix = False

    def __init__(self, order, build):
        self.order, self.render = order, build

    async def build(self, ctx):
        return self.render(ctx)


class CuratorProvider:
    def __init__(self, invoke):
        self.invoke = invoke

    async def chat_with_retry(self, **kwargs):
        # The runtime callback owns call budgets, cancellation and accounting.
        token = CancellationToken()
        worker = asyncio.create_task(asyncio.to_thread(self.invoke, context_cancellation_token=token, **kwargs))
        try:
            result = await asyncio.shield(worker)
        except asyncio.CancelledError:
            token.cancel()
            # Join the provider before fallback can start or accounting is finalized.
            try:
                await worker
            except Exception:
                pass
            raise
        calls = [SimpleNamespace(id=call.id, name=call.name, arguments=dict(call.arguments),
                                 to_openai_tool_call=lambda call=call: call_dict(call))
                 for call in result.tool_calls]
        return SimpleNamespace(content=result.text, tool_calls=calls, has_tool_calls=bool(calls),
                               finish_reason=result.finish_reason, error_classification=None,
                               reasoning_content=result.reasoning_content,
                               thinking_blocks=[dict(block) for block in result.thinking_blocks])


def assemble(agent, user_message, history, *, turn_request=None, invoke_curator):
    agent.refresh_prefix()
    agent.skill_watcher.poll()
    agent.active_skills, agent.skill_references, agent.skill_diagnostics = (), (), {}
    if agent.feature_enabled("skills"):
        selected, always, originals = _resolve_skills(agent, user_message, history)
        agent.active_skills = tuple(always) + tuple(originals[h.qualified_id] for h in selected.activated)
        agent.skill_references = tuple(originals[h.qualified_id] for h in selected.references)
        agent.skill_diagnostics = selected.diagnostics or {}
        agent.skill_diagnostics.update(
            activated_ids=[skill.qualified_id for skill in agent.active_skills],
            reference_ids=[skill.qualified_id for skill in agent.skill_references],
        )

    def memory(ctx):
        if not agent.feature_enabled("memory"):
            return Segment(meta={"memory_hits": 0})
        recalled = render_recalled_memory(agent.backend_memory_hits)
        host = MemoryStore(agent.context_state_root).get_memory_context(current_message=user_message)
        body = "\n\n".join(part for part in (host, recalled) if part)
        return Segment(text="# Memory\n\n" + body if body else "", meta={"memory_hits": len(agent.backend_memory_hits)})

    def hit(skill):
        return SimpleNamespace(name=skill.manifest.name, qualified_id=skill.qualified_id,
                               content=resolve_skill_refs(skill.content, skill.manifest.path.parent), meta={"skill_dir": str(skill.manifest.path.parent),
                                                           "description": skill.manifest.description})

    def skills(ctx, always):
        selected = [hit(skill) for skill in agent.active_skills if bool(skill.manifest.always) is always]
        body = _render_always(selected[:5]) if always else render_router_skills(selected)
        if not always:
            refs = render_skill_references([hit(skill) for skill in agent.skill_references])
            body = "\n\n".join(part for part in (body, refs) if part)
        heading = "# Active Skills" if always else "# Skills"
        return Segment(text=heading + "\n\n" + body if body else "")

    config = agent.native_context_config
    now_fn = agent.context_now_fn
    tools = definitions(agent)
    limit = agent.context_manager.total_token_budget
    output = agent.max_new_tokens
    provider = CuratorProvider(invoke_curator)
    curator = CuratorSegmentBuilder(agent.context_state_root, config, provider,
                                    str(getattr(agent.model_client, "model", "")),
                                    limit + output, lambda: tools, now_fn=now_fn,
                                    memory_enabled=agent.feature_enabled("memory"))
    builders = [
        _Segment(1, lambda ctx: Segment(text=agent.prefix)),
        _Segment(2, lambda ctx: Segment(text=load_bootstrap_files(agent.context_state_root))),
        _Segment(3, memory), _Segment(4, lambda ctx: skills(ctx, True)),
        _Segment(5, lambda ctx: skills(ctx, False)), curator,
    ]
    parts = [builder.render(None) for builder in builders[:-1]]
    builders = [_Segment(index + 1, lambda ctx, part=part: part) for index, part in enumerate(parts)] + [curator]
    engine = ContextAssembler(builders, lambda: tools, now_fn=now_fn)
    system = "\n\n---\n\n".join(part.text for part in parts if part.text)
    reserved_system = estimate_prompt_tokens([{"role": "system", "content": system}])
    reserved_tools = estimate_prompt_tokens([], tools)
    budget = TokenBudget(limit + output, output, reserved_tools, reserved_system,
                         max(0, limit - reserved_system - reserved_tools))
    source = getattr(turn_request, "source", None)
    session_key = str(agent.session["id"])
    turn = TurnContext(user_message, channel=getattr(source, "channel", agent.context_channel),
                       chat_id=getattr(source, "chat_id", agent.context_chat_id))
    history = [dict(row, content=row.get("provider_content", row.get("content", ""))) for row in history]
    assembled = asyncio.run(engine.assemble(session_key, history, budget, turn=turn))
    messages = tuple(typed_message(row) for row in assembled.messages)
    recovery = agent.session.get("pending_context_recovery")
    if recovery and messages and messages[-1].role == "user":
        lines = ["[Recovery \u2014 the previous turn was interrupted before finishing]"]
        if recovery.get("files"):
            lines.append("Files modified last turn: " + ", ".join(recovery["files"]))
        if recovery.get("checkpoint_id"):
            lines.append("Checkpoint: " + recovery["checkpoint_id"])
        lines.append("Verify the current state of these files before continuing.")
        messages = (*messages[:-1], replace(messages[-1], content="\n".join(lines) + "\n\n" + messages[-1].content))
        agent.session.pop("pending_context_recovery", None)
        agent.session_store.save(agent.session)
    agent._native_context_engine = engine
    agent._native_context_session = session_key
    tokens = estimate_prompt_tokens([message_dict(message) for message in messages], tools)
    admission = agent.context_window_budget.admit(tokens)
    history_count = max(0, len(messages) - 2)
    metadata = {
        "engine": engine.name, "assembly": assembled.metadata,
        "prompt_tokens": tokens, "prompt_chars": sum(len(m.content) for m in messages),
        "prefix_hash": agent.prefix_state.hash, "prompt_cache_key": agent.prefix_state.hash,
        "context_window_admitted": admission.admitted, "context_window": admission.to_dict(),
        "tool_count": len(tools), "sections": {"history": {"rendered_tokens": 0}},
        "history": {"source_entry_count": len(history), "included_entry_count": history_count},
        "skill_selection": agent.skill_diagnostics,
        "resume_status": agent.resume_state.get("status", "none"),
    }
    return messages, metadata


def _resolve_skills(agent, query, history):
    originals = {}

    def admitted(skill):
        if agent.skill_catalog.get(skill.qualified_id) is not None and not skill.manifest.path.is_file():
            return False
        availability = agent.skill_catalog.availability(skill.manifest)
        return availability["available"] and not (set(skill.manifest.requires.get("tools", ())) - set(agent.tools))

    always = []
    for manifest in agent.skill_catalog.list():
        if manifest.always:
            skill = ActivatedSkill(manifest, "", 0)
            if admitted(skill):
                always.append(ActivatedSkill(manifest, agent.skill_catalog.load_body(manifest), 0))

    class Router:
        async def select(self, *, query, history, k, diagnostics):
            candidates, details = agent.skill_resolver.router.select(query, history, k=k)
            diagnostics.update(details)
            hits = []
            for skill in candidates:
                if not admitted(skill) or skill.manifest.always:
                    continue
                originals[skill.qualified_id] = skill
                hits.append(SimpleNamespace(name=skill.manifest.name, qualified_id=skill.qualified_id,
                            content=skill.content, meta={"description": skill.manifest.description,
                            "skill_dir": str(skill.manifest.path.parent)}))
            return hits

    resolution = asyncio.run(LocalSkillResolver(Router(), candidate_limit=5, activation_limit=5).resolve(query, history))
    return resolution, always, originals


def _render_always(hits):
    parts = []
    for hit in hits:
        if not hit.content:
            continue
        if "{baseDir}" in hit.content:
            parts.append(f"### Skill: {hit.name}\n\n{hit.content}")
            continue
        parts.append(f"### Skill: {hit.name}\n"
                     f"**Skill directory**: `{hit.meta['skill_dir']}`\n"
                     "Relative refs (e.g. `references/x.md`, `./scripts/y.sh`) "
                     "resolve under this directory \u2014 use the absolute form for "
                     f"read_file / exec.\n\n{hit.content}")
    return "\n\n---\n\n".join(parts)


def stash_recovery(agent, task_state):
    if agent.workspace_checkpoint_service is None or task_state.stop_reason not in {
        "step_limit_reached", "tool_cancelled",
    }:
        return
    files = sorted(set(task_state.edited_files + task_state.observed_changed_files))
    if files or task_state.workspace_checkpoint_id:
        agent.session["pending_context_recovery"] = {
            "checkpoint_id": task_state.workspace_checkpoint_id, "files": files,
        }
        agent.session_store.save(agent.session)


def after_turn(agent, final, messages, usage):
    engine = getattr(agent, "_native_context_engine", None)
    if engine is not None:
        asyncio.run(engine.after_turn(agent._native_context_session,
                    {"final_content": final, "messages": [message_dict(m) for m in messages]}, usage))
