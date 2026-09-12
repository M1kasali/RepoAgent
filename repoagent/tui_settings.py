"""Terminal management dialogs. Secrets never enter the chat composer."""

from rich.text import Text
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

from .tui_rpc import RpcError


class ManagementScreen(ModalScreen):
    BINDINGS = [("escape", "close", "Close")]
    CSS = """
    ManagementScreen { align: center middle; background: #000000 50%; }
    ManagementScreen > VerticalScroll { width: 90%; max-width: 90; height: 90%; border: solid #65c6b4; padding: 0 1; background: #181a1b; }
    ManagementScreen Static { height: auto; }
    ManagementScreen Horizontal { height: 3; }
    ManagementScreen Input, ManagementScreen Select { height: 3; }
    ManagementScreen Button { min-width: 8; margin-right: 1; }
    """

    def action_close(self):
        if not getattr(self, "busy", False):
            self.dismiss(None)

    def show_status(self, message):
        self.query_one("#management-status", Static).update(Text(message))


class ProviderScreen(ManagementScreen):
    def compose(self):
        with VerticalScroll():
            yield Static("Providers", markup=False)
            yield Select([], id="provider-choice", prompt="Provider")
            yield Static("API key", markup=False)
            yield Input(password=True, id="provider-key")
            yield Static("API base", markup=False)
            yield Input(id="provider-base")
            with Horizontal():
                yield Button("Save key", id="save-key")
                yield Button("Disconnect", id="disconnect")
            yield Select([], id="provider-model", prompt="Model")
            yield Input(id="model-name", placeholder="Model name")
            with Horizontal():
                yield Button("Add", id="add-model")
                yield Button("Remove", id="remove-model")
                yield Button("Use", id="use-model")
            yield Static("", id="management-status", markup=False)
            yield Button("Close", id="close-management")

    async def on_mount(self):
        self.rows = {}
        await self.refresh_entries()

    async def refresh_entries(self, selected=None):
        options = await self.app.rpc("model.options")
        self.rows = {row["name"]: row for row in options["profiles"]}
        picker = self.query_one("#provider-choice", Select)
        picker.set_options((Text(name), name) for name in self.rows)
        picker.value = (
            selected or options["profile"] or next(iter(self.rows), Select.BLANK)
        )
        self.render_provider()

    def render_provider(self):
        name = self.query_one("#provider-choice", Select).value
        if name not in self.rows:
            return
        row = self.rows[name]
        self.query_one("#save-key").disabled = not row["requires_credentials"]
        self.query_one("#provider-key", Input).value = ""
        self.query_one("#provider-base", Input).value = row["api_base"]
        picker = self.query_one("#provider-model", Select)
        picker.set_options((Text(model), model) for model in row["models"])
        picker.value = row["models"][0] if row["models"] else Select.BLANK
        self.show_status(
            "Environment key present"
            if row["environment_credential_present"]
            else "Saved key present"
            if row["saved_credential_present"]
            else "No saved key"
        )

    def on_select_changed(self, event: Select.Changed):
        event.stop()
        if event.select.id == "provider-choice":
            self.render_provider()
        elif event.select.id == "provider-model" and event.value != Select.BLANK:
            self.query_one("#model-name", Input).value = str(event.value)

    async def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        action = event.button.id
        if action == "close-management":
            self.action_close()
            return
        slug = self.query_one("#provider-choice", Select).value
        if slug == Select.BLANK:
            return
        for button in self.query(Button):
            button.disabled = True
        self.busy = True
        try:
            if action == "save-key":
                key = self.query_one("#provider-key", Input).value
                self.query_one("#provider-key", Input).value = ""
                await self.app.rpc(
                    "model.save_key",
                    slug=slug,
                    api_key=key,
                    api_base=self.query_one("#provider-base", Input).value,
                )
            elif action == "disconnect":
                await self.app.rpc("model.disconnect", slug=slug)
            elif action in {"add-model", "remove-model"}:
                await self.app.rpc(
                    "model." + action.replace("-", "_"),
                    slug=slug,
                    model=self.query_one("#model-name", Input).value,
                )
            elif action == "use-model":
                await self.app.rpc(
                    "model.select",
                    profile=slug,
                    model=self.query_one("#model-name", Input).value,
                )
            await self.refresh_entries(slug)
            self.show_status(
                "Model selected"
                if action == "use-model"
                else "Saved locally; active model unchanged"
            )
        except RpcError as exc:
            self.show_status(str(exc))
        except Exception:
            self.show_status("Provider operation failed")
        finally:
            self.busy = False
            for button in self.query(Button):
                button.disabled = False
            if slug in self.rows:
                self.query_one("#save-key").disabled = not self.rows[slug][
                    "requires_credentials"
                ]


