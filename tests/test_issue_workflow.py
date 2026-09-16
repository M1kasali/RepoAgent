import subprocess

import pytest

from repoagent.issue_agent.cases import CaseStore, bind_repository, digest
from repoagent.issue_agent.execution import validate_config
from repoagent.issue_agent.workflow import execute_case


def test_issue_transport_uses_structured_stream_without_prompt_only_fallback():
    from repoagent.issue_agent.workflow import StructuredModelClient
    from repoagent.providers.base import (
        ModelEvent,
        ModelMessage,
        ModelRequest,
        ModelResult,
    )

    class Leaf:
        model = "test"
        supports_native_tools = True

        def generate(self, request):
            pytest.fail("legacy prompt-only generate must not be used")

        def stream(self, request):
            assert request.messages[-1].content == "previous result"
            yield ModelEvent(kind="completed", result=ModelResult(text="done"))

    request = ModelRequest(
        prompt="next",
        max_output_tokens=32,
        messages=(ModelMessage(role="user", content="previous result"),),
    )
    assert StructuredModelClient(Leaf()).generate(request).text == "done"


@pytest.fixture
def case(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("before\n")

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(repo), *args], text=True
        ).strip()

    git("init", "-q")
    git("add", ".")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "base",
    )
    git("remote", "add", "origin", "https://github.com/example/project.git")
    store = CaseStore(tmp_path / "cases")
    state = store.create(
        {
            "url": "https://github.com/example/project/issues/1",
            "title": "bug",
            "body": "broken",
        },
        bind_repository(repo, git("rev-parse", "HEAD"), "example/project"),
    )
    config = {
        "image": "python@sha256:" + "a" * 64,
        "probe": "assert False, 'bug'",
        "failure_marker": "bug",
        "pythonpath": "src",
        "mutable_paths": ["main.py"],
    }
    return store, state["case_id"], config


def fake_agent(directory, state, config, client, phase):
    changes = {"main.py": "after\n"} if phase == "fix" else {}
    return {
        "worker": {"answer": "checked", "agent_status": "completed"},
        "changes": changes,
        "changes_digest": digest(changes),
    }


def fake_verifier(directory, repository, config, changes=None):
    return {"passed": bool(changes), "reproduced": not bool(changes)}


def test_wrapped_budget_failure_retains_reason_and_cost(case):
    import json
    from repoagent.evolver.model_budget import EvaluationBudgetError
    from repoagent.evolver.model_proxy import ModelProxyError

    def exhausted(directory, state, config, client, phase):
        (directory / "model-budget.json").write_text(json.dumps({
            "calls_reserved": 3, "known_estimated_cost_usd": 0.12,
            "cost_complete": True,
        }))
        try:
            raise EvaluationBudgetError("input_limit")
        except EvaluationBudgetError as exc:
            raise ModelProxyError("model_call_failed") from exc

    store, cid, config = case
    with pytest.raises(ModelProxyError):
        execute_case(store, cid, "investigate", config=config,
                     client_factory=lambda: None, agent_runner=exhausted,
                     verifier=fake_verifier)
    state = store.load(cid)
    assert state["status"] == "budget_exhausted"
    assert state["runs"][-1]["budget_reason"] == "input_limit"
    assert state["runs"][-1]["model_evidence"]["known_estimated_cost_usd"] == 0.12


def run(case, phase="investigate", **kwargs):
    store, cid, config = case
    return execute_case(
        store,
        cid,
        phase,
        config=config,
        client_factory=lambda: None,
        agent_runner=kwargs.pop("agent_runner", fake_agent),
        verifier=kwargs.pop("verifier", fake_verifier),
        **kwargs,
    )


def test_investigate_then_explicit_fix_preserves_checkout_and_reports(case):
    state = run(case)
    assert state["status"] == "reproduced"
    state = run(case, "fix")
    assert state["status"] == "candidate_ready"
    assert "after" in (case[0].directory(case[1]) / "fix/candidate.patch").read_text()
    from pathlib import Path

    assert (Path(state["repository"]["path"]) / "main.py").read_text() == "before\n"
    assert (case[0].directory(case[1]) / "report.md").is_file()


def test_fix_requires_investigation_and_does_not_call_model(case):
    with pytest.raises(ValueError, match="requires"):
        run(case, "fix")


def test_finished_investigation_not_silently_replayed(case):
    run(case)
    with pytest.raises(ValueError, match="already attempted"):
        run(case)


def test_inflight_case_blocks_replay(case):
    state = case[0].load(case[1])
    state["runs"] = [{"status": "running"}]
    case[0].save(state)
    with pytest.raises(ValueError, match="uncertain"):
        run(case)


@pytest.mark.parametrize(
    "passed,status", [(True, "not_reproduced"), (False, "environment_blocked")]
)
def test_no_reproduction_does_not_spend_model_budget(case, passed, status):
    def forbidden(*args):
        pytest.fail("model should not run")

    state = run(
        case,
        agent_runner=forbidden,
        verifier=lambda *args: {"passed": passed, "reproduced": False},
    )
    assert state["status"] == status


