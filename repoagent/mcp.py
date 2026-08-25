"""MCP discovery and Tool Gateway registration contracts."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .tool_contracts import ToolDefinition, ToolEffect
from .tool_execution import ToolRunnerOutput
from .security import NetworkPolicy, NetworkPolicyError


MCP_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class MCPRegistrationError(ValueError):
    pass


@dataclass(frozen=True)
class MCPToolRegistration:
    server: str
    remote_name: str
    local_name: str
    definition_id: str

    def to_dict(self):
        return {
            "server": self.server,
            "remote_name": self.remote_name,
            "local_name": self.local_name,
            "definition_id": self.definition_id,
        }


class MCPManager:
    def __init__(self, servers=None, *, network_policy=None):
        self._servers = dict(servers or {})
        self._network_policy = network_policy or NetworkPolicy()
        self._registrations = ()

    @property
    def registrations(self):
        return self._registrations

    def discover(self, existing_names=()):
        occupied = set(existing_names)
        entries = {}
        registrations = []
        for server_name in sorted(self._servers):
            self._validate_name(server_name, "server")
            client = self._servers[server_name]
            endpoint_url = getattr(client, "endpoint_url", None)
            if endpoint_url:
                try:
                    self._network_policy.validate_url(endpoint_url)
                except NetworkPolicyError as exc:
                    raise MCPRegistrationError(
                        f"MCP server {server_name!r} endpoint denied: {exc}"
                    ) from exc
            if not callable(getattr(client, "list_tools", None)) or not callable(
                getattr(client, "call_tool", None)
            ):
                raise MCPRegistrationError(
                    f"MCP server {server_name!r} must implement list_tools and call_tool"
                )
            specs = client.list_tools()
            if not isinstance(specs, (list, tuple)):
                raise MCPRegistrationError(
                    f"MCP server {server_name!r} list_tools must return a sequence"
                )
            for raw_spec in specs:
                definition, remote_name = self._definition(server_name, raw_spec)
                if definition.name in occupied:
                    raise MCPRegistrationError(
                        f"duplicate MCP tool name: {definition.name}"
                    )
                occupied.add(definition.name)
                entries[definition.name] = {
                    "definition": definition,
                    "run": self._runner(client, server_name, remote_name),
                    "mcp": {"server": server_name, "remote_name": remote_name},
                }
                registrations.append(
                    MCPToolRegistration(
                        server=server_name,
                        remote_name=remote_name,
                        local_name=definition.name,
                        definition_id=definition.definition_id,
                    )
                )
        self._registrations = tuple(registrations)
        return entries

    @staticmethod
    def _validate_name(value, kind):
        if not isinstance(value, str) or not MCP_NAME_PATTERN.fullmatch(value):
            raise MCPRegistrationError(f"invalid MCP {kind} name: {value!r}")

    def _definition(self, server_name, raw_spec):
        if not isinstance(raw_spec, dict):
            raise MCPRegistrationError("MCP tool definition must be an object")
        remote_name = raw_spec.get("name")
        self._validate_name(remote_name, "tool")
        local_name = f"mcp_{server_name}_{remote_name}"
        if len(local_name) > 64:
            raise MCPRegistrationError(f"MCP tool name is too long: {local_name}")
        schema = raw_spec.get("input_schema")
        if not isinstance(schema, dict):
            raise MCPRegistrationError(
                f"MCP tool {remote_name!r} input_schema must be an object"
            )
        schema = dict(schema)
        schema.setdefault("required", [])
        schema.setdefault("additionalProperties", False)
        try:
            effect = ToolEffect(str(raw_spec.get("effect", "external")))
            if effect is ToolEffect.UNKNOWN:
                effect = ToolEffect.EXTERNAL
            definition = ToolDefinition(
                name=local_name,
                description=str(raw_spec.get("description") or remote_name).strip(),
                parameters=schema,
                effect=effect,
                concurrency_safe=bool(raw_spec.get("concurrency_safe", False)),
                requires_approval=bool(raw_spec.get("requires_approval", False)),
                timeout_seconds=float(raw_spec.get("timeout_seconds", 30.0)),
                max_output_chars=int(raw_spec.get("max_output_chars", 4000)),
                requires_isolation=bool(raw_spec.get("requires_isolation", False)),
            )
        except (TypeError, ValueError) as exc:
            raise MCPRegistrationError(
                f"invalid MCP tool definition {server_name}/{remote_name}: {exc}"
            ) from exc
        return definition, remote_name

    @staticmethod
    def _runner(client, server_name, remote_name):
        def run(arguments, control):
            status = control.status()
            if status != "running":
                return ToolRunnerOutput(
                    "MCP call did not start",
                    {
                        "execution_status": status,
                        "mcp_server": server_name,
                        "mcp_tool": remote_name,
                    },
                )
            output = client.call_tool(
                remote_name, dict(arguments), control=control
            )
            if isinstance(output, ToolRunnerOutput):
                metadata = {
                    "mcp_server": server_name,
                    "mcp_tool": remote_name,
                    **dict(output.metadata),
                }
                return ToolRunnerOutput(output.content, metadata)
            return ToolRunnerOutput(
                str(output),
                {
                    "execution_status": control.status(),
                    "mcp_server": server_name,
                    "mcp_tool": remote_name,
                },
            )

        return run


__all__ = ["MCPManager", "MCPRegistrationError", "MCPToolRegistration"]
