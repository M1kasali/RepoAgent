from repoagent.issue_agent.execution_policy import budgeted_request
from repoagent.providers.base import ModelMessage, ModelRequest, ModelTool


def request():
    return ModelRequest(
        prompt="repair", max_output_tokens=4096,
        messages=(ModelMessage(role="user", content="repair"),),
        tools=(ModelTool("read_file", "Read source", {"type": "object"}),),
    )


def test_budget_policy_preserves_normal_work_and_reserves_reporting():
    original = request()
    assert budgeted_request(original, remaining=7) is original
    verifying = budgeted_request(original, remaining=6)
    assert verifying.tools == original.tools
    assert "Do not expand" in verifying.messages[-1].content
    final = budgeted_request(original, remaining=1)
    assert final.tools == ()
    assert final.messages[:-1] == original.messages
    assert "unfinished" in final.messages[-1].content
    assert "not proof" in final.messages[-1].content
    assert original.tools


def test_budget_policy_does_not_change_compaction():
    from dataclasses import replace
    original = replace(request(), call_kind="compaction")
    assert budgeted_request(original, remaining=1) is original


def test_report_only_response_cannot_execute_unadvertised_tool(tmp_path):
    import asyncio
    import pytest
    from repoagent import RepoAgent, SessionStore, WorkspaceContext
    from repoagent.issue_agent.execution_policy import validate_closeout_result
    from repoagent.providers.base import ModelEvent, ModelResult, ToolCall

    class Provider:
        model = "fixture"
        supports_native_tools = supports_structured_messages = True

        def stream(self, original):
            prepared = budgeted_request(original, remaining=1)
            assert not prepared.tools
            result = ModelResult(tool_calls=(ToolCall(
                id="unexpected", name="write_file",
                arguments={"path": "must-not-exist.py", "content": "bad"}),))
            validate_closeout_result(original, result, remaining=1)
            yield ModelEvent(kind="completed", result=result)

    agent = RepoAgent(model_client=Provider(), workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / "sessions"), max_provider_calls=1,
        approval_policy="auto", allowed_tools=["write_file"],
        feature_flags={"skills": False, "memory": False})
    try:
        with pytest.raises(RuntimeError, match="report-only"):
            agent.ask("Report unfinished work.")
        assert not (tmp_path / "must-not-exist.py").exists()
    finally:
        asyncio.run(agent.aclose())


def test_native_loop_can_finish_with_truthful_incomplete_report(tmp_path):
    import asyncio
    from repoagent import RepoAgent, SessionStore, WorkspaceContext
    from repoagent.providers.base import ModelEvent, ModelResult, ToolCall

    class Provider:
        model = "fixture"
        supports_native_tools = supports_structured_messages = True

        def __init__(self):
            self.requests = []

        def stream(self, request):
            prepared = budgeted_request(request, remaining=2 - len(self.requests))
            self.requests.append(prepared)
            if len(self.requests) == 1:
                result = ModelResult(tool_calls=(ToolCall(
                    id="read", name="read_file", arguments={"path": "main.py"}),))
            else:
                assert not prepared.tools
                result = ModelResult(text="Work unfinished; no patch and no test run.")
            yield ModelEvent(kind="completed", result=result)

    (tmp_path / "main.py").write_text("value = 1\n")
    provider = Provider()
    agent = RepoAgent(
        model_client=provider, workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / "sessions"),
        max_provider_calls=2, allowed_tools=["read_file"],
        feature_flags={"skills": False, "memory": False},
    )
    try:
        assert "unfinished" in agent.ask("Inspect main.py")
        assert len(provider.requests) == 2
        assert agent.current_task_state.status == "completed"
        assert (tmp_path / "main.py").read_text() == "value = 1\n"
    finally:
        asyncio.run(agent.aclose())
