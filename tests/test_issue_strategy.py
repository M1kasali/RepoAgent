import asyncio
import json
from pathlib import Path

import pytest

from repoagent import RepoAgent, SessionStore, WorkspaceContext
from repoagent.issue_agent.execution import validate_config
from repoagent.issue_agent.strategy import SKILL_HEADER, install_strategy, validate_strategy
from repoagent.providers.base import ModelEvent, ModelResult, ModelUsage


@pytest.mark.parametrize("text", ["", "always: true", SKILL_HEADER, SKILL_HEADER + "\0bad"])
def test_invalid_strategy_rejected(text):
    with pytest.raises(ValueError):
        validate_strategy(text)


def test_issue_config_strategy_is_explicit_and_frozen():
    config = json.loads(Path("tests/fixtures/issue_demo/config-360.json").read_text())
    assert "strategy_skill" not in validate_config(config)
    config["strategy_skill"] = SKILL_HEADER + "Check boundary behavior."
    assert validate_config(config)["strategy_skill"] == config["strategy_skill"]


def test_skill_intervention_reaches_actual_native_system_message(tmp_path):
    class Provider:
        supports_native_tools = supports_structured_messages = True
        model = "test"

        def __init__(self):
            self.requests = []

        def stream(self, request):
            self.requests.append(request)
            yield ModelEvent(kind="completed", result=ModelResult(
                text="done", model=self.model,
                usage=ModelUsage(input_tokens=10, output_tokens=2),
            ))

    bundle = tmp_path / "bundle"
    install_strategy(bundle, SKILL_HEADER + "STRATEGY_REACHABILITY_9381: Verify boundary behavior.")
    payloads = []
    for enabled in (False, True):
        root = tmp_path / str(enabled)
        root.mkdir()
        provider = Provider()
        agent = RepoAgent(
            model_client=provider,
            workspace=WorkspaceContext.build(root, repo_root_override=root),
            session_store=SessionStore(root / ".repoagent/sessions"),
            approval_policy="auto", checkpoint_policy="never",
            max_provider_calls=1, context_token_budget=16000,
            context_window_tokens=1000000,
            feature_flags={"skills": enabled, "memory": False},
            skill_roots={"workspace": bundle / "skills"},
        )
        try:
            assert agent.ask("Investigate this issue.") == "done"
            request = provider.requests[0]
            assert request.tools
            system = "\n".join(m.content for m in request.messages if m.role == "system")
            assert ("STRATEGY_REACHABILITY_9381" in system) is enabled
            assert bool(agent.active_skills) is enabled
            payloads.append(system)
        finally:
            asyncio.run(agent.aclose())
    assert payloads[0] != payloads[1]


def test_evolver_candidate_changes_only_skill_config():
    from repoagent.evolver.contracts import FailureEvidence
    from repoagent.evolver.generator import CandidateGenerator
    from repoagent.issue_agent.strategy import SKILL_PATH, candidate_config

    config = json.loads(Path("tests/fixtures/issue_demo/config-360.json").read_text())
    original = json.loads(json.dumps(config))
    generator = CandidateGenerator({"skill": lambda evidence: {
        SKILL_PATH: (SKILL_HEADER + "Verify boundary behavior.").encode(),
    }})
    proposal = generator.generate(
        label="skill", base_commit="a" * 40,
        evidence=[FailureEvidence("failure", "task", "assertion", "observed", "sha256:" + "b" * 64)],
        repository_reader=lambda path: None,
    )
    changed = candidate_config(config, proposal, expected_base_commit="a" * 40)
    assert config == original
    assert {k: v for k, v in changed.items() if k != "strategy_skill"} == original
    with pytest.raises(ValueError, match="pinned"):
        candidate_config(config, proposal, expected_base_commit="b" * 40)
    with pytest.raises(ValueError, match="baseline"):
        candidate_config(changed, proposal, expected_base_commit="a" * 40)
