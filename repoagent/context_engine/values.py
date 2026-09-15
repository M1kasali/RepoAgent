"""Context assembly algorithms. Local provenance is recorded in the alignment ledger."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

@dataclass
class AssembledContext:
    """一次 ``ContextEngine.assemble()`` Call 的 Output。

    Agent 的 LLM Call **只**使用 `messages` 中内容，Session History 的其他部分不会直接到达 Model。
    `system_prompt_addition` 携带摘要/工作状态，`include_indices` 记录保留的原 History Indices，`metadata`
    服务 Debug/Telemetry。对象表示 Context 已装配，不表示 Provider Call 已成功或任务已完成。
    """

    messages: list[dict[str, Any]]
    system_prompt_addition: str | None = None  # 注入的摘要和工作状态
    include_indices: list[int] | None = None  # 保留下来的会话消息索引
    metadata: dict[str, Any] = field(default_factory=dict)  # 调试和遥测


@dataclass
class TokenBudget:
    """一个 Turn 的 Token Budget Breakdown。

    总 Context Window 先为 Output、Tools 与 System Prompt 保留额度，剩余 `available_history` 才能给
    Session History 与 Archive Injection。Properties 提供 Reserved Total 和默认 75% Compaction
    Threshold；这些值是 Context Planning 预算，不是 Provider 最终 Usage Receipt。
    """

    context_length: int  # 模型上下文窗口
    reserved_output: int  # 为补全预留
    reserved_tools: int  # 提示词中的工具模式和结果
    reserved_system: int  # 系统提示词开销
    available_history: int  # 为会话历史和归档注入留下的余额

    @property
    def total_reserved(self) -> int:
        return self.reserved_output + self.reserved_tools + self.reserved_system

    @property
    def threshold(self) -> int:
        """返回 Compaction Trigger，默认是 ``available_history`` 的 75%。

        超过阈值提示 Engine 应开始压缩，而不是等到 Window 完全用尽。结果向下取整；负数或不合理预算
        不在此验证，应由创建 `TokenBudget` 的上游保证。
        """
        return int(self.available_history * 0.75)
