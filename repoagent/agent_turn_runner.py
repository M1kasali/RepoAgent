"""Adapt the existing AgentLoop to the runtime spine contract."""

import asyncio

from .agent_loop import AgentLoop
from .memory_backend import MemoryHit
from .providers import CancellationToken, ProviderCancelledError
from .spine import Text, TurnOutcome, TurnState, Usage


def _consume_background_result(task):
    try:
        task.result()
    except BaseException:
        pass


def _usage_from_metadata(metadata) -> Usage:
    metadata = dict(metadata or {})
    prompt = int(metadata.get("input_tokens") or metadata.get("prompt_tokens") or 0)
    completion = int(
        metadata.get("output_tokens") or metadata.get("completion_tokens") or 0
    )
    total = int(metadata.get("total_tokens") or prompt + completion)
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cache_read_tokens=int(metadata.get("cache_read_tokens") or 0),
        cache_write_tokens=int(metadata.get("cache_write_tokens") or 0),
        source=str(metadata.get("usage_source") or "missing"),
        input_token_semantics=str(
            metadata.get("input_token_semantics") or "ambiguous"
        ),
        model_call_count=int(metadata.get("model_call_count") or 0),
        source_counts=dict(metadata.get("usage_source_counts") or {}),
    )


class AgentTurnRunner:
    def __init__(self, agent) -> None:
        self._agent = agent
        self._loop = AgentLoop(agent)

    async def run(self, request, emit, drain) -> TurnOutcome:
        event_loop = asyncio.get_running_loop()
        cancellation_token = CancellationToken()
        streamed_text = []
        backend = self._agent.memory_backend
        backend_metadata = {
            "backend": type(backend).__name__,
            "recall_status": "not_run",
            "store_status": "not_run",
            "recalled_count": 0,
            "stored_message_count": 0,
            "rejected_secret_hits": 0,
        }
        try:
            hits = await backend.recall(
                self._agent.redact_text(request.text),
                agent_id=str(request.session_id),
                top_k=3,
            )
            safe_hits = []
            rejected_secret_hits = 0
            for hit in hits:
                safe_text = self._agent.redact_text(hit.text)
                if safe_text != hit.text:
                    rejected_secret_hits += 1
                    continue
                safe_hits.append(
                    MemoryHit(
                        text=safe_text,
                        score=hit.score,
                        metadata=self._agent.redact_artifact(hit.metadata),
                    )
                )
            self._agent.backend_memory_hits = safe_hits
            backend_metadata.update(
                {
                    "recall_status": "completed",
                    "recalled_count": len(safe_hits),
                    "rejected_secret_hits": rejected_secret_hits,
                }
            )
        except Exception as exc:
            backend_metadata.update(
                {
                    "recall_status": "failed",
                    "recall_error": f"{type(exc).__name__}: {exc}",
                }
            )
            self._agent.last_memory_backend_metadata = backend_metadata
            self._agent.backend_memory_hits = []
            return TurnOutcome(
                turn_id=request.turn_id,
                request_id=request.request_id,
                session_id=request.session_id,
                state=TurnState.FAILED,
                error=backend_metadata["recall_error"],
            )
        self._agent.last_memory_backend_metadata = backend_metadata

        def emit_model_text(content):
            streamed_text.append(content)
            future = asyncio.run_coroutine_threadsafe(
                emit(Text(content=content)), event_loop
            )
            future.result()

        worker = asyncio.create_task(
            asyncio.to_thread(
                self._loop.run,
                request.text,
                turn_request=request,
                model_text_sink=emit_model_text,
                cancellation_token=cancellation_token,
            )
        )
        try:
            final_answer = await asyncio.shield(worker)
        except asyncio.CancelledError:
            cancellation_token.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(worker), timeout=2.0)
            except ProviderCancelledError:
                pass
            except asyncio.TimeoutError:
                worker.add_done_callback(_consume_background_result)
            except Exception:
                pass
            task_state = self._agent.current_task_state
            if task_state is not None and task_state.status == "running":
                task_state.stop_tool_cancelled("Turn execution cancelled.")
                self._agent.run_store.write_task_state(task_state)
                self._agent.emit_trace(
                    task_state,
                    "tool_cancelled",
                    {"reason": "turn_cancelled", "status": "cancelled"},
                )
            self._agent.backend_memory_hits = []
            raise
        except Exception as exc:
            self._agent.backend_memory_hits = []
            task_state = self._agent.current_task_state
            error = f"{type(exc).__name__}: {exc}"
            if task_state is not None:
                task_state.stop_model_error(error)
                self._agent.run_store.write_task_state(task_state)
                self._agent.emit_trace(
                    task_state,
                    "run_failed",
                    {
                        "status": task_state.status,
                        "stop_reason": task_state.stop_reason,
                        "error": error,
                        "call_efficiency": dict(
                            self._agent.last_call_efficiency_summary
                        ),
                    },
                )
                self._agent.run_store.write_report(
                    task_state,
                    self._agent.redact_artifact(
                        self._agent.build_report(task_state)
                    ),
                )
            return TurnOutcome(
                turn_id=request.turn_id,
                request_id=request.request_id,
                session_id=request.session_id,
                state=TurnState.FAILED,
                usage=_usage_from_metadata(
                    self._agent.last_completion_metadata
                ),
                call_efficiency=dict(
                    self._agent.last_call_efficiency_summary
                ),
                tool_calls=int(task_state.tool_steps) if task_state else 0,
                error=error,
            )
        if not streamed_text:
            await emit(Text(content=final_answer))
        task_state = self._agent.current_task_state
        messages = self._agent.redact_artifact(
            [
                {"role": "user", "content": request.text},
                {"role": "assistant", "content": final_answer},
            ]
        )
        try:
            await backend.store(str(request.session_id), messages)
            backend_metadata.update(
                {
                    "store_status": "completed",
                    "stored_message_count": len(messages),
                }
            )
            if backend is self._agent.memory:
                self._agent.session["memory"] = self._agent.memory.to_dict()
                self._agent.session_path = self._agent.session_store.save(
                    self._agent.session
                )
        except Exception as exc:
            backend_metadata.update(
                {
                    "store_status": "failed",
                    "store_error": f"{type(exc).__name__}: {exc}",
                }
            )
            self._agent.last_memory_backend_metadata = backend_metadata
            self._agent.backend_memory_hits = []
            self._agent.emit_trace(
                task_state, "memory_backend_store_failed", backend_metadata
            )
            self._agent.run_store.write_report(
                task_state,
                self._agent.redact_artifact(
                    self._agent.build_report(task_state)
                ),
            )
            return TurnOutcome(
                turn_id=request.turn_id,
                request_id=request.request_id,
                session_id=request.session_id,
                state=TurnState.FAILED,
                usage=_usage_from_metadata(
                    self._agent.last_completion_metadata
                ),
                call_efficiency=dict(
                    self._agent.last_call_efficiency_summary
                ),
                tool_calls=int(task_state.tool_steps),
                error=backend_metadata["store_error"],
            )
        self._agent.last_memory_backend_metadata = backend_metadata
        self._agent.run_store.write_report(
            task_state,
            self._agent.redact_artifact(self._agent.build_report(task_state)),
        )
        self._agent.backend_memory_hits = []
        return TurnOutcome(
            turn_id=request.turn_id,
            request_id=request.request_id,
            session_id=request.session_id,
            state=TurnState.COMPLETED,
            final_answer=final_answer,
            usage=_usage_from_metadata(self._agent.last_completion_metadata),
            call_efficiency=dict(self._agent.last_call_efficiency_summary),
            explicit_reply=True,
            tool_calls=int(task_state.tool_steps),
        )
