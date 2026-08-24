import pytest

import repoagent.config


@pytest.fixture(autouse=True)
def isolate_user_config(monkeypatch, tmp_path):
    """Keep developer machine configuration out of the test suite."""
    env_path = tmp_path / "xdg-config" / "repoagent" / ".env"
    monkeypatch.setattr(repoagent.config, "user_env_path", lambda: env_path)
