import asyncio

import pytest

pytest.importorskip("textual")

from textual.widgets import Input, Select

from repoagent.native_tui import RepoAgentApp
from repoagent.provider_settings import ProviderSettings
from repoagent.tui_settings import ProviderScreen, SessionScreen
from test_native_tui import until
from test_rpc_models import selection
from test_rpc_sessions import factory


async def click(pilot, screen, name):
    screen.query_one(name).scroll_visible(animate=False)
    await pilot.pause()
    assert await pilot.click(name)
    await pilot.pause()


@pytest.mark.parametrize("size", [(100, 36), (60, 24)])
def test_provider_dialog_masks_key_and_edits_models(tmp_path, size):
    async def scenario():
        root = tmp_path / "workspace"
        root.mkdir()
        options = selection()
        options.settings = ProviderSettings(tmp_path / "home" / "providers.json")
        app = RepoAgentApp(factory(root)(), model_selection=options)
        async with app.run_test(size=size) as pilot:
            await until(pilot, lambda: app.ready)
            await pilot.click("#manage-providers")
            await pilot.pause()
            assert isinstance(app.screen, ProviderScreen)
            screen = app.screen
            screen.query_one("#provider-choice", Select).value = "openai"
            await pilot.pause()
            screen.query_one("#provider-key", Input).value = "not-in-screenshot-secret"
            assert "not-in-screenshot-secret" not in app.export_screenshot()
            await click(pilot, screen, "#save-key")
            assert screen.query_one("#provider-key", Input).value == ""
            assert (
                options.settings.entry("openai")["api_key"]
                == "not-in-screenshot-secret"
            )
            screen.query_one("#model-name", Input).value = "added-model"
            await click(pilot, screen, "#add-model")
            assert "added-model" in options.settings.entry("openai")["models"]
            screen.query_one("#model-name", Input).value = "added-model"
            await click(pilot, screen, "#use-model")
            assert app.server.agent.model_client.profile.model == "added-model"
            await click(pilot, screen, "#disconnect")
            assert "api_key" not in options.settings.entry("openai")
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ProviderScreen)
            assert not app.switching
            assert len(app.server._subscriptions) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("size", [(100, 36), (60, 24)])
def test_session_dialog_branch_undo_clear_and_resubscribe(tmp_path, size):
    from test_session_rewrites import seeded

    async def scenario():
        make = factory(tmp_path)
        app = RepoAgentApp(seeded(make), session_factory=make)
        parent = app.server.session_id
        async with app.run_test(size=size) as pilot:
            await until(pilot, lambda: app.ready)
            await pilot.click("#manage-sessions")
            await pilot.pause()
            screen = app.screen
            await click(pilot, screen, "#branch-session")
            assert app.server.session_id != parent
            assert len(app.server.agent.session_store.inspect(parent)["history"]) == 5
            await click(pilot, screen, "#undo-session")
            assert screen.rewrite_confirmation is not None
            await click(pilot, screen, "#clear-session")
            assert len(app.server.agent.session["history"]) == 5
            await click(pilot, screen, "#undo-session")
            await click(pilot, screen, "#undo-session")
            assert len(app.server.agent.session["history"]) == 2
            await click(pilot, screen, "#clear-session")
            await click(pilot, screen, "#clear-session")
            assert app.server.agent.session["history"] == []
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.server._subscriptions) == 1
            assert app.ready and not app.switching
            turn = await app.rpc(
                "turn.send", content="after rewrite", submission_id="fresh"
            )
            await app.server.host.wait(turn["turn_id"])
            assert app.server.agent.session["history"][0]["content"] == "after rewrite"

    asyncio.run(scenario())


@pytest.mark.parametrize("size", [(100, 36), (60, 24)])
def test_session_dialog_rename_export_and_confirmed_delete(tmp_path, size):
    async def scenario():
        make = factory(tmp_path)
        app = RepoAgentApp(make(), session_factory=make)
        old_id = app.server.session_id
        async with app.run_test(size=size) as pilot:
            await until(pilot, lambda: app.ready)
            await pilot.click("#new")
            await until(
                pilot, lambda: app.server.session_id != old_id and not app.switching
            )
            await pilot.click("#manage-sessions")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SessionScreen)
            assert screen.query_one("#delete-session").disabled
            screen.query_one("#session-title", Input).value = "Current work"
            await click(pilot, screen, "#rename-session")
            assert app.server.agent.session["title"] == "Current work"
            await click(pilot, screen, "#export-session")
            assert list(
                (app.server.agent.session_store.root.parent / "exports").glob("*.json")
            )
            screen.query_one("#managed-session", Select).value = old_id
            await pilot.pause()
            await click(pilot, screen, "#delete-session")
            assert screen.delete_revision is not None
            assert (app.server.agent.session_store.root / f"{old_id}.json").exists()
            await click(pilot, screen, "#delete-session")
            assert not (app.server.agent.session_store.root / f"{old_id}.json").exists()
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.server._subscriptions) == 1

    asyncio.run(scenario())
