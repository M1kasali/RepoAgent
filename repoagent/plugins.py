"""Declarative, trust-gated plugins that can only extend ToolGateway."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .tool_contracts import ToolDefinition, ToolEffect


PLUGIN_SCHEMA_VERSION = 1
PLUGIN_TRUST_STATES = frozenset({"untrusted", "approved", "disabled"})


class PluginManifestError(ValueError):
    pass


@dataclass(frozen=True)
class PluginToolSpec:
    name: str
    description: str
    runner_id: str
    effect: ToolEffect
    parameters: Mapping
    concurrency_safe: bool = False
    requires_approval: bool = False
    timeout_seconds: float | None = None
    max_output_chars: int | None = None
    requires_isolation: bool = False

    def __post_init__(self):
        if not self.name or not self.runner_id or not self.description:
            raise PluginManifestError("plugin tool name, runner, and description are required")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    def definition(self):
        optional = {}
        if self.timeout_seconds is not None:
            optional["timeout_seconds"] = self.timeout_seconds
        if self.max_output_chars is not None:
            optional["max_output_chars"] = self.max_output_chars
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            effect=self.effect,
            concurrency_safe=self.concurrency_safe,
            requires_approval=self.requires_approval or self.effect is not ToolEffect.READ,
            requires_isolation=self.requires_isolation,
            **optional,
        )


@dataclass(frozen=True)
class PluginManifest:
    plugin_id: str
    name: str
    version: str
    tools: tuple[PluginToolSpec, ...] = ()
    source: str = ""

    def __post_init__(self):
        if not self.plugin_id or not self.name or not self.version:
            raise PluginManifestError("plugin id, name, and version are required")
        if len({tool.name for tool in self.tools}) != len(self.tools):
            raise PluginManifestError("plugin tool names must be unique")


@dataclass
class PluginRecord:
    manifest: PluginManifest
    trust: str
    state: str = "discovered"
    registered_tools: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self):
        return {
            "plugin_id": self.manifest.plugin_id,
            "name": self.manifest.name,
            "version": self.manifest.version,
            "source": self.manifest.source,
            "trust": self.trust,
            "state": self.state,
            "registered_tools": list(self.registered_tools),
        }


class PluginCatalog:
    def __init__(self, roots, *, trust_store=None):
        self.roots = tuple(Path(root) for root in roots)
        self.trust_store = dict(trust_store or {})

    def discover(self):
        records = []
        seen = set()
        for root in self.roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*/plugin.json")):
                manifest = self._load(path)
                if manifest.plugin_id in seen:
                    raise PluginManifestError(f"duplicate plugin id: {manifest.plugin_id}")
                seen.add(manifest.plugin_id)
                trust = str(self.trust_store.get(manifest.plugin_id, "untrusted"))
                if trust not in PLUGIN_TRUST_STATES:
                    raise PluginManifestError(f"invalid trust state for {manifest.plugin_id}: {trust}")
                records.append(PluginRecord(manifest, trust))
        return records

    @staticmethod
    def _load(path):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != PLUGIN_SCHEMA_VERSION:
            raise PluginManifestError(f"unsupported plugin schema: {path}")
        tools = []
        for item in payload.get("tools", []):
            try:
                effect = ToolEffect(item["effect"])
                tools.append(
                    PluginToolSpec(
                        name=str(item["name"]),
                        description=str(item["description"]),
                        runner_id=str(item["runner"]),
                        effect=effect,
                        parameters=item["parameters"],
                        concurrency_safe=bool(item.get("concurrency_safe", False)),
                        requires_approval=bool(item.get("requires_approval", False)),
                        timeout_seconds=item.get("timeout_seconds"),
                        max_output_chars=item.get("max_output_chars"),
                        requires_isolation=bool(item.get("requires_isolation", False)),
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise PluginManifestError(f"invalid plugin tool in {path}: {exc}") from exc
        return PluginManifest(
            plugin_id=str(payload.get("id", "")),
            name=str(payload.get("name", "")),
            version=str(payload.get("version", "")),
            tools=tuple(tools),
            source=str(path),
        )


class PluginManager:
    """Own plugin lifecycle without loading third-party Python into the host."""

    def __init__(self, catalog, *, runner_catalog=None):
        self.catalog = catalog
        self.runner_catalog = dict(runner_catalog or {})
        self.records = catalog.discover()
        self.started = False

    def register_tools(self, registry):
        result = dict(registry)
        for record in self.records:
            if record.trust != "approved":
                record.state = "disabled" if record.trust == "disabled" else "blocked"
                continue
            registered = []
            for spec in record.manifest.tools:
                if spec.name in result:
                    raise PluginManifestError(f"plugin tool collides with existing tool: {spec.name}")
                runner = self.runner_catalog.get(spec.runner_id)
                if not callable(runner):
                    raise PluginManifestError(
                        f"plugin runner is not host-registered: {spec.runner_id}"
                    )
                result[spec.name] = {"definition": spec.definition(), "run": runner}
                registered.append(spec.name)
            record.registered_tools = tuple(registered)
            record.state = "registered"
        return result

    def start(self):
        self.started = True
        for record in self.records:
            if record.state == "registered":
                record.state = "active"

    def stop(self):
        self.started = False
        for record in self.records:
            if record.state == "active":
                record.state = "stopped"

    def report(self):
        return [record.to_dict() for record in self.records]


__all__ = [
    "PLUGIN_SCHEMA_VERSION",
    "PLUGIN_TRUST_STATES",
    "PluginCatalog",
    "PluginManager",
    "PluginManifest",
    "PluginManifestError",
    "PluginRecord",
    "PluginToolSpec",
]
