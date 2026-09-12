"""Single-client, workspace-bound JSON-RPC transport for local terminal frontends."""

import asyncio
import json
import math
from pathlib import Path
import sys
import threading
from concurrent.futures import TimeoutError as FutureTimeout

from .questions import QuestionBroker
from .runtime_host import RuntimeHost
from .tui import ConfirmationBroker, TUITransport


class RpcError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class TUIRPCServer:
    def __init__(
        self,
        agent,
        send_frame,
        *,
        confirmation_timeout=35,
        session_factory=None,
        question_timeout=600,
        model_selection=None,
    ):
        if (
            type(confirmation_timeout) not in {int, float}
            or not math.isfinite(confirmation_timeout)
            or confirmation_timeout <= 0
        ):
            raise ValueError("confirmation timeout must be positive and finite")
        self.agent, self.send_frame = agent, send_frame
        if (
            type(question_timeout) not in {int, float}
            or not math.isfinite(question_timeout)
            or question_timeout <= 0
        ):
            raise ValueError("question timeout must be positive and finite")
        self.question_timeout = question_timeout
        self.model_selection = model_selection
        self._configuring = False
        self.questions = QuestionBroker(self._notify_question)
        self._old_question_handler = None
        self.host = RuntimeHost(agent, stream_text=True)
        self.confirmations = ConfirmationBroker(self._notify_confirmation)
        self.transport = TUITransport(self.host, confirmation_broker=self.confirmations)
        self.confirmation_timeout = confirmation_timeout
        self.session_id = str(agent.session["id"])
        self._subscriptions = set()
        self._submissions = {}
        self._turns = set()
        self._write_lock = asyncio.Lock()
        self._loop = None
        self._thread_id = None
        self._old_prompt = None
        self.closed = False
        self.transport_failed = False
        self.session_factory = session_factory
        self._switch_failed = False

    async def start(self):
        if self._loop is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._thread_id = threading.get_ident()
        self._old_prompt = self.agent.approval_engine.set_prompt(self._approval)
        self._bind_questions()
        try:
            if self.model_selection is not None:
                self.model_selection.options(self.agent)
            await self.host.start()
        except BaseException:
            await self.close()
            raise

    async def _send(self, frame):
        if self.closed:
            return
        async with self._write_lock:
            try:
                await self.send_frame(self.agent.redact_artifact(frame))
            except Exception:
                self.confirmations.close()
                self.questions.close()
                self.closed = True
                self.transport_failed = True
                raise

    async def _notify_confirmation(self, params):
        await self._send(
            {"jsonrpc": "2.0", "method": "confirm.request", "params": params}
        )

    async def _notify_question(self, params):
        await self._send(
            {"jsonrpc": "2.0", "method": "clarify.request", "params": params}
        )

    def _bind_questions(self):
        tool = self.agent.question_tool
        if tool is not None:
            self._old_question_handler = tool.handler
            tool.handler = self._ask_question

    def _restore_questions(self):
        if self.agent.question_tool is not None:
            self.agent.question_tool.handler = self._old_question_handler

    def _ask_question(self, question, control):
        loop = self._loop
        if (
            self.closed
            or loop is None
            or threading.get_ident() == self._thread_id
            or control.status() != "running"
        ):
            return ""
        coroutine = self.questions.ask(
            self.session_id,
            question["question"],
            question.get("options", []),
            timeout=min(self.question_timeout, control.remaining_seconds),
        )
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()
            return ""
        try:
            while not self.closed and control.status() == "running":
                try:
                    return future.result(timeout=min(0.1, control.remaining_seconds))
                except FutureTimeout:
                    continue
            return ""
        finally:
            if not future.done():
                future.cancel()

    def _approval(self, definition, request, arguments):
        loop = self._loop
        if self.closed or loop is None or threading.get_ident() == self._thread_id:
            return False
        prompt = json.dumps(
            self.agent.redact_artifact(
                {
                    "tool": definition.name,
                    "effect": definition.effect.value,
                    "arguments": dict(arguments),
                }
            ),
            ensure_ascii=True,
        )
        coroutine = self.confirmations.request(
            prompt,
            timeout=self.confirmation_timeout,
            turn_id=request.turn_id,
        )
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()
            return False
        try:
            return future.result(timeout=self.confirmation_timeout + 1)[1]
        except Exception:
            future.cancel()
            return False

    @staticmethod
    def _text(params, key):
        value = params.get(key)
        if not isinstance(value, str) or not value.strip():
            raise RpcError(-32602, f"{key} must be a non-empty string")
        return value

    async def dispatch(self, method, params):
        if self._switch_failed:
            raise RpcError(
                -32007, "runtime unavailable; reconnect to resume a persisted session"
            )
        if self._configuring and method not in {
            "system.initialize",
            "model.options",
            "confirm.respond",
        }:
            raise RpcError(-32003, "configuration update in progress")
        if method == "system.initialize":
            return {
                "session_id": self.session_id,
                "protocol": "repoagent.tui-rpc/v1",
                "streaming": True,
                "capabilities": [
                    "turn.send",
                    "turn.subscribe",
                    "turn.unsubscribe",
                    "turn.cancel",
                    "confirm.respond",
                    *(["clarify.respond"] if "ask_user" in self.agent.tools else []),
                    *(
                        ["model.options", "model.select"]
                        if self.model_selection is not None
                        else []
                    ),
                    *(
                        [
                            "model.save_key",
                            "model.disconnect",
                            "model.add_model",
                            "model.remove_model",
                        ]
                        if self.model_selection is not None
                        and self.model_selection.settings is not None
                        else []
                    ),
                    "session.list",
                    "session.history",
                    "session.title",
                    "session.delete",
                    "session.export",
                    "session.clear",
                    "session.undo",
                    "session.branch",
                    *(
                        ["session.create", "session.resume"]
                        if self.session_factory
                        else []
                    ),
                ],
            }
        if (
            method in {"model.options", "model.select"}
            and self.model_selection is not None
        ):
            if method == "model.options":
                return self.model_selection.options(self.agent)
            if self.host.busy or self.confirmations.pending() or self.questions.pending:
                raise RpcError(
                    -32003, "finish or cancel the active Turn before switching models"
                )
            name = self._text(params, "profile")
            if name not in self.model_selection.profiles:
                raise RpcError(-32602, "unknown model profile")
            try:
                return self.model_selection.select(
                    self.agent, name, params.get("model")
                )
            except Exception:
                raise RpcError(
                    -32005, "model switch failed; previous model retained"
                ) from None
        if (
            method
            in {
                "model.save_key",
                "model.disconnect",
                "model.add_model",
                "model.remove_model",
            }
            and self.model_selection is not None
            and self.model_selection.settings is not None
        ):
            if self.model_selection.settings.path.resolve().is_relative_to(
                self.agent.root.resolve()
            ):
                raise RpcError(
                    -32602, "provider credentials must be stored outside the workspace"
                )
            if self.host.busy or self.confirmations.pending() or self.questions.pending:
                raise RpcError(
                    -32003, "finish or cancel the active Turn before changing providers"
                )
            slug = self._text(params, "slug")
            if slug not in self.model_selection.profiles:
                raise RpcError(-32602, "unknown provider")
            if method == "model.save_key":
                self.agent.register_secret(self._text(params, "api_key"))
            self._configuring = True
            update = asyncio.create_task(
                asyncio.to_thread(
                    self.model_selection.configure,
                    self.agent,
                    method.split(".")[1],
                    params,
                )
            )
            try:
                try:
                    return await asyncio.shield(update)
                except asyncio.CancelledError:
                    await update
                    raise
            except (ValueError, OSError):
                raise RpcError(
                    -32602,
                    "provider update failed; check fields and local settings permissions",
                ) from None
            finally:
                self._configuring = False
        if method in {
            "session.title",
            "session.delete",
            "session.export",
            "session.clear",
            "session.undo",
            "session.branch",
        }:
            from .session_operations import dispatch_session_operation
            from .session_store import StaleSessionWriteError

            try:
                return await dispatch_session_operation(self, method, params)
            except StaleSessionWriteError:
                raise RpcError(
                    -32006, "session changed; refresh before retrying"
                ) from None
        if method == "session.list":
            offset, limit = self._page(params)
            sessions = []
            for path in sorted(
                self.agent.session_store.root.glob("*.json"), reverse=True
            ):
                try:
                    payload = self._session(path.stem)
                except RpcError:
                    continue
                sessions.append(
                    {
                        "session_id": path.stem,
                        "created_at": payload.get("created_at", ""),
                        "history_count": len(payload["history"]),
                        "title": payload.get("title", ""),
                        "current": path.stem == self.session_id,
                    }
                )
            return {
                "sessions": sessions[offset : offset + limit],
                "total": len(sessions),
            }
        if method == "session.history":
            session_id = params.get("session_id", self.session_id)
            payload = self._session(session_id)
            offset, limit = self._page(params)
            messages = []
            for message in payload["history"][offset : offset + limit]:
                if not isinstance(message, dict):
                    raise RpcError(-32602, "invalid stored history")
                messages.append(
                    {
                        "role": str(message.get("role", "")),
                        "content": self.agent.redact_text(
                            str(message.get("content", ""))
                        )[:16000],
                    }
                )
            return {
                "session_id": session_id,
                "messages": messages,
                "total": len(payload["history"]),
            }
        if method in {
            "session.create",
            "session.resume",
            "session.new",
            "session.select",
        }:
            selected = (
                self._text(params, "session_id")
                if method in {"session.resume", "session.select"}
                else None
            )
            return await self._switch_session(selected)
        if method.startswith("turn."):
            if params.get("session_id", self.session_id) != self.session_id:
                raise RpcError(-32602, "connection is bound to another session")
        if method == "turn.subscribe":
            streaming = params.get("stream", False)
            if type(streaming) is not bool:
                raise RpcError(-32602, "stream must be boolean")
            if len(self._subscriptions) >= 16:
                raise RpcError(-32001, "subscription limit reached")
            subscription = None

            async def notify(event):
                if event["type"] == "turn.text.delta" and not streaming:
                    return
                await self._send(
                    {
                        "jsonrpc": "2.0",
                        "method": "turn.event",
                        "params": {
                            "subscription_id": subscription,
                            "session_id": self.session_id,
                            **event,
                        },
                    }
                )

            subscription = self.transport.subscribe(self.session_id, notify)
            self._subscriptions.add(subscription)
            return {"subscription_id": subscription}
        if method == "turn.unsubscribe":
            key = params.get("subscription_id")
            if type(key) is not int:
                raise RpcError(-32602, "subscription_id must be an integer")
            removed = key in self._subscriptions and self.transport.unsubscribe(
                self.session_id, key
            )
            self._subscriptions.discard(key)
            return {"unsubscribed": bool(removed)}
        if method == "turn.send":
            content = self._text(params, "content")
            submission = self._text(params, "submission_id")
            previous = self._submissions.get(submission)
            if previous is not None:
                if previous[0] != content:
                    raise RpcError(
                        -32602, "submission_id reused with different content"
                    )
                return {**previous[1], "duplicate": True}
            if len(self._submissions) >= 1000:
                raise RpcError(-32001, "connection submission limit reached")
            accepted = await self.transport.send(
                self.session_id, content, submission_id=submission
            )
            self._submissions[submission] = (content, accepted)
            self._turns.add(accepted["turn_id"])
            return accepted
        if method == "turn.cancel":
            turn_id = self._text(params, "turn_id")
            if turn_id not in self._turns:
                return {"cancelled": False}
            self.confirmations.cancel(turn_id)
            cancelled = self.transport.cancel(turn_id)
            if cancelled:
                outcome = await self.host.wait(turn_id)
                cancelled = outcome.state.value == "cancelled"
            return {"cancelled": cancelled}
        if method == "confirm.respond":
            request_id = self._text(params, "request_id")
            answer = params.get("approved")
            if type(answer) is not bool:
                raise RpcError(-32602, "approved must be boolean")
            return {"accepted": self.transport.confirm(request_id, answer)}
        if method == "clarify.respond" and "ask_user" in self.agent.tools:
            key = self._text(
                params,
                "conversation_id" if "conversation_id" in params else "request_id",
            )
            answer = params.get("answer")
            if not isinstance(answer, str) or len(answer) > 16000:
                raise RpcError(
                    -32602, "answer must be a string of at most 16000 characters"
                )
            return {"ok": self.questions.reply(key, answer)}
        raise RpcError(-32601, "method not found")

    @staticmethod
    def _page(params):
        offset, limit = params.get("offset", 0), params.get("limit", 50)
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise RpcError(-32602, "invalid pagination")
        return offset, limit

    def _session(self, session_id):
        try:
            payload = self.agent.session_store.inspect(session_id)
            workspace = payload.get("workspace_root")
            if (
                not isinstance(workspace, str)
                or not workspace
                or Path(workspace).resolve() != self.agent.root.resolve()
            ):
                raise ValueError("workspace mismatch")
            if not isinstance(payload.get("history"), list):
                raise ValueError("invalid history")
            return payload
        except (OSError, ValueError, TypeError):
            raise RpcError(-32602, "session is unavailable in this workspace") from None

    async def _switch_session(self, selected, *, force=False):
        if self.session_factory is None:
            raise RpcError(-32601, "session switching is not configured")
        if self.host.busy or self.confirmations.pending() or self.questions.pending:
            raise RpcError(
                -32003, "finish or cancel the active Turn before switching sessions"
            )
        if selected is not None:
            self._session(selected)
            if selected == self.session_id and not force:
                return {"session_id": selected, "changed": False}
        root = self.agent.root.resolve()
        try:
            await self.host.stop()
            self.confirmations.close()
            self.questions.close()
            self._restore_questions()
            self.agent.approval_engine.set_prompt(self._old_prompt)
            for subscription in self._subscriptions:
                self.transport.unsubscribe(self.session_id, subscription)
            self._subscriptions.clear()
            self._submissions.clear()
            self._turns.clear()
            replacement = self.session_factory(selected)
            if replacement.root.resolve() != root or (
                selected is not None and replacement.session["id"] != selected
            ):
                await replacement.aclose()
                raise ValueError(
                    "session factory returned another workspace or identity"
                )
            if self.model_selection is not None:
                try:
                    self.model_selection.restore(replacement)
                except Exception:
                    await replacement.aclose()
                    raise
            self.agent = replacement
            self.session_id = str(replacement.session["id"])
            self.host = RuntimeHost(replacement, stream_text=True)
            self.confirmations = ConfirmationBroker(self._notify_confirmation)
            self.questions = QuestionBroker(self._notify_question)
            self.transport = TUITransport(
                self.host, confirmation_broker=self.confirmations
            )
            self._old_prompt = replacement.approval_engine.set_prompt(self._approval)
            self._bind_questions()
            await self.host.start()
        except Exception:
            self._switch_failed = True
            raise RpcError(
                -32004, "session switch failed; reconnect to resume a persisted session"
            ) from None
        return {"session_id": self.session_id, "changed": True, "resubscribe": True}

    async def receive(self, line):
        if self.closed:
            return
        request_id, notification = None, False
        try:
            try:
                frame = json.loads(
                    line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
                )
            except (ValueError, TypeError):
                raise RpcError(-32700, "parse error") from None
            if (
                not isinstance(frame, dict)
                or frame.get("jsonrpc") != "2.0"
                or not isinstance(frame.get("method"), str)
            ):
                raise RpcError(-32600, "invalid request")
            notification = "id" not in frame
            if not notification:
                if type(frame["id"]) not in {int, str}:
                    raise RpcError(-32600, "request id must be an integer or string")
                request_id = frame["id"]
            params = frame.get("params", {})
            if not isinstance(params, dict):
                raise RpcError(-32602, "params must be an object")
            result = await self.dispatch(frame["method"], params)
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except RpcError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": exc.code, "message": str(exc)},
            }
        except Exception:
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": "internal error"},
            }
        if not notification:
            await self._send(response)
        if self._switch_failed:
            self.closed = True

    async def close(self):
        self.closed = True
        self.confirmations.close()
        self.questions.close()
        for subscription in self._subscriptions:
            self.transport.unsubscribe(self.session_id, subscription)
        self._subscriptions.clear()
        for turn_id in self._turns:
            self.transport.cancel(turn_id)
        try:
            await self.host.stop()
        finally:
            if self._loop is not None:
                self.agent.approval_engine.set_prompt(self._old_prompt)
                self._restore_questions()
                self._loop = None


async def run_tui_rpc(
    agent, *, reader=None, writer=None, session_factory=None, model_selection=None
):
    """Newline-delimited JSON, 1 MiB inbound limit, EOF closes owned work."""
    reader = sys.stdin.buffer if reader is None else reader
    writer = sys.stdout if writer is None else writer

    async def send(frame):
        text = json.dumps(frame, ensure_ascii=True, allow_nan=False) + "\n"

        def write():
            writer.write(text)
            writer.flush()

        await asyncio.to_thread(write)

    server = TUIRPCServer(
        agent, send, session_factory=session_factory, model_selection=model_selection
    )
    try:
        await server.start()
        while not server.closed:
            line = await asyncio.to_thread(reader.readline, 1024 * 1024 + 1)
            if not line:
                break
            if len(line) > 1024 * 1024:
                await server._send(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32600,
                            "message": "frame exceeds 1 MiB limit",
                        },
                    }
                )
                return 2
            await server.receive(line)
        return 2 if server.transport_failed or server._switch_failed else 0
    finally:
        await server.close()
