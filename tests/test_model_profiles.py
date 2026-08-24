import json
import os
from types import MappingProxyType
from unittest.mock import patch

import pytest

from repoagent import RepoAgent, SessionStore, WorkspaceContext
from repoagent.cli import _resolve_model_profile, build_arg_parser
from repoagent.providers import (
    BUILTIN_MODEL_PROFILES,
    FakeModelClient,
    ModelProfile,
    get_model_profile,
)


def _profile(**overrides):
    values = {
        "name": "test",
        "provider": "openai",
        "protocol": "openai",
        "model": "model-1",
        "base_url": "https://models.example/v1",
        "credential_envs": ("MODEL_API_KEY",),
        "timeout_seconds": 30,
        "max_output_tokens": 1024,
        "temperature": 0.2,
        "top_p": None,
    }
    values.update(overrides)
    return ModelProfile(**values)


def test_builtin_profiles_are_valid_immutable_and_secret_free():
    assert isinstance(BUILTIN_MODEL_PROFILES, MappingProxyType)
    assert tuple(BUILTIN_MODEL_PROFILES) == (
        "ollama",
        "openai",
        "anthropic",
        "deepseek",
    )
    assert get_model_profile("deepseek").protocol == "anthropic"
    assert "api_key" not in get_model_profile("openai").to_dict()
    with pytest.raises(TypeError):
        BUILTIN_MODEL_PROFILES["new"] = _profile()


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"name": "Bad Name"}, "profile name"),
        ({"provider": ""}, "provider"),
        ({"protocol": "unknown"}, "protocol"),
        ({"model": " model-1"}, "model"),
        ({"base_url": "models.example/v1"}, "HTTP"),
        ({"base_url": "https://user:secret@models.example/v1"}, "credentials"),
        ({"base_url": "https://models.example/v1?key=secret"}, "query"),
        ({"timeout_seconds": 0}, "timeout"),
        ({"timeout_seconds": "30"}, "timeout"),
        ({"max_output_tokens": 0}, "max_output_tokens"),
        ({"max_output_tokens": 1.5}, "max_output_tokens"),
        ({"temperature": float("nan")}, "temperature"),
        ({"temperature": 2.1}, "temperature"),
        ({"top_p": 0}, "top_p"),
        ({"credential_envs": ("BAD-NAME",)}, "credential_envs"),
        ({"credential_envs": ("KEY", "KEY")}, "duplicates"),
    ],
)
def test_model_profile_rejects_invalid_configuration(overrides, message):
    with pytest.raises(ValueError, match=message):
        _profile(**overrides)


def test_non_ollama_profile_rejects_silent_top_p_configuration():
    with pytest.raises(ValueError, match="only by ollama"):
        _profile(top_p=0.9)


def test_cli_profile_and_provider_conflict_fails_before_client_construction():
    args = build_arg_parser().parse_args(
        ["--profile", "openai", "--provider", "anthropic"]
    )

    with pytest.raises(ValueError, match="must select the same"):
        _resolve_model_profile(args)


def test_cli_resolves_profile_env_then_validates_overrides():
    args = build_arg_parser().parse_args(
        [
            "--model",
            "custom-model",
            "--base-url",
            "https://gateway.example/v1",
            "--openai-timeout",
            "45",
            "--max-new-tokens",
            "2048",
            "--temperature",
            "0.4",
        ]
    )

    with patch.dict(
        os.environ, {"REPOAGENT_MODEL_PROFILE": "openai"}, clear=True
    ):
        profile = _resolve_model_profile(args)

    assert profile.name == "openai"
    assert profile.model == "custom-model"
    assert profile.base_url == "https://gateway.example/v1"
    assert profile.timeout_seconds == 45
    assert profile.max_output_tokens == 2048
    assert profile.temperature == 0.4


def test_cli_rejects_invalid_profile_url_and_numeric_overrides():
    invalid_url = build_arg_parser().parse_args(
        ["--profile", "openai", "--base-url", "not-a-url"]
    )
    invalid_tokens = build_arg_parser().parse_args(
        ["--profile", "openai", "--max-new-tokens", "0"]
    )

    with pytest.raises(ValueError, match="HTTP"):
        _resolve_model_profile(invalid_url)
    with pytest.raises(ValueError, match="max_output_tokens"):
        _resolve_model_profile(invalid_tokens)


def test_model_profile_provenance_is_traced_without_credential_value(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    client = FakeModelClient(["<final>done</final>"])
    client.profile = _profile()
    agent = RepoAgent(
        model_client=client,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
    )

    with patch.dict(os.environ, {"MODEL_API_KEY": "secret-value"}, clear=False):
        assert agent.ask("finish") == "done"
    events = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state.run_id)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    requested = next(event for event in events if event["event"] == "model_requested")
    assert requested["model_profile"]["name"] == "test"
    assert requested["model_profile"]["credential_envs"] == ["MODEL_API_KEY"]
    assert "secret-value" not in json.dumps(requested)
