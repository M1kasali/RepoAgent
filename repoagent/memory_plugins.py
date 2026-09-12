"""Explicitly selected installed Memory plugins; never download or silently fall back."""

from dataclasses import dataclass
from importlib import metadata
import inspect
import json
from pathlib import Path

from .memory_backend import MemoryBackend


class MemoryPluginError(ValueError):
    pass


@dataclass(frozen=True)
class MemoryServices:
    workspace: Path


def load_memory_backend(name, *, workspace, config_path=None):
    """Factories use ``factory(config=dict, services=MemoryServices)``.

    Only an explicitly selected installed entry point is imported. Importing a
    plugin executes trusted host code, not sandboxed Agent code. A constructed
    backend is not yet started or proven healthy.
    """
    if name == "local":
        if config_path is not None:
            raise MemoryPluginError("memory config requires an external backend")
        return None
    if not isinstance(name, str) or not name.strip():
        raise MemoryPluginError("memory backend name must be non-empty")
    points = tuple(metadata.entry_points(group="repoagent.memory_backends"))
    matches = [point for point in points if point.name == name]
    if len(matches) != 1:
        reason = "unavailable" if not matches else "ambiguous"
        raise MemoryPluginError(
            f"configured memory backend {name!r} is {reason}; install a compatible "
            "trusted plugin or explicitly select --memory-backend local"
        )
    config = {}
    if config_path is not None:
        path = Path(config_path).expanduser()
        if not path.is_absolute():
            path = Path(workspace) / path
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MemoryPluginError(
                "cannot read memory plugin JSON configuration"
            ) from exc
        if not isinstance(config, dict):
            raise MemoryPluginError("memory plugin configuration must be an object")
    try:
        factory = matches[0].load()
        backend = factory(
            config=config, services=MemoryServices(Path(workspace).resolve())
        )
    except Exception as exc:
        raise MemoryPluginError(
            f"memory plugin construction failed ({type(exc).__name__})"
        ) from exc
    if not isinstance(backend, MemoryBackend) or any(
        not inspect.iscoroutinefunction(getattr(backend, method, None))
        for method in ("recall", "store", "feedback", "start", "stop")
    ):
        raise MemoryPluginError(
            "memory plugin must implement all five async backend methods"
        )
    return backend
