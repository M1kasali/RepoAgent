from repoagent.prompt_prefix import build_prompt_prefix, tool_signature
from repoagent.tool_contracts import ToolDefinition, ToolEffect
from repoagent.tools import build_tool_registry
from repoagent.workspace import WorkspaceContext
from repoagent.tokenization import Utf8TokenEstimator


class _Agent:
    depth = 0
    max_depth = 1

    def __init__(self, root):
        self.root = root


def test_tool_signature_is_stable_across_registry_insertion_order(tmp_path):
    def definition(name, description, effect):
        return ToolDefinition(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            effect=effect,
            requires_approval=effect is not ToolEffect.READ,
        )

    tools = {
        "b": {
            "definition": definition("b", "B", ToolEffect.READ),
            "run": object(),
        },
        "a": {
            "definition": definition("a", "A", ToolEffect.EXECUTE),
            "run": object(),
        },
    }
    reordered = {"a": tools["a"], "b": tools["b"]}

    assert tool_signature(tools) == tool_signature(reordered)


def test_build_prompt_prefix_renders_tools_and_workspace_metadata(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(tmp_path)
    tools = build_tool_registry(_Agent(tmp_path))

    prefix = build_prompt_prefix(workspace=workspace, tools=tools, built_at="2026-06-02T00:00:00+08:00")

    assert "You are repoagent" in prefix.text
    assert "Tools:" in prefix.text
    assert "- read_file(" in prefix.text
    assert "Workspace:" in prefix.text
    assert prefix.hash
    assert prefix.workspace_fingerprint == workspace.fingerprint()
    assert prefix.tool_signature == tool_signature(tools)
    assert prefix.built_at == "2026-06-02T00:00:00+08:00"


def test_native_prefix_uses_schema_instead_of_duplicate_text_tool_catalog(tmp_path):
    workspace = WorkspaceContext.build(tmp_path)
    tools = build_tool_registry(_Agent(tmp_path))
    legacy = build_prompt_prefix(workspace, tools, execution_context="Network is disabled.")
    native = build_prompt_prefix(workspace, tools, execution_context="Network is disabled.", native_tools=True)
    assert "Tools:" not in native.text
    assert "<tool>" not in native.text
    assert "compact Agent Harness" in native.text
    assert "Network is disabled." in native.text
    assert "## Workspace" in native.text
    assert "NEVER predict or claim results" in native.text
    assert "Confirm with `ask_user`" in native.text
    assert native.tool_signature == legacy.tool_signature
    assert native.hash != legacy.hash
    assert Utf8TokenEstimator().count(native.text) > 0


def test_runtime_refreshes_prefix_when_native_tool_capability_changes(tmp_path):
    from repoagent import RepoAgent, FakeModelClient, SessionStore

    client = FakeModelClient([])
    agent = RepoAgent(model_client=client, workspace=WorkspaceContext.build(tmp_path),
                      session_store=SessionStore(tmp_path / ".repoagent/sessions"))
    legacy = agent.prefix
    client.supports_native_tools = True
    agent.refresh_prefix()
    assert "<tool>" not in agent.prefix
    client.supports_native_tools = False
    agent.refresh_prefix()
    assert agent.prefix == legacy


def test_native_prefix_hash_tracks_schema_changes_even_without_text_catalog(tmp_path):
    from dataclasses import replace

    workspace = WorkspaceContext.build(tmp_path)
    tools = build_tool_registry(_Agent(tmp_path))
    before = build_prompt_prefix(workspace, tools, native_tools=True)
    tools["read_file"]["definition"] = replace(tools["read_file"]["definition"], description="Updated read semantics")
    after = build_prompt_prefix(workspace, tools, native_tools=True)
    assert after.text == before.text
    assert after.hash != before.hash
    assert after.tool_signature != before.tool_signature


def test_native_capability_is_conservative_across_fallback_chain():
    from types import SimpleNamespace
    from repoagent.providers.fallback import FallbackModelClient

    native = SimpleNamespace(supports_native_tools=True)
    legacy = SimpleNamespace()
    assert FallbackModelClient([native, native]).supports_native_tools
    assert not FallbackModelClient([native, legacy]).supports_native_tools


def test_native_prefix_keeps_schemas_and_call_result_replay(tmp_path):
    from repoagent import RepoAgent, SessionStore
    from repoagent.providers import ModelEvent, ModelResult, ToolCall

    class Provider:
        supports_native_tools = True
        supports_structured_messages = True

        def __init__(self):
            self.requests = []

        def stream(self, request):
            self.requests.append(request)
            result = (ModelResult(tool_calls=(ToolCall("read", "read_file", {"path": "README.md"}),))
                      if len(self.requests) == 1 else ModelResult(text="Inspected."))
            yield ModelEvent(kind="completed", result=result)

    (tmp_path / "README.md").write_text("retained evidence")
    provider = Provider()
    agent = RepoAgent(model_client=provider, workspace=WorkspaceContext.build(tmp_path),
                      session_store=SessionStore(tmp_path / ".repoagent/sessions"), approval_policy="auto",
                      max_steps=4, max_provider_calls=3)
    assert agent.ask("Read README.md") == "Inspected."
    assert all("<tool>" not in request.prompt for request in provider.requests)
    tools = {tool.name: tool for tool in provider.requests[0].tools}
    assert "path" in tools["read_file"].parameters["required"]
    replay = provider.requests[1].messages
    assert any(m.role == "assistant" and m.tool_calls[0].id == "read" for m in replay if m.tool_calls)
    assert any(m.role == "tool" and m.tool_call_id == "read" and "retained evidence" in m.content for m in replay)
    assert provider.requests[0].messages[0].role == "system"
    assert provider.requests[0].messages[-1].role == "user"
    assert provider.requests[1].messages[:2] == provider.requests[0].messages
    assert all("Runtime budget" not in request.prompt for request in provider.requests)
    assert "Runtime budget" not in agent.session["history"][0]["content"]
