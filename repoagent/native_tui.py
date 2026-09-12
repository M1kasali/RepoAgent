"""Optional full-screen frontend; all Agent operations use the RPC service."""

from uuid import uuid4

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, RichLog, Select, Static, TextArea

from .tui_rpc import RpcError, TUIRPCServer


class RuntimeFrame(Message):
    def __init__(self, frame):
        super().__init__()
        self.frame = frame


class RepoAgentApp(App):
    TITLE = "RepoAgent"
    BINDINGS = [("ctrl+enter", "send", "Send"), ("ctrl+q", "quit", "Quit")]
    CSS = """
    Screen { background: #181a1b; color: #e5e7eb; }
    #identity { height: 2; padding: 0 1; color: #65c6b4; }
    #sessions { height: 3; }
    #session { width: 1fr; }
    #model-row { height: 3; }
    #model { width: 1fr; }
    Button { min-width: 8; margin: 0 1 0 0; }
    #transcript { height: 1fr; padding: 0 1; }
    #preview { height: auto; max-height: 8; padding: 0 1; color: #b4c4eb; }
    #confirmation { height: auto; max-height: 8; border: solid #d7ae64; }
    #prompt { height: auto; max-height: 3; overflow-y: auto; }
    #decisions, #actions { height: 3; }
    #question { height: auto; max-height: 3; overflow-y: auto; padding: 0 1; }
    #choices { height: 3; }
    #composer { height: 5; border: solid #696f78; }
    #status { width: 1fr; padding: 1 0; }
    """

    def __init__(self, agent, *, session_factory=None, model_selection=None):
        super().__init__()
        self.server = TUIRPCServer(
            agent,
            self._receive,
            session_factory=session_factory,
            model_selection=model_selection,
        )
        self.active_turn = None
        self.confirmation_id = None
        self.question_id = None
        self.preview = ""
        self.ready = False
        self.switching = False
        self.session_options = []

    def compose(self) -> ComposeResult:
        yield Static("RepoAgent", id="identity", markup=False)
        with Horizontal(id="sessions"):
            yield Select([], prompt="Session", id="session")
            yield Button("New", id="new")
            yield Button("Manage", id="manage-sessions")
        with Horizontal(id="model-row"):
            yield Select([], prompt="Model", id="model")
            yield Button("Settings", id="manage-providers")
        yield RichLog(id="transcript", wrap=True, markup=False, max_lines=5000)
        yield Static("", id="preview", markup=False)
        yield Static("", id="question", markup=False)
        yield Select([], prompt="Suggested answers", id="choices")
        with Vertical(id="confirmation"):
            yield Static("", id="prompt", markup=False)
            with Horizontal(id="decisions"):
                yield Button("Deny", id="deny")
                yield Button("Approve", id="approve")
        yield TextArea(id="composer")
        with Horizontal(id="actions"):
            yield Button("Send", id="send")
            yield Button("Skip", id="skip")
            yield Button("Cancel", id="cancel")
            yield Static("Starting", id="status", markup=False)
            yield Button("Quit", id="quit")

    async def _receive(self, frame):
        # Never wait on UI handlers while the RPC writer lock is held.
        self.post_message(RuntimeFrame(frame))

    async def rpc(self, method, **params):
        if self.server.closed:
            raise RpcError(-32000, "Runtime closed; restart the terminal UI")
        result = await self.server.dispatch(method, params)
        return self.server.agent.redact_artifact(result)

    async def on_mount(self):
        # A fast Turn may reuse Send for a question before Button's debounce ends.
        self.query_one("#send", Button).active_effect_duration = 0
        self.query_one("#confirmation").display = False
        self.query_one("#preview").display = False
        self.clear_question()
        self.query_one("#model-row").display = self.server.model_selection is not None
        self.query_one("#manage-providers").display = (
            self.server.model_selection is not None
            and self.server.model_selection.settings is not None
        )
        self.controls()
        try:
            await self.server.start()
            await self.refresh_session()
            await self.refresh_models()
            self.ready = True
            self.status("Ready")
            self.set_interval(0.2, self.expire_confirmation)
            self.query_one("#composer").focus()
        except Exception as exc:
            self.status(f"Startup failed: {type(exc).__name__}")
            await self.server.close()
        self.controls()

    def on_resize(self, event):
        if self.is_mounted:
            self.query_one("#composer").styles.height = (
                3 if event.size.height < 30 else 5
            )

    def status(self, value):
        self.query_one("#status", Static).update(Text(value))

    def controls(self):
        busy = not self.ready or self.active_turn is not None or self.switching
        for name in (
            "send",
            "composer",
            "session",
            "new",
            "model",
            "manage-sessions",
            "manage-providers",
        ):
            self.query_one(f"#{name}").disabled = busy
        if self.server.session_factory is None:
            self.query_one("#new").disabled = True
            self.query_one("#session").disabled = True
        self.query_one("#cancel").disabled = self.active_turn is None
        if self.question_id is not None:
            self.query_one("#send").disabled = False
            self.query_one("#composer").disabled = False

    async def refresh_session(self, *, subscribe=True):
        if subscribe:
            await self.rpc("turn.subscribe", stream=True)
        self.query_one("#identity", Static).update(
            Text(f"RepoAgent  |  {self.server.session_id}")
        )
        self.session_options = []
        offset = 0
        while True:
            page = await self.rpc("session.list", offset=offset, limit=100)
            self.session_options.extend(
                (Text(row.get("title") or row["session_id"]), row["session_id"])
                for row in page["sessions"]
            )
            offset += len(page["sessions"])
            if offset >= page["total"] or not page["sessions"]:
                break
        picker = self.query_one("#session", Select)
        picker.set_options(self.session_options)
        picker.value = self.server.session_id
        first = await self.rpc("session.history", limit=1)
        history = await self.rpc(
            "session.history", offset=max(0, first["total"] - 100), limit=100
        )
        log = self.query_one("#transcript", RichLog)
        log.clear()
        for row in history["messages"]:
            log.write(Text(f"{row['role']}: {row['content']}"))
        self.preview = ""
        self.query_one("#preview", Static).update("")

    async def refresh_models(self):
        if self.server.model_selection is None:
            return
        options = await self.rpc("model.options")
        picker = self.query_one("#model", Select)
        picker.set_options(
            (
                Text(
                    f"{row['name']}: {options['model'] if row['name'] == options['profile'] else row['model']}"
                ),
                row["name"],
            )
            for row in options["profiles"]
        )
        picker.value = options["profile"] or Select.BLANK

    async def select_model(self, name):
        if not self.ready or self.active_turn is not None or self.switching:
            return
        self.switching = True
        self.controls()
        try:
            await self.rpc("model.select", profile=name)
            self.status("Model selected; not remotely verified")
        except RpcError as exc:
            self.status(str(exc))
        finally:
            await self.refresh_models()
            self.switching = False
            self.controls()

    async def action_send(self):
        if self.question_id is not None:
            answer = self.query_one("#composer", TextArea).text.strip()
            if answer:
                await self.answer_question(answer)
            return
        if not self.ready or self.active_turn is not None or self.switching:
            return
        composer = self.query_one("#composer", TextArea)
        content = composer.text.strip()
        if not content:
            return
        self.active_turn = "submitting"
        self.controls()
        try:
            accepted = await self.rpc(
                "turn.send", content=content, submission_id=uuid4().hex
            )
            self.active_turn = accepted["turn_id"]
            composer.clear()
            safe = self.server.agent.redact_artifact(content)
            self.query_one("#transcript", RichLog).write(Text(f"user: {safe}"))
            self.preview = ""
            self.query_one("#preview", Static).update("")
            self.status("Running")
        except Exception as exc:
            self.active_turn = None
            self.status(
                str(exc)
                if isinstance(exc, RpcError)
                else f"Send failed: {type(exc).__name__}"
            )
        self.controls()

    def on_runtime_frame(self, event: RuntimeFrame):
        frame = event.frame
        params = frame.get("params", {})
        if frame.get("method") == "clarify.request":
            pending = self.server.questions.pending.get(self.server.session_id)
            if pending is None or pending.request_id != params["request_id"]:
                return
            self.question_id = params["request_id"]
            self.query_one("#question", Static).update(Text(params["question"]))
            self.query_one("#question").display = True
            choices = list(dict.fromkeys(params["choices"]))
            self.query_one("#choices", Select).set_options(
                (Text(value), value) for value in choices
            )
            self.query_one("#choices").display = bool(choices)
            self.query_one("#skip").display = True
            self.query_one("#preview").display = False
            self.query_one("#composer", TextArea).clear()
            self.status("Answer required")
            self.controls()
            self.query_one("#composer").focus()
        elif frame.get("method") == "confirm.request":
            if params["request_id"] not in self.server.confirmations.pending():
                return
            self.confirmation_id = params["request_id"]
            self.query_one("#prompt", Static).update(Text(params["prompt"]))
            self.query_one("#confirmation").display = True
            self.query_one("#preview").display = False
            self.query_one("#deny").focus()
            self.status("Approval required")
        elif frame.get("method") == "turn.event":
            if params.get("session_id") != self.server.session_id:
                return
            if params.get("turn_id") != self.active_turn:
                return
            if params["type"] == "turn.text.delta":
                self.preview = (self.preview + params["text"])[-16000:]
                self.query_one("#preview", Static).update(Text(self.preview))
                self.query_one("#preview").display = self.confirmation_id is None
            elif params["type"] == "turn.terminal":
                outcome = params["outcome"]
                answer = outcome.get("error") or outcome.get("final_answer") or ""
                self.query_one("#transcript", RichLog).write(
                    Text(f"assistant: {answer}")
                )
                self.active_turn = None
                self.preview = ""
                self.query_one("#preview", Static).update("")
                self.query_one("#preview").display = False
                self.clear_confirmation()
                self.clear_question()
                self.status(str(outcome["state"]))
                self.controls()
                self.query_one("#composer").focus()

    def clear_confirmation(self):
        self.confirmation_id = None
        self.query_one("#confirmation").display = False
        self.query_one("#prompt", Static).update("")

    def expire_confirmation(self):
        pending = self.server.questions.pending.get(self.server.session_id)
        if self.question_id and (
            pending is None or pending.request_id != self.question_id
        ):
            self.clear_question()
            self.status("Running" if self.active_turn else "Ready")
        if (
            self.confirmation_id
            and self.confirmation_id not in self.server.confirmations.pending()
        ):
            self.clear_confirmation()
            self.status("Running" if self.active_turn else "Ready")

    def clear_question(self):
        self.question_id = None
        for name in ("question", "choices", "skip"):
            self.query_one(f"#{name}").display = False
        self.query_one("#question", Static).update("")
        self.query_one("#composer", TextArea).clear()
        self.controls()

    async def answer_question(self, answer):
        result = await self.rpc(
            "clarify.respond", request_id=self.question_id, answer=answer
        )
        self.clear_question()
        self.status("Running" if result["ok"] else "Question expired")

    async def answer_confirmation(self, approved):
        if self.confirmation_id:
            result = await self.rpc(
                "confirm.respond", request_id=self.confirmation_id, approved=approved
            )
            self.clear_confirmation()
            self.status("Running" if result["accepted"] else "Confirmation expired")

    async def switch_session(self, session_id=None):
        if not self.ready or self.active_turn is not None or self.switching:
            return
        self.switching = True
        self.controls()
        try:
            method = "session.resume" if session_id else "session.create"
            params = {"session_id": session_id} if session_id else {}
            result = await self.rpc(method, **params)
            if result["changed"]:
                await self.refresh_session()
                await self.refresh_models()
                self.query_one("#composer", TextArea).clear()
            self.status("Ready")
        except Exception as exc:
            self.status(
                str(exc)
                if isinstance(exc, RpcError)
                else f"Session failed: {type(exc).__name__}"
            )
            self.query_one("#session", Select).value = self.server.session_id
            if self.server._switch_failed:
                self.ready = False
                await self.server.close()
        finally:
            self.switching = False
            self.controls()

    async def on_select_changed(self, event: Select.Changed):
        if event.select.id == "model":
            current = getattr(self.server.agent.model_client, "profile", None)
            if event.value != Select.BLANK and (
                current is None or event.value != current.name
            ):
                await self.select_model(str(event.value))
            return
        if event.select.id == "choices":
            if self.question_id and event.value != Select.BLANK:
                self.query_one("#composer", TextArea).load_text(str(event.value))
            return
        if event.value != Select.BLANK and event.value != self.server.session_id:
            await self.switch_session(str(event.value))

    async def on_button_pressed(self, event: Button.Pressed):
        name = event.button.id
        if name in {"manage-sessions", "manage-providers"}:
            from .tui_settings import ProviderScreen, SessionScreen

            if self.active_turn is None and self.ready:
                self.switching = True
                self.controls()
                self.push_screen(
                    ProviderScreen() if name == "manage-providers" else SessionScreen(),
                    self.management_closed,
                )
        elif name == "send":
            await self.action_send()
        elif name == "skip" and self.question_id:
            await self.answer_question("")
        elif name == "new":
            await self.switch_session()
        elif name in {"approve", "deny"}:
            await self.answer_confirmation(name == "approve")
        elif name == "cancel" and self.active_turn:
            await self.rpc("turn.cancel", turn_id=self.active_turn)
        elif name == "quit":
            self.exit(0)

    async def on_unmount(self):
        await self.server.close()

    async def management_closed(self, result):
        if self.server._switch_failed:
            await self.server.close()
            self.ready = False
            self.switching = False
            self.controls()
            return
        await self.refresh_session(subscribe=not self.server._subscriptions)
        await self.refresh_models()
        self.switching = False
        self.controls()


async def run_native_tui(agent, *, session_factory=None, model_selection=None):
    app = RepoAgentApp(
        agent, session_factory=session_factory, model_selection=model_selection
    )
    try:
        return await app.run_async() or 0
    finally:
        await app.server.close()