def test_empty_issue_requests_information(case):
    state = case[0].load(case[1])
    state["issue"]["body"] = ""
    case[0].save(state)
    assert run(case)["status"] == "needs_information"


def test_model_failure_is_durable(case):
    def broken(*args):
        raise RuntimeError("failure")

    with pytest.raises(RuntimeError):
        run(case, agent_runner=broken)
    state = case[0].load(case[1])
    assert (
        state["status"] == "execution_failed"
        and state["runs"][0]["error_type"] == "RuntimeError"
    )


def test_failed_fix_not_ready(case):
    run(case)
    state = run(
        case, "fix", verifier=lambda *args: {"passed": False, "reproduced": True}
    )
    assert state["status"] == "verification_failed"


@pytest.mark.parametrize("empty_patch", [True, False])
def test_closeout_report_cannot_override_patch_acceptance(case, empty_patch):
    run(case)

    def closed_out(*args):
        changes = {} if empty_patch else {"main.py": "still broken\n"}
        return {
            "worker": {"answer": "Everything is fixed!", "agent_status": "completed",
                       "closeout_calls": [{"index": 22, "remaining": 1, "tools_enabled": False}]},
            "changes": changes, "changes_digest": digest(changes),
        }

    def verifier(directory, repository, config, changes=None):
        return {"passed": changes is not None and empty_patch,
                "reproduced": changes is None or not empty_patch}

    state = run(case, "fix", agent_runner=closed_out, verifier=verifier)
    assert state["status"] == "verification_failed"
    assert "candidate" in state["runs"][-1]["verification"]


def test_stopped_agent_is_not_a_completed_investigation(case):
    def stopped(*args):
        return {
            "worker": {"answer": "out of budget", "agent_status": "stopped"},
            "changes": {},
        }

    assert run(case, agent_runner=stopped)["status"] == "investigation_incomplete"


@pytest.mark.parametrize("phase", ["investigate", "fix"])
def test_provider_step_limit_is_budget_exhaustion_not_verification_failure(case, phase):
    if phase == "fix":
        run(case)

    def stopped(*args):
        return {
            "worker": {"agent_status": "stopped", "stop_reason": "step_limit_reached"},
            "changes": {"main.py": "after\n"},
        }

    state = run(case, phase, agent_runner=stopped)
    assert state["status"] == "budget_exhausted"
    assert state["runs"][-1]["budget_reason"] == "call_limit"
    assert "candidate" not in state["runs"][-1]["verification"]


def test_progress_tracks_actual_stages(case):
    events = []
    run(case, on_progress=events.append)
    assert events == ["baseline", "agent", "finished:reproduced"]
    events.clear()
    run(case, "fix", on_progress=events.append)
    assert events == ["baseline", "agent", "candidate", "finished:candidate_ready"]


def test_blocked_baseline_never_announces_agent_execution(case):
    events = []
    run(
        case,
        on_progress=events.append,
        verifier=lambda *args: {"passed": False, "reproduced": False},
    )
    assert events == ["baseline", "finished:environment_blocked"]


def test_frozen_config_tampering_blocks_repair(case):
    run(case)
    state = case[0].load(case[1])
    state["execution_config"]["probe"] = "pass"
    case[0].save(state)
    with pytest.raises(ValueError, match="frozen execution config changed"):
        run(case, "fix")


def test_verified_patch_digest_must_match_deliverable(case):
    run(case)

    def mismatched(directory, repository, config, changes=None):
        return {
            "passed": bool(changes),
            "reproduced": not bool(changes),
            "patch_digest": "wrong",
        }

    with pytest.raises(ValueError, match="delivered patch differs"):
        run(case, "fix", verifier=mismatched)
    assert case[0].load(case[1])["status"] == "execution_failed"


@pytest.mark.parametrize(
    "key,value",
    [
        ("image", "python:latest"),
        ("mutable_paths", ["../outside"]),
        ("pythonpath", "src;echo bad"),
        ("probe", ""),
        ("failure_marker", ""),
    ],
)
def test_config_invalid(case, key, value):
    config = dict(case[2], **{key: value})
    with pytest.raises(ValueError):
        validate_config(config)


def test_budget_counter_accepts_native_immutable_tool_schema(monkeypatch):
    from repoagent.issue_agent.workflow import make_client
    from repoagent.providers.base import ModelRequest, ModelTool
    from test_evolver_model_budget import LeafClient
    from repoagent.providers.clients import AnthropicCompatibleModelClient
    import repoagent.cli
    import repoagent.config

    monkeypatch.setattr(repoagent.config, "load_project_env", lambda path: None)
    class FixtureAnthropic(LeafClient, AnthropicCompatibleModelClient):
        temperature = None

        def stream(self, request):
            from repoagent.providers.base import ModelEvent
            yield ModelEvent(kind="completed", result=self.generate(request))

    monkeypatch.setattr(repoagent.cli, "_build_model_client", lambda args: FixtureAnthropic())
    client = make_client()
    result = client.generate(
        ModelRequest(
            "read a file",
            10,
            tools=(
                ModelTool("read_file", "read", {"type": "object", "properties": {}}),
            ),
        )
    )
    assert result.text and client.evidence()["measurement_valid"]
