import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
import stat

import pytest

from repoagent import FakeModelClient
from repoagent.atomic_io import StorageCorruptionError
from repoagent.cli import _client_from_profile, _resolve_model_profile, build_arg_parser
from repoagent.model_selection import ModelSelection
from repoagent.provider_settings import ProviderSettings
from repoagent.providers import get_model_profile
from repoagent.tui_rpc import RpcError, TUIRPCServer
from test_rpc_sessions import factory


def test_settings_atomic_merge_permissions_validation_and_corruption(tmp_path):
    store = ProviderSettings(tmp_path / "config" / "providers.json")
    store.update(
        "openai", "save_key", api_key="private-key", api_base="https://example.test/v1"
    )
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                lambda i: store.update("openai", "add_model", model=f"model-{i}"),
                range(12),
            )
        )
    assert len(store.entry("openai")["models"]) == 12
    if os.name != "nt":
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    before = store.path.read_bytes()
    for params in (
        {"api_key": "bad\nkey"},
        {"api_key": "key", "api_base": "https://user:pass@example.test"},
    ):
        with pytest.raises(ValueError):
            store.update("openai", "save_key", **params)
        assert store.path.read_bytes() == before
    store.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(StorageCorruptionError, match="invalid provider"):
        store.update("openai", "disconnect")
    assert store.path.read_text() == "{broken"


def test_settings_reject_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        ProviderSettings(tmp_path / "link" / "providers.json").update(
            "openai", "save_key", api_key="private-key"
        )


def test_cli_uses_saved_credentials_with_environment_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    profile = get_model_profile("openai")
    for key in (
        *profile.credential_envs,
        "PICO_OPENAI_API_KEY",
        "REPOAGENT_OPENAI_API_BASE",
        "OPENAI_API_BASE",
        "PICO_OPENAI_API_BASE",
    ):
        monkeypatch.delenv(key, raising=False)
    store = ProviderSettings()
    store.update(
        "openai",
        "save_key",
        api_key="stored-private-key",
        api_base="https://saved.example/v1",
    )
    selected = _resolve_model_profile(
        build_arg_parser().parse_args(["--profile", "openai"])
    )
    assert selected.base_url == "https://saved.example/v1"
    assert _client_from_profile(selected).api_key == "stored-private-key"
    monkeypatch.setenv("REPOAGENT_OPENAI_API_KEY", "environment-private-key")
    assert _client_from_profile(selected).api_key == "environment-private-key"
    store.update("openai", "disconnect")
    assert _client_from_profile(selected).api_key == "environment-private-key"


def test_rpc_provider_management_redaction_and_next_selection(tmp_path):
    async def scenario():
        root = tmp_path / "workspace"
        root.mkdir()
        store = ProviderSettings(tmp_path / "home" / "providers.json")
        profile = get_model_profile("openai")

        def make(profile):
            client = FakeModelClient(["<final>done</final>"])
            client.profile = profile
            client.api_key = store.entry("openai").get("api_key", "")
            return client

        choices = ModelSelection({"openai": profile}, make, settings=store)
        agent = factory(root)()
        original = agent.model_client
        frames = []

        async def send(frame):
            frames.append(frame)

        server = TUIRPCServer(agent, send, model_selection=choices)
        await server.start()
        try:
            saved = await server.dispatch(
                "model.save_key", {"slug": "openai", "api_key": "stored-private-key"}
            )
            assert saved["active_client_unchanged"] and agent.model_client is original
            assert "stored-private-key" not in json.dumps(saved)
            assert "stored-private-key" not in agent.redact_text("stored-private-key")
            assert any(
                value == "stored-private-key"
                for _, value in agent.detected_secret_env_items()
            )
            await server.dispatch(
                "model.add_model", {"slug": "openai", "model": "custom-model"}
            )
            await server.dispatch(
                "model.select", {"profile": "openai", "model": "custom-model"}
            )
            assert agent.model_client.api_key == "stored-private-key"
            assert agent.model_client.profile.model == "custom-model"
            with pytest.raises(RpcError):
                await server.dispatch(
                    "model.remove_model", {"slug": "openai", "model": "custom-model"}
                )
            await server.dispatch("model.disconnect", {"slug": "openai"})
            assert agent.model_client.api_key == "stored-private-key"
            await server.dispatch("model.select", {"profile": "openai"})
            assert agent.model_client.api_key == ""
            await server.dispatch(
                "model.remove_model", {"slug": "openai", "model": "custom-model"}
            )
            assert (
                "custom-model"
                not in (await server.dispatch("model.options", {}))["profiles"][0][
                    "models"
                ]
            )
            assert (
                "stored-private-key"
                not in agent.redact_artifact({"text": "stored-private-key"})["text"]
            )
        finally:
            await server.close()

    asyncio.run(scenario())


def test_registered_credential_redacts_split_stream_and_history(tmp_path):
    from repoagent.providers.base import ModelEvent

    async def scenario():
        class Client(FakeModelClient):
            def stream(self, request):
                result = self.generate(request)
                for part in ("<final>stored-", "private-key", " done</final>"):
                    yield ModelEvent(kind="text_delta", text=part)
                yield ModelEvent(kind="completed", result=result)

        agent = factory(tmp_path)()
        agent.model_client = Client(["<final>stored-private-key done</final>"])
        agent.register_secret("stored-private-key")
        frames = []

        async def send(frame):
            frames.append(frame)

        server = TUIRPCServer(agent, send)
        await server.start()
        try:
            await server.dispatch("turn.subscribe", {"stream": True})
            turn = await server.dispatch(
                "turn.send", {"content": "show result", "submission_id": "one"}
            )
            await server.host.wait(turn["turn_id"])
            assert "stored-private-key" not in json.dumps(frames)
            events = agent.run_store.load_turn_events(turn["turn_id"])
            assert "stored-private-key" not in json.dumps(events)
            assert "stored-private-key" not in json.dumps(agent.session["history"])
            assert "<redacted>" in json.dumps(frames)
        finally:
            await server.close()

    asyncio.run(scenario())


def test_config_write_fences_new_turn_until_disk_update_finishes(tmp_path):
    import threading

    async def scenario():
        root = tmp_path / "workspace"
        root.mkdir()
        from test_rpc_models import selection

        choices = selection()
        choices.settings = ProviderSettings(tmp_path / "home" / "providers.json")
        entered, release = threading.Event(), threading.Event()
        original = choices.settings.update

        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test did not release writer")
            return original(*args, **kwargs)

        choices.settings.update = delayed

        async def send(frame):
            pass

        server = TUIRPCServer(factory(root)(), send, model_selection=choices)
        await server.start()
        pending = asyncio.create_task(
            server.dispatch(
                "model.add_model", {"slug": "openai", "model": "another-model"}
            )
        )
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            with pytest.raises(RpcError, match="update in progress"):
                await server.dispatch(
                    "turn.send", {"content": "no", "submission_id": "one"}
                )
            pending.cancel()
            await asyncio.sleep(0)
            assert server._configuring
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert "another-model" in choices.settings.entry("openai")["models"]
        finally:
            release.set()
            await asyncio.gather(pending, return_exceptions=True)
            await server.close()

    asyncio.run(scenario())
