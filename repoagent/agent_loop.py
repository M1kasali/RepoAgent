"""Agent control loop extracted from the runtime facade."""

import time

from .call_efficiency import CallEfficiencyEntry, CallEfficiencySummary
from .checkpoint import (
    CHECKPOINT_NONE_STATUS,
    CHECKPOINT_PARTIAL_STALE_STATUS,
    CHECKPOINT_WORKSPACE_MISMATCH_STATUS,
)
from .providers.base import (
    CancellationToken,
    ModelRequest,
    ModelUsage,
    ModelUsageAggregate,
    ProviderCancelledError,
    ProviderError,
    stream_model,
)
from .providers.tool_schema import model_tools_from_registry
from .task_state import TaskState
from .tool_gateway import compatibility_metadata
from .tracing import current_trace_context
from .workspace import clip, now


def _pricing_for_client(client, provider, model):
    candidates = tuple(getattr(client, "providers", ())) or (client,)
    profiles = tuple(
        profile
        for candidate in candidates
        if (profile := getattr(candidate, "profile", None)) is not None
    )
    for profile in profiles:
        if profile.provider == provider:
            return profile.pricing
    for profile in profiles:
        if profile.model == model:
            return profile.pricing
    profile = getattr(client, "profile", None)
    return profile.pricing if profile is not None else None


def _validated_fallback_attempts(value, *, statuses):
    if not isinstance(value, (list, tuple)):
        return ()
    attempts = []
    seen_indexes = set()
    for raw in value:
        if not isinstance(raw, dict) or raw.get("status") not in statuses:
            continue
        try:
            index = int(raw.get("index", 0))
            duration_ms = int(raw.get("duration_ms", 0))
        except (TypeError, ValueError):
            continue
        if index < 0 or duration_ms < 0 or index in seen_indexes:
            continue
        seen_indexes.add(index)
        attempts.append({**raw, "index": index, "duration_ms": duration_ms})
    return tuple(attempts)


class _FinalTextStream:
    def __init__(self, sink):
        self._sink = sink
        self._buffer = ""
        self._state = "searching"

    def feed(self, text):
        if not text or self._state == "done":
            return
        self._buffer += text
        if self._state == "searching":
            marker = "<final>"
            index = self._buffer.find(marker)
            if index < 0:
                self._buffer = self._suffix_prefix(self._buffer, marker)
                return
            self._buffer = self._buffer[index + len(marker) :]
            self._state = "streaming"
        if self._state == "streaming":
            marker = "</final>"
            index = self._buffer.find(marker)
            if index >= 0:
                self._emit(self._buffer[:index])
                self._buffer = ""
                self._state = "done"
                return
            suffix = self._suffix_prefix(self._buffer, marker)
            self._emit(self._buffer[: len(self._buffer) - len(suffix)])
            self._buffer = suffix

    def _emit(self, text):
        if text:
            self._sink(text)

    @staticmethod
    def _suffix_prefix(text, marker):
        limit = min(len(text), len(marker) - 1)
        for size in range(limit, 0, -1):
            if text.endswith(marker[:size]):
                return text[-size:]
        return ""


