"""Agent control loop extracted from the runtime facade."""

import time

from .checkpoint import CHECKPOINT_NONE_STATUS, CHECKPOINT_PARTIAL_STALE_STATUS, CHECKPOINT_WORKSPACE_MISMATCH_STATUS
from .providers.base import ModelRequest, ProviderError, stream_model
from .providers.tool_schema import model_tools_from_registry
from .task_state import TaskState
from .workspace import clip, now


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

    def run(self, user_message, turn_request=None, model_text_sink=None):
        agent = self.agent
        run_started_at = time.monotonic()
        agent.memory.set_task_summary(user_message)
        agent.record({"role": "user", "content": user_message, "created_at": now()})

        task_state = TaskState.create(
            run_id=str(turn_request.turn_id) if turn_request else agent.new_run_id(),
            task_id=str(turn_request.request_id) if turn_request else agent.new_task_id(),
            user_request=user_message,
        )
        task_state.resume_status = agent.resume_state.get("status", CHECKPOINT_NONE_STATUS)
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
                checkpoint = agent.create_checkpoint(task_state, user_message, trigger="freshness_mismatch")
                agent.run_store.write_task_state(task_state)
                agent.emit_trace(
                    task_state,
                    "checkpoint_created",
                    {
                        "checkpoint_id": checkpoint["checkpoint_id"],
                        "trigger": "freshness_mismatch",
                    },
                )
            elif prompt_metadata.get("resume_status") == CHECKPOINT_WORKSPACE_MISMATCH_STATUS:
                agent.emit_trace(
                    task_state,
                    "runtime_identity_mismatch",
                    {
                        "fields": list(prompt_metadata.get("runtime_identity_mismatch_fields", [])),
                    },
                )
                checkpoint = agent.create_checkpoint(task_state, user_message, trigger="workspace_mismatch")
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
                checkpoint = agent.create_checkpoint(task_state, user_message, trigger="context_reduction")
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
                },
            )
            prompt_cache_key = None
            prompt_cache_retention = None
            if getattr(agent.model_client, "supports_prompt_cache", False):
                # 只有后端明确支持时，才把稳定前缀的 hash 作为 cache key 发出去。
                prompt_cache_key = prompt_metadata.get("prompt_cache_key")
                prompt_cache_retention = "in_memory"
            model_started_at = time.monotonic()
            model_request = ModelRequest(
                prompt=prompt,
                max_output_tokens=agent.max_new_tokens,
                prompt_cache_key=prompt_cache_key,
                prompt_cache_retention=prompt_cache_retention,
                turn_id=str(turn_request.turn_id) if turn_request else task_state.run_id,
                session_id=str(turn_request.session_id) if turn_request else agent.session["id"],
                request_id=(
                    str(turn_request.request_id)
                    if turn_request
                    else task_state.task_id
                ),
                attempt=task_state.attempts,
                tools=model_tools_from_registry(agent.tools),
            )
            stream_stats = {"events": 0, "text_deltas": 0, "tool_calls": 0}
            final_stream = (
                _FinalTextStream(model_text_sink) if model_text_sink is not None else None
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
            except ProviderError as exc:
                agent.emit_trace(
                    task_state,
                    "model_failed",
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
            except Exception as exc:
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
                        "duration_ms": int(
                            (time.monotonic() - model_started_at) * 1000
                        ),
                    },
                )
                raise
            raw = model_result.text
            completion_metadata = model_result.completion_metadata()
            if completion_metadata:
                # 把后端返回的 usage/cache 统计并回 prompt_metadata，
                # 方便统一写入 report 和 trace。
                prompt_metadata.update(completion_metadata)
            agent.last_completion_metadata = completion_metadata
            agent.last_model_result = model_result
            agent.last_prompt_metadata = prompt_metadata
            if model_result.tool_calls:
                kind, payload = "tools", [
                    {
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "args": dict(tool_call.arguments),
                    }
                    for tool_call in model_result.tool_calls
                ]
            else:
                kind, payload = agent.parse(raw)
            agent.emit_trace(
                task_state,
                "model_parsed",
                {
                    "kind": kind,
                    "completion_metadata": completion_metadata,
                    "stream": stream_stats,
                    "duration_ms": int((time.monotonic() - model_started_at) * 1000),
                },
            )

            if kind in {"tool", "tools"}:
                calls = payload if kind == "tools" else [payload]
                for call in calls:
                    if tool_steps >= agent.max_steps:
                        break
                    tool_steps += 1
                    tool_call_id = str(call.get("id") or "")
                    name = call.get("name", "")
                    args = call.get("args", {})
                    task_state.record_tool(name)
                    tool_started_at = time.monotonic()
                    tool_result = agent.execute_tool(name, args)
                    result = tool_result.content
                    history_item = {
                        "role": "tool",
                        "name": name,
                        "args": args,
                        "content": result,
                        "created_at": now(),
                    }
                    if tool_call_id:
                        history_item["tool_call_id"] = tool_call_id
                    agent.record(history_item)
                    agent.run_store.write_task_state(task_state)
                    agent.emit_trace(
                        task_state,
                        "tool_executed",
                        {
                            "tool_call_id": tool_call_id,
                            "name": name,
                            "args": args,
                            "result": clip(result, 500),
                            "duration_ms": int(
                                (time.monotonic() - tool_started_at) * 1000
                            ),
                            **dict(tool_result.metadata or {}),
                        },
                    )
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
                continue

            if kind == "retry":
                agent.record({"role": "assistant", "content": payload, "created_at": now()})
                agent.run_store.write_task_state(task_state)
                continue

            final = (payload or raw).strip()
            agent.record({"role": "assistant", "content": final, "created_at": now()})
            task_state.finish_success(final)
            agent.promote_durable_memory(user_message, final)
            checkpoint = agent.create_checkpoint(task_state, user_message, trigger="run_finished")
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
                    "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
                },
            )
            agent.run_store.write_report(task_state, agent.redact_artifact(agent.build_report(task_state)))
            return final

        if attempts >= max_attempts and tool_steps < agent.max_steps:
            final = "Stopped after too many malformed model responses without a valid tool call or final answer."
            task_state.stop_retry_limit(final)
        else:
            final = "Stopped after reaching the step limit without a final answer."
            task_state.stop_step_limit(final)
        agent.record({"role": "assistant", "content": final, "created_at": now()})
        agent.promote_durable_memory(user_message, final)
        agent.run_store.write_task_state(task_state)
        checkpoint = agent.create_checkpoint(task_state, user_message, trigger=task_state.stop_reason or "run_stopped")
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
                "run_duration_ms": int((time.monotonic() - run_started_at) * 1000),
            },
        )
        agent.run_store.write_report(task_state, agent.redact_artifact(agent.build_report(task_state)))
        return final
