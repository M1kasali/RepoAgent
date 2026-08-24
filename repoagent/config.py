"""User-level and project-local configuration helpers."""

import os
import re
from pathlib import Path


ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_PREFIX = "REPOAGENT_"
LEGACY_ENV_PREFIX = "PICO_"


def _strip_quotes(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_env_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export "):].strip()
    if "=" not in line:
        raise ValueError(f"invalid .env line: {line}")
    name, value = line.split("=", 1)
    name = name.strip()
    if not ENV_KEY_PATTERN.match(name):
        raise ValueError(f"invalid .env variable name: {name}")
    return name, _strip_quotes(value)


def find_project_env(start):
    current = Path(start).resolve()
    if current.is_file():
        current = current.parent
    for path in (current, *current.parents):
        env_path = path / ".env"
        if env_path.exists():
            return env_path
    return None


def load_env_file(env_path, override=True):
    env_path = Path(env_path).expanduser()
    if not env_path.is_file():
        return {}
    loaded = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        name, value = parsed
        loaded[name] = value
        if override or name not in os.environ:
            os.environ[name] = value
    return loaded


def user_env_path():
    config_home = os.environ.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "repoagent" / ".env"


def load_user_env(override=False):
    """Load persistent RepoAgent settings without replacing shell variables."""
    return load_env_file(user_env_path(), override=override)


def load_project_env(start, override=True):
    env_path = find_project_env(start)
    if env_path is None:
        return {}
    return load_env_file(env_path, override=override)


def provider_env(name, legacy_names=(), default=""):
    names = [name]
    if name.startswith(ENV_PREFIX):
        names.append(LEGACY_ENV_PREFIX + name[len(ENV_PREFIX):])
    names.extend(legacy_names)
    for env_name in names:
        value = os.environ.get(env_name)
        if value:
            return value
    return default
