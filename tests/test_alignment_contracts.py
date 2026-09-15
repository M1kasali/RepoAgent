import json
import subprocess
from unittest.mock import patch

import pytest

from repoagent import FakeModelClient, RepoAgent, SessionStore
from repoagent.providers.base import ModelEvent, ModelMessage, ModelRequest, ModelResult, ToolCall
from repoagent.providers.clients import AnthropicCompatibleModelClient, OpenAICompatibleModelClient
from repoagent.workspace import WorkspaceContext


@pytest.mark.parametrize("explicit", [False, True])
def test_workspace_refresh_preserves_root_policy(tmp_path, explicit):
    parent = tmp_path / "parent"
    parent.mkdir()
    subprocess.run(["git", "init", "-q", str(parent)], check=True)
    (parent / "README.md").write_text("PARENT_ONLY_SENTINEL")
    child = parent / "task"
    child.mkdir()
    agent = RepoAgent(
        model_client=FakeModelClient([]),
        workspace=WorkspaceContext.build(child, repo_root_override=child if explicit else None),
        session_store=SessionStore(tmp_path / "sessions"),
    )
    for force in (False, True, False):
        agent.refresh_prefix(force=force)
        assert agent.workspace.repo_root == str(child if explicit else parent)
        assert ("PARENT_ONLY_SENTINEL" in agent.prefix) is (not explicit)
        if explicit:
            assert agent.workspace.status == "clean"


@pytest.mark.parametrize("body", ["plain answer", "<final>answer</final>\nLIMA", '<tool>{"name":"read_file"}</tool>', "<think>private</think>visible"])
def test_native_loop_preserves_answer_body_without_legacy_parsing(tmp_path, body):
    class Provider:
        supports_native_tools = True
        supports_structured_messages = True

        def stream(self, request):
            assert "<final>" not in request.prompt
            yield ModelEvent(kind="text_delta", text=body)
            yield ModelEvent(kind="completed", result=ModelResult(text=body))

    agent = RepoAgent(model_client=Provider(), workspace=WorkspaceContext.build(tmp_path),
                      session_store=SessionStore(tmp_path / "sessions"), max_provider_calls=2)
    expected = "visible" if body.startswith("<think>") else body
    assert agent.ask("Answer without tools") == expected


@pytest.mark.parametrize("client_type", [OpenAICompatibleModelClient, AnthropicCompatibleModelClient])
def test_sync_provider_serializes_structured_tool_history(client_type):
    client = client_type("test-model", "https://example.invalid/v1", "test-key", 0, 30)
    messages = (
        ModelMessage(role="system", content="SYSTEM_SENTINEL"),
        ModelMessage(role="user", content="QUESTION_SENTINEL"),
        ModelMessage(role="assistant", tool_calls=(ToolCall("call-1", "lookup_record", {}),)),
        ModelMessage(role="tool", content="RESULT_SENTINEL", tool_call_id="call-1", name="lookup_record"),
    )

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps({"output": [], "content": [{"type": "text", "text": "ok"}]}).encode()

    with patch("urllib.request.urlopen", return_value=Response()) as transport:
        client.generate(ModelRequest(prompt="LEGACY_FALLBACK", max_output_tokens=8, messages=messages))
    payload = json.loads(transport.call_args.args[0].data)
    serialized = json.dumps(payload)
    for sentinel in ("SYSTEM_SENTINEL", "QUESTION_SENTINEL", "RESULT_SENTINEL", "call-1", "lookup_record"):
        assert sentinel in serialized
    assert "LEGACY_FALLBACK" not in serialized
    assert payload["stream"] is False
    if client_type is OpenAICompatibleModelClient:
        assert [item.get("type") for item in payload["input"]][-2:] == ["function_call", "function_call_output"]
    else:
        assert payload["messages"][-2]["content"][0]["type"] == "tool_use"
        assert payload["messages"][-1]["content"][0]["type"] == "tool_result"


def test_native_exhaustion_synthesis_preserves_literal_final_tags(tmp_path):
    class Provider:
        supports_native_tools = True
        supports_structured_messages = True

        def stream(self, request):
            if request.tools:
                result = ModelResult(tool_calls=(ToolCall("read", "read_file", {"path": "README.md"}),))
            else:
                result = ModelResult(text="<think>private</think><final>literal</final>\nsummary")
            yield ModelEvent(kind="completed", result=result)

    (tmp_path / "README.md").write_text("evidence")
    agent = RepoAgent(model_client=Provider(), workspace=WorkspaceContext.build(tmp_path),
                      session_store=SessionStore(tmp_path / "sessions"), max_steps=1,
                      max_provider_calls=3, approval_policy="auto")
    assert agent.ask("Read README then summarize") == "<final>literal</final>\nsummary"


def test_native_output_events_exclude_thinking_and_tool_preamble(tmp_path):
    class Provider:
        supports_native_tools = True
        supports_structured_messages = True
        count = 0

        def stream(self, request):
            self.count += 1
            if self.count == 1:
                body = "<think>private</think>I will read."
                result = ModelResult(text=body, tool_calls=(ToolCall("read", "read_file", {"path": "README.md"}),))
            else:
                body = "<think>private</think><final>literal</final>\nsummary"
                result = ModelResult(text=body)
            for part in (body[:5], body[5:]):
                yield ModelEvent(kind="text_delta", text=part)
            yield ModelEvent(kind="completed", result=result)

    (tmp_path / "README.md").write_text("evidence")
    agent = RepoAgent(model_client=Provider(), workspace=WorkspaceContext.build(tmp_path),
                      session_store=SessionStore(tmp_path / "sessions"), max_steps=3,
                      max_provider_calls=3, approval_policy="auto")
    expected = "<final>literal</final>\nsummary"
    assert agent.ask("Read README then summarize") == expected
    events = agent.run_store.load_turn_events(agent.current_task_state.run_id)
    assert "".join(event["payload"]["content"] for event in events if event["kind"] == "runner.text") == expected


def test_legacy_answer_protocol_remains_compatible(tmp_path):
    agent = RepoAgent(model_client=FakeModelClient(["<final>legacy</final>"]),
                      workspace=WorkspaceContext.build(tmp_path),
                      session_store=SessionStore(tmp_path / "sessions"))
    assert agent.ask("Answer") == "legacy"
