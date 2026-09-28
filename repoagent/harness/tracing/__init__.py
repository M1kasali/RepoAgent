"""Pico In-tree Tracing，提供 ``audit.span.v1`` Observability。

Instrumentation 通过在 Pico 自有 Methods 上标注 ``@trace.instrument(...)`` Decorator 完成，核心见
:mod:`repoagent.harness.tracing.trace`；历史 Standard Path 是 ``docs/TRACING_STANDARD_API.md``。没有 Monkeypatch：
Decorators 直接位于 Pico Source，Tracing Disabled 时为 No-op，因此 Tracing Failure 不能改变 Host
Behavior。

使用 ``REPOAGENT_HARNESS_TRACING=0`` 或 Pico Config ``[tracing] enabled = false`` 关闭。Spans 默认落到
``~/.repoagent-harness/traces/logs/audit-spans.log``，可用 ``REPOAGENT_HARNESS_TRACING_DIR`` Override；``pico tracing`` 或
``/tracing`` 打开 Dashboard。Span Write 成功只证明观测记录落地，不表示被观测任务完成或 Viewer 健康。
"""

from __future__ import annotations

from . import config, trace

__all__ = ["enabled", "trace"]


def enabled() -> bool:
    return config.enabled()
