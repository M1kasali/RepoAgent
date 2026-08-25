import asyncio
import json
import threading
import time

import pytest

from repoagent import (
    EvaluationCase,
    FakeModelClient,
    Grade,
    ModelEvent,
    ModelResult,
    RepoAgent,
    SessionStore,
    SubagentRoleEvaluator,
    TrialOutput,
    WorkspaceContext,
)
from repoagent.plugins import PluginCatalog, PluginManager, PluginManifestError
from repoagent.routing import (
    DeterministicRouter,
    RouteRequest,
    RoutedModelClient,
    RoutingProfile,
)
from repoagent.subagents import (
    IsolatedSubagentWorkspace,
    ROLE_PROFILES,
    SubagentBudget,
    SubagentMessage,
    SubagentOutcome,
    SubagentRequest,
)


def _agent(tmp_path, outputs, **kwargs):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return RepoAgent(
        model_client=FakeModelClient(outputs),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
        **kwargs,
    )


def _plugin(root, *, plugin_id="demo", runner="host.echo"):
    directory = root / plugin_id
    directory.mkdir(parents=True)
    (directory / "plugin.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": plugin_id,
                "name": "Demo",
                "version": "1.0.0",
                "tools": [
                    {
                        "name": "plugin_echo",
                        "description": "Echo a value through the host runner.",
                        "runner": runner,
                        "effect": "read",
                        "concurrency_safe": True,
                        "parameters": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                            "additionalProperties": False,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_subagent_contracts_attenuate_budget_and_validate_messages():
    parent = SubagentBudget(10, 4000, 1000, 30)
    requested = SubagentBudget(20, 2000, 2000, 60)
    assert requested.attenuate(parent).to_dict() == {
        "max_steps": 10,
        "max_input_tokens": 2000,
        "max_output_tokens": 1000,
        "timeout_seconds": 30,
    }
    message = SubagentMessage("parent", "subagent_x", "request", "inspect", 1)
    request = SubagentRequest.create(
        parent_turn_id="turn",
        parent_request_id="request",
        parent_session_id="session",
        task="inspect",
        role="reviewer",
        budget=parent,
        allowed_tools=("read_file",),
        messages=(message,),
    )
    outcome = SubagentOutcome(request.subagent_id, "completed", answer="done")
    assert outcome.to_dict()["state"] == "completed"
    assert ROLE_PROFILES["red-team-verifier"]["read_only"] is True


def test_isolated_subagent_workspace_does_not_modify_parent_state(tmp_path):
    (tmp_path / "value.txt").write_text("parent\n", encoding="utf-8")
    (tmp_path / ".repoagent").mkdir()
    (tmp_path / ".repoagent" / "secret-state").write_text("state", encoding="utf-8")
    with IsolatedSubagentWorkspace(tmp_path, "subagent_test") as isolated:
        assert not (isolated.root / ".repoagent").exists()
        (isolated.root / "value.txt").write_text("child\n", encoding="utf-8")
        assert isolated.digest.startswith("sha256:")
    assert (tmp_path / "value.txt").read_text() == "parent\n"


def test_delegate_implementer_is_isolated_and_accounted_under_parent_turn(tmp_path):
    agent = _agent(
        tmp_path,
        [
            '<tool>{"name":"delegate","args":{"task":"edit README","max_steps":3,"role":"implementer"}}</tool>',
            '<tool name="patch_file" path="README.md"><old_text>demo</old_text><new_text>child-change</new_text></tool>',
            "<final>Implemented in isolated workspace.</final>",
            "<final>Reviewed child result.</final>",
        ],
    )

    assert agent.ask("delegate implementation") == "Reviewed child result."
    assert (tmp_path / "README.md").read_text() == "demo\n"
    report = agent.run_store.load_report(agent.current_task_state.run_id)
    assert report["subagent_summary"]["count"] == 1
    assert report["subagent_summary"]["completed"] == 1
    record = report["subagents"][0]
    assert record["request"]["role"] == "implementer"
    assert record["request"]["budget"]["max_steps"] <= agent.max_steps
    evidence = agent.current_run_dir / record["outcome"]["evidence"]["path"]
    assert (evidence / "manifest.json").exists()
    trace = agent.run_store.query_trace(agent.current_task_state.run_id)
    assert {row["event"] for row in trace} >= {"subagent_started", "subagent_completed"}


def test_router_is_deterministic_and_routed_client_emits_explainable_trace(tmp_path):
    router = DeterministicRouter(
        (
            RoutingProfile("default", ("fast",), priority=1),
            RoutingProfile(
                "security",
                ("strong", "fast"),
                priority=2,
                roles=("red-team-verifier",),
                keywords=("security",),
            ),
        ),
        default_profile="default",
    )
    decision = router.route(RouteRequest("security review", role="red-team-verifier"))
    assert decision.profile_id == "security"
    assert decision.providers == ("strong", "fast")
    assert "role:red-team-verifier" in decision.reasons
    assert router.route(RouteRequest("ordinary task")).profile_id == "default"

    client = RoutedModelClient(
        {"strong": FakeModelClient(["<final>routed</final>"]), "fast": FakeModelClient([])},
        router,
        role="red-team-verifier",
    )
    agent = RepoAgent(
        model_client=client,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="never",
    )
    assert agent.ask("security review") == "routed"
    routed = next(
        row
        for row in agent.run_store.query_trace(agent.current_task_state.run_id)
        if row["event"] == "model_routed"
    )
    assert routed["routing_decision"]["profile_id"] == "security"
    assert routed["routing_decision"]["fallback_providers"] == ["fast"]


def test_plugin_trust_gateway_capability_and_secret_redaction(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    _plugin(plugins)
    untrusted = PluginManager(
        PluginCatalog((plugins,)), runner_catalog={"host.echo": lambda args, control: args["value"]}
    )
    blocked_agent = _agent(tmp_path / "blocked", (), plugin_manager=untrusted)
    assert "plugin_echo" not in blocked_agent.tools
    assert untrusted.report()[0]["state"] == "blocked"

    secret = "plugin-secret-value"
    monkeypatch.setenv("PLUGIN_TEST_SECRET", secret)
    approved = PluginManager(
        PluginCatalog((plugins,), trust_store={"demo": "approved"}),
        runner_catalog={"host.echo": lambda args, control: args["value"]},
    )
    agent = _agent(
        tmp_path / "approved",
        (),
        plugin_manager=approved,
        secret_env_names=("PLUGIN_TEST_SECRET",),
    )
    result = agent.execute_tool("plugin_echo", {"value": secret})
    assert result.status == "ok"
    assert result.content == "<redacted>"
    assert result.metadata["capability"]["allowed"] is True
    assert approved.report()[0]["state"] == "active"

    bad = PluginManager(
        PluginCatalog((plugins,), trust_store={"demo": "approved"}),
        runner_catalog={},
    )
    with pytest.raises(PluginManifestError, match="host-registered"):
        _agent(tmp_path / "bad", (), plugin_manager=bad)


class _CancellingDelegateProvider:
    def __init__(self):
        self.calls = 0
        self.child_started = threading.Event()
        self.child_cancelled = threading.Event()

    def stream(self, request):
        self.calls += 1
        if self.calls == 1:
            text = (
                '<tool>{"name":"delegate","args":{"task":"wait",'
                '"max_steps":3,"role":"reviewer"}}</tool>'
            )
            yield ModelEvent(kind="text_delta", text=text)
            yield ModelEvent(kind="completed", result=ModelResult(text=text))
            return
        self.child_started.set()
        while not request.cancellation_token.cancelled:
            time.sleep(0.005)
        self.child_cancelled.set()
        request.cancellation_token.raise_if_cancelled(provider="test")


def test_parent_cancellation_propagates_to_active_subagent(tmp_path):
    async def scenario():
        provider = _CancellingDelegateProvider()
        agent = RepoAgent(
            model_client=provider,
            workspace=WorkspaceContext.build(tmp_path),
            session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
            approval_policy="never",
        )
        task = asyncio.create_task(agent.ask_async("delegate and wait"))
        assert await asyncio.to_thread(provider.child_started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(provider.child_cancelled.wait, 2)
        assert agent.last_subagent_records[0]["outcome"]["state"] == "cancelled"
        await agent.aclose()

    asyncio.run(scenario())


def test_role_team_must_complement_single_agent_without_reducing_passes():
    cases = (
        EvaluationCase("edit", "fix", "fixed", "implementation"),
        EvaluationCase("risk", "review", "risk-found", "review"),
    )

    def single_agent(case, repetition):
        return TrialOutput("fixed" if case.task_id == "edit" else "missed")

    def role_team(case, repetition):
        return TrialOutput("fixed" if case.task_id == "edit" else "risk-found")

    def grader(case, output):
        passed = output.text == case.expected
        return Grade(passed, float(passed))

    result = SubagentRoleEvaluator(single_agent, role_team, grader).run(cases)
    assert result["summary"]["wins"] == 1
    assert result["summary"]["losses"] == 0
    assert result["role_gate"]["status"] == "pass"
