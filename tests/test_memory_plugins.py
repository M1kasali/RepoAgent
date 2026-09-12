import asyncio
from types import SimpleNamespace

import pytest

from repoagent.cli import build_arg_parser
from repoagent.memory_backend import InMemoryMemoryBackend
from repoagent.memory_plugins import MemoryPluginError, load_memory_backend
from test_skills import build_agent


def install(monkeypatch, factory, name="fixture"):
    point = SimpleNamespace(name=name, load=lambda: factory)
    monkeypatch.setattr(
        "repoagent.memory_plugins.metadata.entry_points", lambda **kwargs: [point]
    )


def test_local_does_not_discover_or_import_plugins(monkeypatch, tmp_path):
    def forbidden(**kwargs):
        pytest.fail("local mode must not discover plugins")

    monkeypatch.setattr("repoagent.memory_plugins.metadata.entry_points", forbidden)
    assert load_memory_backend("local", workspace=tmp_path) is None
    with pytest.raises(MemoryPluginError):
        load_memory_backend("local", workspace=tmp_path, config_path="config.json")


def test_missing_and_ambiguous_plugin_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "repoagent.memory_plugins.metadata.entry_points", lambda **kwargs: []
    )
    with pytest.raises(MemoryPluginError, match="unavailable"):
        load_memory_backend("myna", workspace=tmp_path)
    point = SimpleNamespace(name="duplicate")
    monkeypatch.setattr(
        "repoagent.memory_plugins.metadata.entry_points",
        lambda **kwargs: [point, point],
    )
    with pytest.raises(MemoryPluginError, match="ambiguous"):
        load_memory_backend("duplicate", workspace=tmp_path)


def test_factory_receives_workspace_and_config_without_starting(monkeypatch, tmp_path):
    received = {}
    backend = InMemoryMemoryBackend()

    def factory(**kwargs):
        received.update(kwargs)
        return backend

    install(monkeypatch, factory)
    (tmp_path / "config.json").write_text('{"namespace": "repo"}')
    assert (
        load_memory_backend("fixture", workspace=tmp_path, config_path="config.json")
        is backend
    )
    assert received["config"] == {"namespace": "repo"}
    assert received["services"].workspace == tmp_path.resolve()
    assert not backend.started


@pytest.mark.parametrize("payload", ["[]", "null", "not json"])
def test_invalid_configuration_is_rejected_before_factory(
    monkeypatch, tmp_path, payload
):
    install(monkeypatch, lambda **kwargs: pytest.fail("factory must not execute"))
    (tmp_path / "config.json").write_text(payload)
    with pytest.raises(MemoryPluginError):
        load_memory_backend("fixture", workspace=tmp_path, config_path="config.json")


def test_incomplete_and_sync_backends_are_rejected(monkeypatch, tmp_path):
    install(monkeypatch, lambda **kwargs: object())
    with pytest.raises(MemoryPluginError, match="five async"):
        load_memory_backend("fixture", workspace=tmp_path)
    backend = InMemoryMemoryBackend()
    backend.start = lambda: None
    install(monkeypatch, lambda **kwargs: backend)
    with pytest.raises(MemoryPluginError, match="five async"):
        load_memory_backend("fixture", workspace=tmp_path)


def test_plugin_runtime_uses_user_track_stores_and_recalls_next_turn(
    monkeypatch, tmp_path
):
    class UserOnlyBackend(InMemoryMemoryBackend):
        async def recall(self, query, *, user_id=None, agent_id=None, top_k):
            assert user_id and agent_id is None
            return await super().recall(query, user_id=user_id, top_k=top_k)

    backend = UserOnlyBackend()
    install(monkeypatch, lambda **kwargs: backend)
    loaded = load_memory_backend("fixture", workspace=tmp_path)
    agent = build_agent(
        tmp_path,
        ["<final>Remembered.</final>", "<final>Blue.</final>"],
        memory_backend=loaded,
    )
    agent.ask("The deploy key is blue")
    agent.ask("deploy key?")
    assert agent.last_memory_backend_metadata["recalled_count"] == 1
    assert "The deploy key is blue" in agent.model_client.prompts[1]
    assert agent.last_memory_backend_metadata["store_status"] == "completed"
    assert not backend.started


def test_failed_start_cleans_up_and_preserves_original_error(tmp_path):
    class BrokenBackend(InMemoryMemoryBackend):
        stopped = 0

        async def start(self):
            raise ValueError("startup failure")

        async def stop(self):
            self.stopped += 1
            raise RuntimeError("cleanup failure")

    backend = BrokenBackend()
    agent = build_agent(tmp_path, [], memory_backend=backend)
    with pytest.raises(ValueError, match="startup failure"):
        asyncio.run(agent.ask_async("hello"))
    assert backend.stopped == 1
    assert not agent._memory_backend_started


def test_cli_memory_selection_is_explicit():
    parser = build_arg_parser()
    assert parser.parse_args([]).memory_backend == "local"
    args = parser.parse_args(
        ["--memory-backend", "fixture", "--memory-config", "memory.json"]
    )
    assert args.memory_backend == "fixture" and args.memory_config == "memory.json"


def test_runtime_assembly_loads_selected_plugin(monkeypatch, tmp_path):
    from repoagent import FakeModelClient
    from repoagent.runtime_assembly import RuntimeAssembly

    backend = InMemoryMemoryBackend()
    install(monkeypatch, lambda **kwargs: backend)
    args = build_arg_parser().parse_args(
        ["--cwd", str(tmp_path), "--memory-backend", "fixture"]
    )
    client = FakeModelClient([])
    client.profile = SimpleNamespace(
        max_output_tokens=64, context_window_tokens=4096, context_window_source="test"
    )
    agent = RuntimeAssembly.from_arguments(
        args,
        model_client_factory=lambda args: client,
        secret_names_factory=lambda args: (),
    ).build()
    assert agent.memory_backend is backend
    assert not backend.started


def test_falsey_external_backend_is_not_silently_replaced(tmp_path):
    class Backend(InMemoryMemoryBackend):
        def __bool__(self):
            return False

    backend = Backend()
    assert build_agent(tmp_path, [], memory_backend=backend).memory_backend is backend