class SessionScreen(ManagementScreen):
    def compose(self):
        with VerticalScroll():
            yield Static("Sessions", markup=False)
            yield Select([], id="managed-session", prompt="Session")
            yield Input(id="session-title", placeholder="Title")
            with Horizontal():
                yield Button("Rename", id="rename-session")
                yield Button("Export", id="export-session")
            yield Button("Delete", id="delete-session")
            with Horizontal():
                yield Button("Undo turn", id="undo-session")
                yield Button("Clear", id="clear-session")
                yield Button("Branch", id="branch-session")
            yield Static("", id="management-status", markup=False)
            yield Button("Close", id="close-management")

    async def on_mount(self):
        self.delete_revision = None
        self.rewrite_confirmation = None
        for name in ("undo-session", "clear-session"):
            self.query_one("#" + name, Button).active_effect_duration = 0
        self.query_one("#delete-session", Button).active_effect_duration = 0
        self.rows = {}
        await self.refresh_entries()

    async def refresh_entries(self):
        self.rows = {}
        offset = 0
        while True:
            page = await self.app.rpc("session.list", offset=offset, limit=100)
            self.rows.update((row["session_id"], row) for row in page["sessions"])
            offset += len(page["sessions"])
            if not page["sessions"] or offset >= page["total"]:
                break
        picker = self.query_one("#managed-session", Select)
        picker.set_options(
            (Text(row.get("title") or name), name) for name, row in self.rows.items()
        )
        picker.value = self.app.server.session_id

    def on_select_changed(self, event: Select.Changed):
        event.stop()
        self.delete_revision = None
        self.reset_rewrite_confirmation()
        self.query_one("#delete-session", Button).label = "Delete"
        if event.value in self.rows:
            self.query_one("#session-title", Input).value = self.rows[event.value].get(
                "title", ""
            )
        self.query_one("#delete-session").disabled = (
            event.value == self.app.server.session_id
        )

    def reset_rewrite_confirmation(self):
        self.rewrite_confirmation = None
        self.query_one("#undo-session", Button).label = "Undo turn"
        self.query_one("#clear-session", Button).label = "Clear"

    async def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        action = event.button.id
        if action == "close-management":
            self.action_close()
            return
        session_id = self.query_one("#managed-session", Select).value
        if session_id == Select.BLANK:
            return
        if self.rewrite_confirmation and self.rewrite_confirmation[:2] != (
            action,
            session_id,
        ):
            self.reset_rewrite_confirmation()
        try:
            if action in {"undo-session", "clear-session"}:
                revision = (
                    self.rewrite_confirmation[2] if self.rewrite_confirmation else None
                )
                result = await self.app.rpc(
                    "session." + action.split("-")[0],
                    session_id=session_id,
                    confirmed=revision is not None,
                    revision=revision,
                )
                if result.get("confirmation_required"):
                    self.rewrite_confirmation = (action, session_id, result["revision"])
                    event.button.label = (
                        "Confirm undo" if action == "undo-session" else "Confirm clear"
                    )
                    self.show_status(
                        "Remove conversation history? Workspace files and shared memory remain."
                    )
                else:
                    self.reset_rewrite_confirmation()
                    await self.refresh_entries()
                    self.show_status(
                        "History updated"
                        if result.get("changed")
                        else "No turns to undo"
                    )
            elif action == "branch-session":
                result = await self.app.rpc("session.branch", session_id=session_id)
                if result["session_id"]:
                    await self.app.rpc(
                        "session.resume", session_id=result["session_id"]
                    )
                    await self.refresh_entries()
                    self.show_status("Branch opened")
                else:
                    self.show_status("No history to branch")
            elif action == "rename-session":
                await self.app.rpc(
                    "session.title",
                    session_id=session_id,
                    title=self.query_one("#session-title", Input).value,
                )
                await self.refresh_entries()
                self.show_status("Title saved")
            elif action == "export-session":
                result = await self.app.rpc("session.export", session_id=session_id)
                self.show_status(result["path"])
            elif action == "delete-session":
                result = await self.app.rpc(
                    "session.delete",
                    session_id=session_id,
                    confirmed=self.delete_revision is not None,
                    revision=self.delete_revision,
                )
                if result.get("confirmation_required"):
                    self.delete_revision = result["revision"]
                    self.query_one("#delete-session", Button).label = "Confirm delete"
                    self.show_status(
                        "Delete this session? Stored run evidence is retained."
                    )
                else:
                    await self.refresh_entries()
                    self.show_status("Session deleted")
        except RpcError as exc:
            self.reset_rewrite_confirmation()
            self.delete_revision = None
            self.query_one("#delete-session", Button).label = "Delete"
            self.show_status(str(exc))
        except Exception:
            self.show_status("Session operation failed")
