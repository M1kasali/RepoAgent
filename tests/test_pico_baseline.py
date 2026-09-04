import asyncio
import sys
from types import ModuleType, SimpleNamespace

from repoagent.evaluation.pico_baseline import (
    RepoAgentProviderBridge,
    _detach_pico_call_efficiency,
    _stop_pico_skill_watcher,
    pico_messages_to_model_messages,
    pico_tools_to_model_tools,
)
from repoagent.pricing import ModelPricing
from repoagent.providers import (
    ModelResult,
    ModelUsage,
    ToolCall,
    UsageSource,
)


def _install_fake_pico_provider(monkeypatch):
    pico = ModuleType("pico")
    providers = ModuleType("pico.providers")
    base = ModuleType("pico.providers.base")

    class ErrorClassification:
        def __init__(
            self,
            category,
            retryable=False,
            should_fallback=False,
            should_compress=False,
        ):
            self.category = category
            self.retryable = retryable
            self.should_fallback = should_fallback
            self.should_compress = should_compress

    class LLMResponse:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.tool_calls = kwargs.get("tool_calls", [])

    class ToolCallRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    base.ErrorClassification = ErrorClassification
    base.LLMResponse = LLMResponse
    base.ToolCallRequest = ToolCallRequest
    monkeypatch.setitem(sys.modules, "pico", pico)
    monkeypatch.setitem(sys.modules, "pico.providers", providers)
    monkeypatch.setitem(sys.modules, "pico.providers.base", base)


def test_pico_message_and_tool_projection_preserves_structured_history():
    messages = pico_messages_to_model_messages(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": [{"type": "text", "text": "task"}]},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": '{"path":"answer.py","content":"ok"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "write_file",
                "content": "written",
            },
        ]
    )
    tools = pico_tools_to_model_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "write",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                },
            }
        ]
    )

    assert [message.role for message in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]
    assert messages[2].tool_calls[0].arguments == {
        "path": "answer.py",
        "content": "ok",
    }
    assert messages[3].tool_call_id == "call-1"
    assert tools[0].name == "write_file"
    assert tools[0].parameters["type"] == "object"


def test_pico_message_projection_preserves_malformed_arguments_for_tool_validation():
    messages = pico_messages_to_model_messages(
        [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {
                            "name": "write_file",
                            "arguments": "not-json",
                        },
                    }
                ],
            }
        ]
    )

    assert messages[0].tool_calls[0].arguments == {"_raw_arguments": "not-json"}


def test_pico_call_efficiency_is_detached_from_async_thread_shutdown():
    controller = object()
    assembly = SimpleNamespace(call_efficiency=controller)

    detached = _detach_pico_call_efficiency(assembly)

    assert detached is controller
    assert assembly.call_efficiency is None


def test_pico_skill_watcher_is_stopped_during_adapter_cleanup():
    class Skills:
        stopped = False

        def stop_file_watcher(self):
            self.stopped = True

    skills = Skills()
    assembly = SimpleNamespace(
        agent_loop=SimpleNamespace(context=SimpleNamespace(skills=skills))
    )

    _stop_pico_skill_watcher(assembly)

    assert skills.stopped is True


def test_pico_provider_bridge_uses_repoagent_transport_and_enforces_hard_call_cap(
    monkeypatch,
):
    _install_fake_pico_provider(monkeypatch)

    class Client:
        profile = SimpleNamespace(
            provider="deepseek",
            protocol="anthropic",
            model="deepseek-v4-flash",
            temperature=0.2,
            timeout_seconds=60,
            pricing=ModelPricing(0.28, 0.56, "test"),
        )

        def __init__(self):
            self.requests = []

        def generate(self, request):
            self.requests.append(request)
            return ModelResult(
                tool_calls=(ToolCall("call-1", "read_file", {"path": "x"}),),
                finish_reason="tool_use",
                usage=ModelUsage(
                    input_tokens=10,
                    output_tokens=5,
                    total_tokens=15,
                    source=UsageSource.ACTUAL,
                ),
                provider="deepseek",
                model="deepseek-v4-flash",
                latency_ms=7,
            )

    client = Client()
    bridge = RepoAgentProviderBridge(client, max_calls=1, max_output_tokens=128)
    first = asyncio.run(
        bridge.chat(
            messages=[{"role": "user", "content": "inspect"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "read",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
    )
    second = asyncio.run(
        bridge.chat(messages=[{"role": "user", "content": "again"}])
    )

    assert first.tool_calls[0].name == "read_file"
    assert client.requests[0].messages[0].content == "inspect"
    assert client.requests[0].max_output_tokens == 128
    assert bridge.entries[0].estimated_cost_usd == 5.6e-06
    assert second.finish_reason == "error"
    assert second.error_classification.category == "budget_exhausted"
    assert bridge.budget_exhausted is True
    assert len(client.requests) == 1
    assert len(bridge.entries) == 1
