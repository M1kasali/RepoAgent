"""Bounded human questions, separate from effect authorization."""

import asyncio
from dataclasses import dataclass
from uuid import uuid4

from .tool_contracts import ToolDefinition, ToolEffect
from .tool_execution import ToolRunnerOutput


QUESTION_DEFINITION = ToolDefinition(
    name="ask_user",
    description="Ask the user related questions when a decision depends on their preference. Options are suggestions, not authorization.",
    parameters={
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 4000,
                        },
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 500,
                            },
                        },
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["questions"],
        "additionalProperties": False,
    },
    effect=ToolEffect.READ,
    concurrency_safe=False,
    timeout_seconds=1800,
)


class QuestionTool:
    def __init__(self):
        self.handler = None

    def run(self, arguments, control):
        questions = arguments["questions"]
        if not 1 <= len(questions) <= 3:
            raise ValueError("ask_user requires between one and three questions")
        for entry in questions:
            if not entry["question"].strip() or len(entry.get("options", [])) > 12:
                raise ValueError("invalid question or too many options")
        if self.handler is None:
            return "Error: ask_user is unavailable in this transport"
        results = []
        for entry in questions:
            if control.status() != "running":
                return ToolRunnerOutput(
                    "Question interrupted", {"execution_status": control.status()}
                )
            answer = self.handler(entry, control)
            if control.status() != "running":
                return ToolRunnerOutput(
                    "Question interrupted", {"execution_status": control.status()}
                )
            results.append(
                f'User answered: "{entry["question"]}" -> "{answer}".'
                if answer
                else f'For "{entry["question"]}": user did not answer; proceed with best judgment.'
            )
        return " ".join(results)


@dataclass
class PendingQuestion:
    request_id: str
    future: asyncio.Future


class QuestionBroker:
    def __init__(self, notify):
        self.notify = notify
        self.pending = {}
        self.closed = False

    async def ask(self, conversation_id, question, choices, *, timeout=600):
        if self.closed:
            return ""
        old = self.pending.get(conversation_id)
        if old is not None and not old.future.done():
            old.future.set_result("")
        current = PendingQuestion(
            uuid4().hex, asyncio.get_running_loop().create_future()
        )
        self.pending[conversation_id] = current
        try:

            async def wait():
                await self.notify(
                    {
                        "conversation_id": conversation_id,
                        "request_id": current.request_id,
                        "question": question,
                        "choices": choices,
                    }
                )
                return await current.future

            return await asyncio.wait_for(wait(), timeout)
        except Exception:
            return ""
        finally:
            if self.pending.get(conversation_id) is current:
                self.pending.pop(conversation_id, None)

    def reply(self, key, answer):
        if not isinstance(answer, str) or len(answer) > 16000:
            raise ValueError("answer must be a string of at most 16000 characters")
        for conversation, pending in self.pending.items():
            if key in {conversation, pending.request_id}:
                if pending.future.done():
                    return False
                pending.future.set_result(answer)
                return True
        return False

    def cancel_all(self):
        for pending in self.pending.values():
            if not pending.future.done():
                pending.future.set_result("")

    def close(self):
        self.closed = True
        self.cancel_all()
