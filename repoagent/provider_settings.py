"""User-owned provider settings; the only writer for this JSON document."""

import json
import os
from pathlib import Path

from .atomic_io import StorageCorruptionError, atomic_replace_unlocked, file_lock
from .config import user_env_path
from .providers.profiles import get_model_profile


class ProviderSettings:
    def __init__(self, path=None):
        self.path = (
            Path(path)
            if path is not None
            else user_env_path().with_name("providers.json")
        )

    def _safe_path(self):
        if any(path.is_symlink() for path in (self.path, *self.path.parents)):
            raise ValueError("provider settings path must not contain symlinks")

    @staticmethod
    def _validate_entry(name, entry):
        profile = get_model_profile(name)
        if not isinstance(entry, dict) or set(entry) - {
            "api_key",
            "api_base",
            "models",
        }:
            raise ValueError("invalid provider settings fields")
        key = entry.get("api_key", "")
        if key and not profile.credential_envs:
            raise ValueError("this provider does not use API-key authentication")
        if (
            not isinstance(key, str)
            or len(key) > 8192
            or any(ord(ch) < 32 for ch in key)
        ):
            raise ValueError("invalid provider credential")
        if "api_base" in entry:
            profile.with_overrides(base_url=entry["api_base"])
        models = entry.get("models", [])
        if not isinstance(models, list) or len(models) > 100:
            raise ValueError("invalid model list")
        for model in models:
            ProviderSettings.validate_model(model)
        if len(models) != len(set(models)):
            raise ValueError("duplicate model names")

    @staticmethod
    def validate_model(model):
        if (
            not isinstance(model, str)
            or not model
            or model != model.strip()
            or len(model) > 200
            or any(ord(ch) < 32 for ch in model)
        ):
            raise ValueError("invalid model name")

    def read(self):
        self._safe_path()
        if not self.path.exists():
            return {"version": 1, "providers": {}}
        if self.path.stat().st_size > 1024 * 1024:
            raise StorageCorruptionError("provider settings exceed size limit")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or type(data.get("version")) is not int
                or data.get("version") != 1
                or set(data) != {"version", "providers"}
                or not isinstance(data["providers"], dict)
            ):
                raise ValueError()
            for name, entry in data["providers"].items():
                self._validate_entry(name, entry)
            return data
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise StorageCorruptionError("invalid provider settings document") from None

    def entry(self, name):
        get_model_profile(name)
        return self.read()["providers"].get(name, {})

    def update(self, name, operation, *, api_key=None, api_base=None, model=None):
        get_model_profile(name)
        self._safe_path()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = self.path.with_suffix(".lock")
        if lock.is_symlink():
            raise ValueError("provider settings lock must not be a symlink")
        with file_lock(lock):
            data = self.read()
            entry = dict(data["providers"].get(name, {}))
            if operation == "save_key":
                if not isinstance(api_key, str) or not api_key.strip():
                    raise ValueError("a non-empty API key is required")
                entry["api_key"] = api_key
                if api_base is not None:
                    entry["api_base"] = api_base
            elif operation == "disconnect":
                entry.pop("api_key", None)
                entry.pop("api_base", None)
            elif operation in {"add_model", "remove_model"}:
                self.validate_model(model)
                models = list(entry.get("models", []))
                if operation == "add_model" and model not in models:
                    models.append(model)
                elif operation == "remove_model" and model in models:
                    models.remove(model)
                entry["models"] = models
            else:
                raise ValueError("unknown provider operation")
            self._validate_entry(name, entry)
            data["providers"][name] = entry
            atomic_replace_unlocked(
                self.path, json.dumps(data, sort_keys=True, ensure_ascii=True) + "\n"
            )
            if os.name != "nt":
                os.chmod(self.path, 0o600)
        return {"saved": True}
