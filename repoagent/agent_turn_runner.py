"""Adapt the existing AgentLoop to the runtime spine contract."""

import asyncio

from .agent_loop import AgentLoop
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
            raise
        except Exception as exc:
            task_state = self._agent.current_task_state
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
                error=f"{type(exc).__name__}: {exc}",
            )
        if not streamed_text:
            await emit(Text(content=final_answer))
        task_state = self._agent.current_task_state
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