class AgentLoop:
    def __init__(self, agent):
        self.agent = agent

    def run(
        self,
        user_message,
        turn_request=None,
        model_text_sink=None,
        cancellation_token: CancellationToken | None = None,
        deadline: float | None = None,
    ):
        agent = self.agent
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled(
                provider=type(agent.model_client).__name__
            )
        run_started_at = time.monotonic()
        usage_rows = []
        call_entries = []
        agent.last_completion_metadata = {}
        agent.last_model_result = None
        agent.last_prompt_metadata = {}
        agent.last_call_efficiency_summary = CallEfficiencySummary.from_entries(
            (), turn_succeeded=False
        ).to_dict()

        def record_model_call(
            model_request,
            *,
            provider_attempt,
            provider,
            model,
            status,
            duration_ms,
            usage=None,
            finish_reason="",
            error_category="",
        ):
            parent_trace = current_trace_context()
            provider_trace = (
                parent_trace.child("provider") if parent_trace is not None else None
            )
            entry = CallEfficiencyEntry(
                provider_call_id=(
                    f"{model_request.request_id}:{model_request.attempt}:"
                    f"{provider_attempt}"
                ),
                turn_id=model_request.turn_id,
                request_id=model_request.request_id,
                session_id=model_request.session_id,
                agent_attempt=model_request.attempt,
                provider_attempt=provider_attempt,
                provider=str(provider),
                model=str(model),
                status=status,
                duration_ms=max(0, int(duration_ms)),
                usage=usage or ModelUsage(),
                pricing=_pricing_for_client(
                    agent.model_client, str(provider), str(model)
                ),
                finish_reason=finish_reason,
                error_category=error_category,
                call_kind=model_request.call_kind,
                trace_id=provider_trace.trace_id if provider_trace else "",
                span_id=provider_trace.span_id if provider_trace else "",
                parent_span_id=(
                    provider_trace.parent_span_id if provider_trace else ""
                ),
            )
            call_entries.append(entry)
            payload = entry.to_dict()
            payload["semantic_event"] = "provider.call.completed"
            agent.run_store.append_model_call(task_state, payload)
            agent.emit_trace(task_state, "model_call_accounted", payload)
            agent.last_call_efficiency_summary = CallEfficiencySummary.from_entries(
                call_entries, turn_succeeded=False
            ).to_dict()
            return entry

        def record_missing_model_calls(count, prompt_metadata):
            usage_rows.extend(ModelUsage() for _ in range(max(1, int(count))))
            aggregate = ModelUsageAggregate.from_usages(usage_rows)
            aggregate_metadata = aggregate.to_metadata()
            agent.last_completion_metadata = {
                **agent.last_completion_metadata,
                **aggregate_metadata,
            }
            agent.last_prompt_metadata = {
                **prompt_metadata,
                **agent.last_completion_metadata,
            }
            return aggregate_metadata

        agent.memory.set_task_summary(user_message)
        agent.record({"role": "user", "content": user_message, "created_at": now()})

        task_state = TaskState.create(
            run_id=str(turn_request.turn_id) if turn_request else agent.new_run_id(),
            task_id=str(turn_request.request_id)
            if turn_request
            else agent.new_task_id(),
            user_request=user_message,
        )
        task_state.resume_status = agent.resume_state.get(
            "status", CHECKPOINT_NONE_STATUS
        )
        agent.current_task_state = task_state
        agent.current_run_dir = agent.run_store.start_run(task_state)
        agent.emit_trace(
            task_state,
            "run_started",
            {
                "task_id": task_state.task_id,
                "user_request": clip(user_message, 300),
            },
        )
        agent.emit_trace(
            task_state,
            "memory_recall_completed",
            {
                "semantic_event": "memory.recall.completed",
                "hit_count": int(
                    agent.last_memory_backend_metadata.get("recalled_count", 0)
                ),
                "status": agent.last_memory_backend_metadata.get(
                    "recall_status", "not_run"
                ),
            },
        )

        tool_steps = 0
        attempts = 0
        max_attempts = max(agent.max_steps * 3, agent.max_steps + 4)

        # 这是 agent 的主循环，可以按“感知 -> 决策 -> 行动 -> 记录”来理解：
        # 1. 感知：重新组 prompt，把当前状态整理给模型看
        # 2. 决策：让模型返回一个工具调用，或一个最终答案
        # 3. 行动：如果是工具调用，就执行工具
        # 4. 记录：把结果写回 history / task_state / trace / memory
        # 然后进入下一轮，直到停机条件满足
        while tool_steps < agent.max_steps and attempts < max_attempts:
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled(
                    provider=type(agent.model_client).__name__
                )
            attempts += 1
            task_state.record_attempt()
            agent.run_store.write_task_state(task_state)
            prompt_started_at = time.monotonic()
            prompt, prompt_metadata = agent._build_prompt_and_metadata(user_message)
            agent.emit_trace(
                task_state,
                "prompt_built",
                {
                    "prompt_metadata": prompt_metadata,
                    "duration_ms": int((time.monotonic() - prompt_started_at) * 1000),
                },
            )
            if prompt_metadata.get("resume_status") == CHECKPOINT_PARTIAL_STALE_STATUS:
                checkpoint = agent.create_checkpoint(
                    task_state, user_message, trigger="freshness_mismatch"
                )
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "freshness_mismatch",
                    },
                )
            elif (
                prompt_metadata.get("resume_status")
                == CHECKPOINT_WORKSPACE_MISMATCH_STATUS
            ):
                agent.emit_trace(
                    task_state,
                    "runtime_identity_mismatch",
                    {
                        "fields": list(
                            prompt_metadata.get("runtime_identity_mismatch_fields", [])
                        ),
                    },
                )
                checkpoint = agent.create_checkpoint(
                    task_state, user_message, trigger="workspace_mismatch"
                )
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "workspace_mismatch",
                    },
                )
            if prompt_metadata.get("budget_reductions"):
                checkpoint = agent.create_checkpoint(
                    task_state, user_message, trigger="context_reduction"
                )
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "context_reduction",
                    },
                )
            agent.emit_trace(
                task_state,
                "model_requested",
                {
                    "attempts": task_state.attempts,
                    "tool_steps": task_state.tool_steps,
                    "prompt_cache_key": prompt_metadata.get("prompt_cache_key"),
                    "model_profile": (
                        agent.model_client.profile.to_dict()
                        if getattr(agent.model_client, "profile", None) is not None
                        else None
                    ),
                },
            )
            prompt_cache_key = None
            prompt_cache_retention = None
            if getattr(agent.model_client, "supports_prompt_cache", False):
                # 只有后端明确支持时，才把稳定前缀的 hash 作为 cache key 发出去。
                prompt_cache_key = prompt_metadata.get("prompt_cache_key")
                prompt_cache_retention = "in_memory"
            model_started_at = time.monotonic()
            timeout_seconds = (
                max(0.001, deadline - model_started_at)
                if deadline is not None
                else None
            )
            model_request = ModelRequest(
                prompt=prompt,
                max_output_tokens=agent.max_new_tokens,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_retention=prompt_cache_retention,
                turn_id=str(turn_request.turn_id)
                if turn_request
                else task_state.run_id,
                session_id=str(turn_request.session_id)
                if turn_request
                else agent.session["id"],
                request_id=(
                    str(turn_request.request_id) if turn_request else task_state.task_id
                ),
                attempt=task_state.attempts,
                tools=model_tools_from_registry(agent.tools),
                cancellation_token=cancellation_token,
                timeout_seconds=timeout_seconds,
            )
            stream_stats = {"events": 0, "text_deltas": 0, "tool_calls": 0}
            final_stream = (
                _FinalTextStream(model_text_sink)
                if model_text_sink is not None
                else None
            )

            def observe_model_event(event):
                stream_stats["events"] += 1
                if event.kind == "text_delta":
                    stream_stats["text_deltas"] += 1
                    if final_stream is not None:
                        final_stream.feed(event.text)
                elif event.kind == "tool_call":
                    stream_stats["tool_calls"] += 1

            try:
                model_result = stream_model(
                    agent.model_client,
                    model_request,
                    on_event=observe_model_event,
                )
            except ProviderCancelledError as exc:
                record_model_call(
                    model_request,
                    provider_attempt=0,
                    provider=exc.provider or type(agent.model_client).__name__,
                    model=str(getattr(agent.model_client, "model", "")),
                    status="cancelled",
                    duration_ms=int((time.monotonic() - model_started_at) * 1000),
                    error_category=exc.category,
                )
                agent.emit_trace(
                    task_state,
                    "model_cancelled",
                    {
                        **exc.to_dict(),
                        "model": str(getattr(agent.model_client, "model", "")),
                        "attempts": task_state.attempts,
                        "duration_ms": int(
                            (time.monotonic() - model_started_at) * 1000
                        ),
                    },
                )
                raise
            except ProviderError as exc:
                error_evidence = exc.to_dict()
                fallback_attempts = _validated_fallback_attempts(
                    error_evidence.get("fallback_attempts"),
                    statuses={"failed"},
                )
                if fallback_attempts:
                    for attempt in fallback_attempts:
                        record_model_call(
                            model_request,
                            provider_attempt=int(attempt.get("index", 0)),
                            provider=attempt.get("provider", exc.provider),
                            model=attempt.get("model", ""),
                            status="failed",
                            duration_ms=int(attempt.get("duration_ms", 0)),
                            error_category=attempt.get("category", exc.category),
                        )
                else:
                    record_model_call(
                        model_request,
                        provider_attempt=0,
                        provider=(exc.provider or type(agent.model_client).__name__),
                        model=str(getattr(agent.model_client, "model", "")),
                        status="failed",
                        duration_ms=int((time.monotonic() - model_started_at) * 1000),
                        error_category=exc.category,
                    )
                missing_calls = len(fallback_attempts)
                usage_aggregate = record_missing_model_calls(
                    missing_calls or 1, prompt_metadata
                )
                agent.emit_trace(
                    task_state,
                    "model_failed",
                    {
                        **error_evidence,
                        "model": str(getattr(agent.model_client, "model", "")),
                        "attempts": task_state.attempts,
                        "usage_aggregate": usage_aggregate,
                        "duration_ms": int(
                            (time.monotonic() - model_started_at) * 1000
                        ),
                    },
                )
                raise
            except Exception as exc:
                record_model_call(
                    model_request,
                    provider_attempt=0,
                    provider=type(agent.model_client).__name__,
                    model=str(getattr(agent.model_client, "model", "")),
                    status="failed",
                    duration_ms=int((time.monotonic() - model_started_at) * 1000),
                    error_category="unexpected",
                )
                usage_aggregate = record_missing_model_calls(1, prompt_metadata)
                agent.emit_trace(
                    task_state,
                    "model_failed",
                    {
                        "category": "unexpected",
                        "provider": type(agent.model_client).__name__,
                        "model": str(getattr(agent.model_client, "model", "")),
                        "retryable": False,
                        "should_fallback": False,
                        "status_code": None,
                        "error_type": type(exc).__name__,
                        "attempts": task_state.attempts,
                        "usage_aggregate": usage_aggregate,
                        "duration_ms": int(
                            (time.monotonic() - model_started_at) * 1000
                        ),
                    },
                )
                raise
            raw = model_result.text
            call_completion_metadata = model_result.completion_metadata()
            fallback = call_completion_metadata.get("fallback")
            if isinstance(fallback, dict):
                fallback_attempts = _validated_fallback_attempts(
                    fallback.get("attempts"),
                    statuses={"completed", "failed"},
                )
                if (
                    sum(
                        attempt["status"] == "completed"
                        for attempt in fallback_attempts
                    )
                    != 1
                ):
                    fallback_attempts = ()
                failed_attempts = sum(
                    1 for attempt in fallback_attempts if attempt["status"] == "failed"
                )
                usage_rows.extend(ModelUsage() for _ in range(failed_attempts))
                for attempt in fallback_attempts:
                    status = str(attempt.get("status", ""))
                    record_model_call(
                        model_request,
                        provider_attempt=int(attempt.get("index", 0)),
                        provider=attempt.get("provider", model_result.provider),
                        model=attempt.get("model", model_result.model),
                        status=status,
                        duration_ms=int(attempt.get("duration_ms", 0)),
                        usage=(
                            model_result.usage
                            if status == "completed"
                            else ModelUsage()
                        ),
                        finish_reason=(
                            model_result.finish_reason if status == "completed" else ""
                        ),
                        error_category=attempt.get("category", ""),
                    )
            if not isinstance(fallback, dict) or not fallback_attempts:
                record_model_call(
                    model_request,
                    provider_attempt=0,
                    provider=(
                        model_result.provider or type(agent.model_client).__name__
                    ),
                    model=(
                        model_result.model
                        or str(getattr(agent.model_client, "model", ""))
                    ),
                    status="completed",
                    duration_ms=(
                        model_result.latency_ms
                        or int((time.monotonic() - model_started_at) * 1000)
                    ),
                    usage=model_result.usage,
                    finish_reason=model_result.finish_reason,
                )
            usage_rows.append(model_result.usage)
            usage_aggregate = ModelUsageAggregate.from_usages(usage_rows)
            completion_metadata = {
                **call_completion_metadata,
                **usage_aggregate.to_metadata(),
                "last_call_usage": model_result.usage.to_metadata(),
            }
            if completion_metadata:
                # 把后端返回的 usage/cache 统计并回 prompt_metadata，
                # 方便统一写入 report 和 trace。
                prompt_metadata.update(completion_metadata)
            agent.last_completion_metadata = completion_metadata
            agent.last_model_result = model_result
            agent.last_prompt_metadata = prompt_metadata
            routing_decision = model_result.metadata.get("routing_decision")
            if isinstance(routing_decision, dict):
                agent.emit_trace(
                    task_state,
                    "model_routed",
                    {"routing_decision": routing_decision},
                )
            if isinstance(fallback, dict) and fallback.get("used"):
                agent.emit_trace(task_state, "model_fallback", fallback)
            if model_result.tool_calls:
                kind, payload = (
                    "tools",
                    [
                        {
                            "id": tool_call.id,
                            "name": tool_call.name,
                            "args": dict(tool_call.arguments),
                        }
                        for tool_call in model_result.tool_calls
                    ],
                )
            else:
                kind, payload = agent.parse(raw)
            agent.emit_trace(
                task_state,
                "model_parsed",
                {
                    "kind": kind,
                    "completion_metadata": call_completion_metadata,
                    "usage_aggregate": usage_aggregate.to_metadata(),
                    "stream": stream_stats,
                    "duration_ms": int((time.monotonic() - model_started_at) * 1000),
                },
            )

            if kind in {"tool", "tools"}:
                calls = payload if kind == "tools" else [payload]
                remaining_steps = max(0, agent.max_steps - tool_steps)
                prepared = []
                for call in calls[:remaining_steps]:
                    name = str(call.get("name") or "invalid_tool")
                    raw_args = call.get("args", {})
                    args = raw_args if isinstance(raw_args, dict) else {}
                    request = agent.build_tool_request(
                        name,
                        args,
                        call_id=str(call.get("id") or ""),
                        origin="model",
                    )
                    prepared.append((request, name, args))
                details = {
                    id(request): (name, args) for request, name, args in prepared
                }
                batches = agent.tool_gateway.plan_batches(
                    request for request, _, _ in prepared
                )
                for batch in batches:
                    if cancellation_token is not None:
                        cancellation_token.raise_if_cancelled(
                            provider=type(agent.model_client).__name__
                        )
                    batch_started_at = time.monotonic()
                    for request in batch:
                        name, _ = details[id(request)]
                        task_state.record_tool(name)
                    tool_steps += len(batch)
                    agent.run_store.write_task_state(task_state)
                    agent.emit_trace(
                        task_state,
                        "tool_batch_started",
                        {
                            "tool_call_ids": [request.call_id for request in batch],
                            "mode": batch.mode.value,
                            "scheduling_reason": batch.reason,
                            "effects": [effect.value for effect in batch.effects],
                            "mutation_conflict_policy": (
                                batch.mutation_conflict_policy.value
                            ),
                            "max_parallel": agent.tool_gateway.max_parallel,
                        },
                    )
                    tool_results = agent.execute_tool_batch(
                        batch,
                        cancellation_token=cancellation_token,
                    )
                    cancelled = []
                    for tool_request, tool_result in zip(
                        batch, tool_results, strict=True
                    ):
                        name, args = details[id(tool_request)]
                        tool_call_id = tool_request.call_id
                        result = tool_result.content
                        agent.record(
                            {
                                "role": "tool",
                                "name": name,
                                "args": args,
                                "content": result,
                                "created_at": now(),
                                "tool_call_id": tool_call_id,
                            }
                        )
                        agent.run_store.write_task_state(task_state)
                        agent.emit_trace(
                            task_state,
                            "tool_executed",
                            {
                                "semantic_event": "tool.call.completed",
                                "tool_call_id": tool_call_id,
                                "status": tool_result.status,
                                "name": name,
                                "args": args,
                                "result": clip(result, 500),
                                "duration_ms": tool_result.duration_ms,
                                "tool_request": tool_request.to_dict(),
                                "tool_result": tool_result.to_dict(),
                                **compatibility_metadata(tool_result),
                            },
                        )
                        if tool_result.status == "cancelled":
                            cancelled.append(tool_result)
                            agent.emit_trace(
                                task_state,
                                "tool_cancelled",
                                {
                                    "tool_call_id": tool_call_id,
                                    "name": name,
                                    "status": tool_result.status,
                                    "error_code": tool_result.error_code,
                                },
                            )
                            continue
                        checkpoint = agent.create_checkpoint(
                            task_state, user_message, trigger="tool_executed"
                        )
                        agent.run_store.write_task_state(task_state)
                        agent.emit_trace(
                            task_state,
                            "checkpoint_created",
                            {
                                "checkpoint_id": checkpoint["checkpoint_id"],
                                "trigger": "tool_executed",
                            },
                        )
                    agent.emit_trace(
                        task_state,
                        "tool_batch_completed",
                        {
                            "tool_call_ids": [
                                request.call_id for request in batch
                            ],
                            "result_call_ids": [
                                result.call_id for result in tool_results
                            ],
                            "mode": batch.mode.value,
                            "scheduling_reason": batch.reason,
                            "effects": [effect.value for effect in batch.effects],
                            "mutation_conflict_policy": (
                                batch.mutation_conflict_policy.value
                            ),
                            "duration_ms": int(
                                (time.monotonic() - batch_started_at) * 1000
                            ),
                        },
                    )
                    if cancelled and cancellation_token is not None:
                        task_state.stop_tool_cancelled(
                            "Tool execution cancelled."
                        )
                        agent.run_store.write_task_state(task_state)
                        cancellation_token.raise_if_cancelled(
                            provider=type(agent.model_client).__name__
                        )
                continue

            if kind == "retry":
                agent.record(
                    {"role": "assistant", "content": payload, "created_at": now()}
                )
                agent.run_store.write_task_state(task_state)
                continue

            final = (payload or raw).strip()
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled(
                    provider=type(agent.model_client).__name__
                )
            agent.record({"role": "assistant", "content": final, "created_at": now()})
            task_state.finish_success(final)
            agent.last_call_efficiency_summary = CallEfficiencySummary.from_entries(
                call_entries, turn_succeeded=True
            ).to_dict()
            agent.promote_durable_memory(user_message, final)
            checkpoint = agent.create_checkpoint(
                task_state, user_message, trigger="run_finished"
            )
            agent.run_store.write_task_state(task_state)
            agent.emit_trace(
                task_state,
                "checkpoint_created",
                {
                    "checkpoint_id": checkpoint["checkpoint_id"],
                    "trigger": "run_finished",
                },
            )
            agent.emit_trace(
                task_state,
                "run_finished",
                {
                    "status": task_state.status,
                    "stop_reason": task_state.stop_reason,
                    "final_answer": final,
                    "call_efficiency": agent.last_call_efficiency_summary,
                    "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
                },
            )
            agent.run_store.write_report(
                task_state, agent.redact_artifact(agent.build_report(task_state))
            )
            return final

        if attempts >= max_attempts and tool_steps < agent.max_steps:
            final = "Stopped after too many malformed model responses without a valid tool call or final answer."
            task_state.stop_retry_limit(final)
        else:
            final = "Stopped after reaching the step limit without a final answer."
            task_state.stop_step_limit(final)
        agent.record({"role": "assistant", "content": final, "created_at": now()})
        agent.last_call_efficiency_summary = CallEfficiencySummary.from_entries(
            call_entries, turn_succeeded=False
        ).to_dict()
        agent.promote_durable_memory(user_message, final)
        agent.run_store.write_task_state(task_state)
        checkpoint = agent.create_checkpoint(
            task_state, user_message, trigger=task_state.stop_reason or "run_stopped"
        )
        agent.emit_trace(
            task_state,
            "checkpoint_created",
            {
                "checkpoint_id": checkpoint["checkpoint_id"],
                "trigger": task_state.stop_reason or "run_stopped",
            },
        )
        agent.emit_trace(
            task_state,
            "run_finished",
            {
                "status": task_state.status,
                "stop_reason": task_state.stop_reason,
                "final_answer": final,
                "call_efficiency": agent.last_call_efficiency_summary,
                "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
            },
        )
        agent.run_store.write_report(
            task_state, agent.redact_artifact(agent.build_report(task_state))
        )
        return final
