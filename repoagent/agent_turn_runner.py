"""Adapt the existing AgentLoop to the runtime spine contract."""

import asyncio

from .agent_loop import AgentLoop
from .spine import Text, TurnOutcome, TurnState, Usage


def _usage_from_metadata(metadata) -> Usage:
    metadata = dict(metadata or {})
    prompt = int(metadata.get("input_tokens") or metadata.get("prompt_tokens") or 0)
    completion = int(
        metadata.get("output_tokens") or metadata.get("completion_tokens") or 0
    )
    total = int(metadata.get("total_tokens") or prompt + completion)
    return Usage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)


class AgentTurnRunner:
    def __init__(self, agent) -> None:
        self._agent = agent
        self._loop = AgentLoop(agent)

    async def run(self, request, emit, drain) -> TurnOutcome:
        event_loop = asyncio.get_running_loop()
        streamed_text = []

        def emit_model_text(content):
            streamed_text.append(content)
            future = asyncio.run_coroutine_threadsafe(
                emit(Text(content=content)), event_loop
            )
            future.result()

        final_answer = await asyncio.to_thread(
            self._loop.run,
            request.text,
            turn_request=request,
            model_text_sink=emit_model_text,
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
            explicit_reply=True,
            tool_calls=int(task_state.tool_steps),
        )
