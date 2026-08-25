import json

import pytest

from repoagent import (
    FakeModelClient,
    MCPRegistrationError,
    RepoAgent,
    SessionStore,
    WorkspaceContext,
)


class FakeMCPClient:
    def __init__(self, tools, outputs=None):
        self.tools = tools
        self.outputs = dict(outputs or {})
        self.calls = []

    def list_tools(self):
        return self.tools

    def call_tool(self, name, arguments, *, control):
        self.calls.append((name, arguments, control.status()))
        return self.outputs.get(name, f"called:{name}")


def build_agent(tmp_path, client, outputs=()):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return RepoAgent(
        model_client=FakeModelClient(outputs),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
        mcp_servers={"docs": client},
    )


def spec(**overrides):
    value = {
        "name": "lookup",
        "description": "Look up documentation.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        "effect": "read",
        "concurrency_safe": True,
    }
    value.update(overrides)
    return value


def test_mcp_discovery_namespaces_and_executes_through_gateway(tmp_path):
    client = FakeMCPClient([spec()], {"lookup": "documentation result"})
    agent = build_agent(tmp_path, client)

    result = agent.execute_tool(
        "mcp_docs_lookup", {"query": "Tool Gateway"}, call_id="mcp-call-1"
    )

    assert result.status == "ok"
    assert result.content == "documentation result"
    assert result.metadata["mcp_server"] == "docs"
    assert result.metadata["mcp_tool"] == "lookup"
    assert client.calls == [("lookup", {"query": "Tool Gateway"}, "running")]
    assert agent.tools["mcp_docs_lookup"]["definition"].concurrency_safe is True
    assert agent.mcp_manager.registrations[0].local_name == "mcp_docs_lookup"


def test_mcp_tool_is_visible_to_model_and_trace_preserves_origin(tmp_path):
    client = FakeMCPClient([spec()], {"lookup": "remote answer"})
    agent = build_agent(
        tmp_path,
        client,
        (
            '<tool>{"name":"mcp_docs_lookup","args":{"query":"runtime"}}</tool>',
            "<final>done</final>",
        ),
    )

    assert agent.ask("research runtime") == "done"

    trace = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    event = next(item for item in trace if item["event"] == "tool_executed")
    assert event["name"] == "mcp_docs_lookup"
    assert event["tool_request"]["origin"] == "model"
    assert event["tool_result"]["metadata"]["mcp_server"] == "docs"


@pytest.mark.parametrize(
    ("tool_spec", "message"),
    [
        ({"name": "bad name", "input_schema": {}}, "tool name"),
        ({"name": "lookup", "input_schema": "object"}, "input_schema"),
        (spec(effect="write", concurrency_safe=True), "read-effect"),
        (spec(input_schema={"type": "array"}), "schema type"),
    ],
)
def test_mcp_discovery_rejects_invalid_remote_schema(tmp_path, tool_spec, message):
    with pytest.raises(MCPRegistrationError, match=message):
        build_agent(tmp_path, FakeMCPClient([tool_spec]))


def test_mcp_unknown_effect_is_external_serial_and_requires_approval(tmp_path):
    client = FakeMCPClient([spec(effect="unknown", concurrency_safe=False)])
    agent = build_agent(tmp_path, client)
    request = agent.build_tool_request(
        "mcp_docs_lookup", {"query": "x"}, call_id="mcp-unknown"
    )

    batch = agent.tool_gateway.plan_batches((request,))[0]
    result = agent.execute_tool_request(request)

    assert batch.reason == "mutation_conflict_policy"
    assert batch.effects[0].value == "external"
    assert result.metadata["approval"]["required"] is True


def test_mcp_discovery_rejects_private_endpoint_before_listing_tools(tmp_path):
    client = FakeMCPClient([spec()])
    client.endpoint_url = "http://169.254.169.254/latest/meta-data"

    with pytest.raises(MCPRegistrationError, match="endpoint denied"):
        build_agent(tmp_path, client)
