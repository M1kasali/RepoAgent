import asyncio
import threading

import pytest

pytest.importorskip("textual")

from textual.widgets import Select, Static, TextArea

from repoagent import FakeModelClient
from repoagent.native_tui import RepoAgentApp
from repoagent.providers.base import ModelEvent
from test_rpc_sessions import factory
from test_rpc_questions import question_agent
from test_rpc_models import selection


async def until(pilot, predicate):
    for _ in range(100):
        if predicate():
            return
        await pilot.pause(0.02)
    raise AssertionError("UI did not reach expected state")


@pytest.mark.parametrize("size", [(100, 32), (60, 24)])
def test_native_send_session_switch_and_literal_rendering(tmp_path, size):
    async def scenario():
        make = factory(tmp_path)
        app = RepoAgentApp(make(), session_factory=make)
        old_id = app.server.session_id
        async with app.run_test(size=size) as pilot:
            await until(pilot, lambda: app.ready)
            app.query_one("#composer", TextArea).load_text(
                "[bold]literal[/bold]\nsecond line"
            )
            await pilot.click("#send")
            await until(
                pilot,
                lambda: (
                    app.active_turn is None
                    and bool(app.server.agent.session["history"])
                ),
            )
            assert app.server.agent.session["history"][0]["content"].endswith(
                "second line"
            )
            await pilot.click("#new")
            await until(
                pilot, lambda: app.server.session_id != old_id and not app.switching
            )
            assert app.server.agent.session["history"] == []
            app.query_one("#session", Select).value = old_id
            await until(
                pilot, lambda: app.server.session_id == old_id and not app.switching
            )
            assert app.server.agent.session["history"][0]["content"].startswith(
                "[bold]"
            )
            assert len(app.server._subscriptions) == 1
            for selector in ("#composer", "#send", "#cancel", "#quit"):
                region = app.query_one(selector).region
                assert region.width > 0 and region.height > 0
                assert region.right <= size[0] and region.bottom <= size[1]
        assert app.server.closed

    asyncio.run(scenario())


def test_native_partial_preview_then_terminal_and_redaction(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-native-secret")

    async def scenario():
        release = threading.Event()

        class Client(FakeModelClient):
            def stream(self, request):
                result = self.generate(request)
                for part in ("<final>key sk-native-", "secret preview "):
                    yield ModelEvent(kind="text_delta", text=part)
                if not release.wait(5):
                    raise RuntimeError("model not released")
                yield ModelEvent(kind="text_delta", text="done</final>")
                yield ModelEvent(kind="completed", result=result)

        make = factory(tmp_path)
        agent = make()
        agent.model_client = Client(
            ["<final>key sk-native-secret preview done</final>"]
        )
        app = RepoAgentApp(agent, session_factory=make)
        try:
            async with app.run_test(size=(100, 32)) as pilot:
                await until(pilot, lambda: app.ready)
                app.query_one("#composer", TextArea).load_text("hello")
                await pilot.click("#send")
                await until(pilot, lambda: bool(app.preview))
                assert app.server.host.busy
                assert (
                    "<redacted>" in app.preview
                    and "sk-native-secret" not in app.preview
                )
                original = app.server.session_id
                await app.switch_session()
                await app.action_send()
                assert app.server.session_id == original
                assert len(app.server._submissions) == 1
                release.set()
                await until(pilot, lambda: app.active_turn is None)
                assert app.preview == ""
                assert "sk-native-secret" not in app.export_screenshot()
        finally:
            release.set()

    asyncio.run(scenario())


def test_native_cli_forwards_session_factory(monkeypatch, tmp_path):
    import repoagent.cli as cli

    captured = []

    def build(args):
        captured.append(args)
        return object()

    async def run(agent, *, session_factory, model_selection):
        session_factory("previous-session")
        return 0

    monkeypatch.setattr(cli, "build_agent", build)
    monkeypatch.setattr("repoagent.native_tui.run_native_tui", run)
    assert cli.main(["tui", "--native", "--", "--cwd", str(tmp_path)]) == 0
    assert captured[0].approval == "ask"
    assert captured[0].resume is None
    assert captured[1].resume == "previous-session"
    assert captured[0].enable_questions


@pytest.mark.parametrize(
    "action", ["free", "choice", "skip", "cancel", "quit", "timeout"]
)
@pytest.mark.parametrize("size", [(100, 36), (60, 24)])
def test_native_question_and_model_controls(tmp_path, action, size):
    async def scenario():
        agent = question_agent(tmp_path)
        app = RepoAgentApp(agent, model_selection=selection())
        app.server.question_timeout = 0.6 if action == "timeout" else 10
        async with app.run_test(size=size) as pilot:
            await until(pilot, lambda: app.ready)
            app.query_one("#composer", TextArea).load_text("choose a color")
            await pilot.click("#send")
            await until(pilot, lambda: app.question_id is not None)
            assert app.query_one("#model").disabled
            for selector in ("#composer", "#send", "#skip", "#cancel", "#quit"):
                region = app.query_one(selector).region
                assert region.right <= size[0] and region.bottom <= size[1]
            if action == "choice":
                app.query_one("#choices", Select).value = "green"
                await pilot.pause()
                assert app.question_id is not None
                await pilot.click("#send")
            elif action == "free":
                app.query_one("#composer", TextArea).load_text("blue")
                await pilot.click("#send")
            elif action != "timeout":
                await pilot.click(f"#{action}")
            if action != "quit":
                await until(pilot, lambda: app.active_turn is None)
                assert app.question_id is None
                assert not app.query_one("#question").display
                if action in {"free", "choice"}:
                    expected = "blue" if action == "free" else "green"
                    assert expected in agent.model_client.prompts[-1]
                app.query_one("#model", Select).value = "anthropic"
                await until(
                    pilot,
                    lambda: getattr(agent.model_client, "profile", None) is not None,
                )
                assert agent.model_client.profile.model == "test-b"
                assert not app.server.host.busy
        assert app.server.closed
        assert not app.server.questions.pending

    asyncio.run(scenario())


@pytest.mark.parametrize("decision", ["approve", "deny", "cancel", "quit", "timeout"])
@pytest.mark.parametrize("size", [(100, 36), (60, 24)])
def test_native_real_tool_confirmation_lifecycle(tmp_path, decision, size):
    async def scenario():
        agent = factory(tmp_path)()
        agent.model_client = FakeModelClient(
            [
                '<tool>{"name":"write_file","args":{"path":"result.txt","content":"yes"}}</tool>',
                "<final>done</final>",
            ]
        )
        agent.approval_engine.mode = "ask"
        app = RepoAgentApp(agent)
        app.server.confirmation_timeout = 0.5 if decision == "timeout" else 10
        async with app.run_test(size=size) as pilot:
            await until(pilot, lambda: app.ready)
            app.query_one("#composer", TextArea).load_text("write the file")
            await pilot.click("#send")
            await until(pilot, lambda: app.confirmation_id is not None)
            assert app.query_one("#confirmation").display
            assert app.query_one("#new").disabled
            assert "write_file" in str(app.query_one("#prompt", Static).render())
            for selector in ("#approve", "#deny", "#cancel", "#quit"):
                region = app.query_one(selector).region
                assert region.right <= size[0] and region.bottom <= size[1]
            if decision != "timeout":
                await pilot.click(f"#{decision}")
            if decision != "quit":
                await until(pilot, lambda: app.active_turn is None)
                assert not app.query_one("#confirmation").display
        assert app.server.closed
        assert not app.server.confirmations.pending()
        assert (tmp_path / "result.txt").exists() == (decision == "approve")

    asyncio.run(scenario())
