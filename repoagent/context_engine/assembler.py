"""Context assembly algorithms. Local provenance is recorded in the alignment ledger."""
from __future__ import annotations
import asyncio
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable
from .base import AssembledPrefix, AssemblyContext, ContextEngine, SegmentBuilder
from . import render
from .values import AssembledContext, TokenBudget
from .curator import TurnContext

class ContextAssembler(ContextEngine):
    """把 SegmentBuilders 的两阶段产物合并为单 Turn Context 的唯一 Engine。

    构造时按 ``order`` 排序，并依据 ``needs_prefix`` 固定 Phase A/Phase B；`assemble` 每轮并行
    运行独立贡献者、建立 `AssembledPrefix`、再运行依赖固定开销的贡献者。实例长期由
    AgentLoop 持有，可通过 `replace_model` 把模型变化转发给需要它的 Builder。

    Engine ``owns_compaction=True``，因为 Curator 自行选择和归档 History；Host 必须传完整
    append-only 候选并跳过 MemoryConsolidator。最终 metadata 会带 ``engine`` 名称，便于 Turn
    evidence 确认实际组装路径。
    """

    def __init__(
        self,
        builders: list[SegmentBuilder],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._builders = sorted(builders, key=lambda b: b.order)
        self._phase_a = [b for b in self._builders if not b.needs_prefix]
        self._phase_b = [b for b in self._builders if b.needs_prefix]
        self.get_tool_definitions = get_tool_definitions
        self._now_fn = now_fn or datetime.now

    @property
    def name(self) -> str:
        return "context_assembler"

    @property
    def owns_compaction(self) -> bool:
        # Curator 路径自行归档历史，因此 AgentLoop 向其传入完整的追加式日志，
        # 并跳过 Host 的 MemoryConsolidator。
        return True

    def replace_model(self, model: str) -> None:
        for builder in self._builders:
            replace_model = getattr(builder, "replace_model", None)
            if callable(replace_model):
                replace_model(model)

    async def assemble(
        self,
        session_key: str,
        session_messages: list[dict[str, Any]],
        budget: TokenBudget,
        *,
        turn: "TurnContext",
    ) -> AssembledContext:
        ctx = AssemblyContext(
            session_key=session_key,
            current_message=turn.current_message,
            media=turn.media,
            channel=turn.channel,
            chat_id=turn.chat_id,
            session_messages=session_messages,
            budget=budget,
        )

        # ── 阶段 A——相互独立的片段构建器，并发执行 ──────
        a_segs = await asyncio.gather(*[b.build(ctx) for b in self._phase_a])
        meta: dict[str, Any] = {}
        prefix_parts: list[str] = []
        for seg in a_segs:
            if seg is None:
                continue
            meta |= seg.meta
            if seg.text:
                prefix_parts.append(seg.text)
        system_prefix = "\n\n---\n\n".join(prefix_parts)

        user_msg = self._build_user(ctx)

        # ── 阶段 B——依赖前缀的构建器（Curator），串行执行 ───
        ctx_b = replace(
            ctx,
            prefix=AssembledPrefix(
                system_prefix=system_prefix,
                user_message=user_msg,
                tool_defs=self.get_tool_definitions(),
            ),
        )
        b_segs = await asyncio.gather(*[b.build(ctx_b) for b in self._phase_b])

        system = system_prefix
        history: list[dict[str, Any]] = []
        seg6_parts: list[str] = []
        for seg in b_segs:
            if seg is None:
                continue
            meta |= seg.meta
            if seg.text:
                seg6_parts.append(seg.text)
            if seg.history is not None:
                history = seg.history
        for text in seg6_parts:
            system = system + "\n\n---\n\n" + text

        messages = [{"role": "system", "content": system}, *history, user_msg]
        return AssembledContext(
            messages=messages,
            metadata=meta | {"engine": self.name},
        )

    async def after_turn(
        self,
        session_key: str,
        outcome: dict[str, Any],
        usage: dict[str, int] | None = None,
    ) -> None:
        # 委托给需要维护每 Turn 账目的 builder（例如 Curator）。
        for builder in self._builders:
            hook = getattr(builder, "after_turn", None)
            if hook is not None:
                await hook(session_key, outcome, usage)

    def _build_user(self, ctx: AssemblyContext) -> dict[str, Any]:
        """构造唯一结构化 User message，把运行时上下文放在真实内容之前。

        `render.build_runtime_context` 根据当前时间、Channel 与 Chat 生成每轮环境前缀，
        `render.build_user_content` 则把 ``current_message`` 和 Media 转成 Provider 可接受内容。
        纯文本用两个换行连接；多模态列表在首位插入 runtime text block，保持图片等后续块顺序。

        返回固定 ``{"role": "user", "content": merged}`` 形状。该运行时前缀只供本轮模型使用，
        Session 持久化会把它剥离，避免每轮动态时间污染长期用户历史。
        """
        runtime_ctx = render.build_runtime_context(self._now_fn, ctx.channel, ctx.chat_id)
        user_content = render.build_user_content(ctx.current_message, ctx.media)
        if isinstance(user_content, str):
            merged: Any = f"{runtime_ctx}\n\n{user_content}"
        else:
            merged = [{"type": "text", "text": runtime_ctx}] + user_content
        return {"role": "user", "content": merged}


__all__ = ["ContextAssembler"]
