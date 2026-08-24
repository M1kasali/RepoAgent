import os
from unittest.mock import patch

import repoagent.config
from repoagent.config import load_project_env, load_user_env, provider_env, user_env_path


def test_load_user_env_uses_xdg_config_home(tmp_path, monkeypatch):
    config_home = tmp_path / "config"
    env_path = config_home / "repoagent" / ".env"
    env_path.parent.mkdir(parents=True)
    env_path.write_text("REPOAGENT_PROVIDER=openai\n", encoding="utf-8")

    with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=True):
        assert user_env_path() == env_path
        monkeypatch.setattr(repoagent.config, "user_env_path", user_env_path)
        assert load_user_env() == {"REPOAGENT_PROVIDER": "openai"}
        assert os.environ["REPOAGENT_PROVIDER"] == "openai"


def test_project_env_overrides_user_config(tmp_path, monkeypatch):
    config_home = tmp_path / "config"
    user_file = config_home / "repoagent" / ".env"
    user_file.parent.mkdir(parents=True)
    user_file.write_text("REPOAGENT_PROVIDER=deepseek\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("REPOAGENT_PROVIDER=openai\n", encoding="utf-8")

    with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(config_home)}, clear=True):
        monkeypatch.setattr(repoagent.config, "user_env_path", user_env_path)
        load_user_env()
        load_project_env(workspace)
        assert os.environ["REPOAGENT_PROVIDER"] == "openai"


def test_provider_env_supports_legacy_pico_prefix(monkeypatch):
    monkeypatch.delenv("REPOAGENT_PROVIDER", raising=False)
    monkeypatch.setenv("PICO_PROVIDER", "anthropic")

    assert provider_env("REPOAGENT_PROVIDER") == "anthropic"
