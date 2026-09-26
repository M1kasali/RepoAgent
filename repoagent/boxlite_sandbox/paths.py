"""Product-specific data location for the unchanged Pico runtime cache."""

import os
from pathlib import Path

from .. import config


def get_data_dir():
    return Path(
        os.environ.get("REPOAGENT_DATA_DIR", config.user_env_path().parent)
    ).expanduser()


def get_sandbox_dir(backend):
    base = get_data_dir()
    path = base / "sandbox" / backend
    path.mkdir(parents=True, exist_ok=True)
    return path
