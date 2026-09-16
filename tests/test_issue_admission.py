from dataclasses import replace

from repoagent.issue_agent.admission import issue_limits, count_anthropic_request
from repoagent.providers.base import ModelMessage, ModelRequest


def test_structured_request_does_not_count_unused_prompt_copy():
    request = ModelRequest(prompt="copy" * 40000, max_output_tokens=4096,
                           messages=(ModelMessage(role="user", content="actual input"),))
    assert count_anthropic_request(request, model="deepseek-flash") == count_anthropic_request(
        replace(request, prompt="unused"), model="deepseek-flash")


def test_prompt_only_request_is_still_counted():
    short = ModelRequest(prompt="short", max_output_tokens=4096)
    assert count_anthropic_request(replace(short, prompt="long" * 1000), model="deepseek-flash") > count_anthropic_request(short, model="deepseek-flash")


def test_advertised_call_count_fits_unchanged_cost_ceiling():
    from decimal import Decimal
    limits = issue_limits()
    per_call = (Decimal(limits.max_input_tokens) * Decimal("0.3") +
                Decimal(limits.max_output_tokens) * Decimal("1.2")) / 1000000
    assert limits.max_estimated_cost_usd == 1
    assert limits.max_calls == 23
    assert per_call * limits.max_calls <= Decimal("1")
    assert per_call * (limits.max_calls + 1) > Decimal("1")


def test_oversize_tool_history_fits_without_dropping_call_pairs():
    from repoagent.issue_agent.admission import fit_issue_request
    from repoagent.providers.base import ToolCall
    messages = [ModelMessage(role="system", content="keep instructions")]
    for index in range(6):
        call = ToolCall(str(index), "read_file", {"path": "a.py"})
        messages += [ModelMessage(role="assistant", tool_calls=(call,)),
                     ModelMessage(role="tool", tool_call_id=str(index), name="read_file", content="x" * 3000)]
    request = ModelRequest(prompt="fallback", max_output_tokens=4096, messages=tuple(messages))
    fitted, report = fit_issue_request(request, model="deepseek-flash", input_limit=14000)
    assert report["elided_tool_results"] == 3
    assert report["after_input_bound"] <= 14000 < report["before_input_bound"]
    assert len(fitted.messages) == len(request.messages)
    assert fitted.messages[0] == request.messages[0]
    assert fitted.messages[-6:] == request.messages[-6:]
    assert request.messages[2].content == "x" * 3000


def test_input_without_elidable_history_remains_denied():
    import pytest
    from repoagent.issue_agent.admission import fit_issue_request
    from repoagent.evolver.model_budget import EvaluationBudgetError
    request = ModelRequest(prompt="x" * 5000, max_output_tokens=4096)
    with pytest.raises(EvaluationBudgetError, match="input_limit"):
        fit_issue_request(request, model="deepseek-flash", input_limit=1000)


def test_counter_matches_actual_provider_wire_payload(monkeypatch):
    import pytest
    import urllib.request
    from repoagent.providers.clients import AnthropicCompatibleModelClient
    from repoagent.providers.base import ModelTool

    class Captured(Exception):
        pass

    data = []
    def capture(request, **kwargs):
        data.append(request.data)
        raise Captured()

    monkeypatch.setattr(urllib.request, "urlopen", capture)
    request = ModelRequest(prompt="unused duplicate", max_output_tokens=4096,
                           messages=(ModelMessage(role="user", content="quotes: \" and unicode: \u4e2d"),),
                           tools=(ModelTool("read", "read a file", {"type": "object"}),))
    leaf = AnthropicCompatibleModelClient("deepseek-flash", "https://example.invalid", "fixture", 0.2, 30)
    with pytest.raises(Captured):
        list(leaf.stream(request))
    assert count_anthropic_request(request, model=leaf.model, temperature=leaf.temperature) == len(data[0]) + 512
