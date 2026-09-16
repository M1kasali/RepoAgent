import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from repoagent.evolver import CandidateCheck
from repoagent.issue_agent.cases import digest, git
from repoagent.issue_agent.strategy_evaluation import IssueRepairEvaluator, repair_seed, trial_result
from repoagent.issue_agent.strategy_efficiency import summarize_efficiency
from repoagent.issue_agent.strategy_reliability import summarize_reliability
from repoagent.issue_agent.workflow import execute_case
from test_issue_workflow import case as _case, run


@pytest.mark.parametrize("flags", [
    {"measurement_valid": False, "cost_complete": True},
    {"measurement_valid": True, "cost_complete": False},
])
def test_invalid_measurement_retains_known_paid_cost_without_qualifying(flags):
    result = trial_result({"case_id": "invalid", "status": "execution_failed", "runs": [{
        "phase": "fix", "model_evidence": {**flags, "calls_reserved": 2,
            "known_estimated_cost_usd": 0.125}}]})
    assert result["estimated_cost_usd"] is None
    values = [{"task_id": "a", "repetition": 0, "arm": arm, "result": result}
              for arm in ("control", "treatment")]
    efficiency = summarize_efficiency(values, task_ids=["a"], repetitions=1)
    reliability = summarize_reliability(values, task_ids=["a"], repetitions=1,
                                        minimum_candidate_passes=1)
    for summary, key in ((efficiency, "estimated_cost_usd"),
                         (reliability, "known_estimated_cost_usd")):
        assert not summary["eligible_for_heldout_pilot"]
        assert summary["totals"]["control"][key] == 0.125
        assert summary["totals"]["control"]["calls"] == 2

case = _case


class Client:
    limits = SimpleNamespace(max_estimated_cost_usd=1)

    def descriptor(self):
        return {"model": "fixture", "max_calls": 23}


def test_flat_source_layout_is_allowed_but_mutation_root_is_not(case):
    from repoagent.issue_agent.execution import validate_config
    config = {**case[2], "pythonpath": "."}
    assert validate_config(config)["pythonpath"] == "."
    for unsafe in (".", "../main.py", "/tmp/main.py"):
        with pytest.raises(ValueError):
            validate_config({**config, "mutable_paths": [unsafe]})
    for unsafe in ("..", "/tmp", ".;pwd", "src/../"):
        with pytest.raises(ValueError):
            validate_config({**config, "pythonpath": unsafe})


def test_seed_excludes_previous_repair_and_refuses_unfinished_investigation(case):
    state = run(case)
    state["runs"].append({"phase": "fix", "answer": "GOLD_SOLUTION"})
    seed = repair_seed(state)
    assert "GOLD_SOLUTION" not in json.dumps(seed)
    assert seed["investigation"]["agent"]["worker"]["answer"] == "checked"
    state["runs"][0]["agent"]["worker"]["agent_status"] = "stopped"
    with pytest.raises(ValueError, match="completed"):
        repair_seed(state)


def test_adapter_runs_real_workflow_and_refuses_replay_and_drift(case, tmp_path):
    state = run(case)
    root = Path(state["repository"]["path"])
    base = state["repository"]["base_revision"]
    identity = {"commit_sha": base, "tree_sha": git(root, "rev-parse", base + "^{tree}")}
    seen = []

    def execute(store, cid, phase, **options):
        def agent(directory, current, config, client, stage):
            seen.append(current)
            assert "strategy_skill" not in config
            return {"worker": {"agent_status": "completed", "answer": "done"},
                    "changes": {"main.py": "after\n"}, "changes_digest": digest({"main.py": "after\n"}),
                    "model": {"measurement_valid": True, "cost_complete": True,
                              "known_estimated_cost_usd": 0.1, "calls_reserved": 2}}

        def verify(*args):
            candidate = len(args) == 4
            return {"status": "completed", "exit_code": 0 if candidate else 1,
                    "passed": candidate, "reproduced": not candidate, "output_truncated": False}

        return execute_case(store, cid, phase, **options, agent_runner=agent, verifier=verify)

    evaluator = IssueRepairEvaluator(baseline_commit=base, tasks={"task": state},
        output_root=tmp_path / "pairs", client_factory=Client, execute=execute)
    descriptor = evaluator.descriptor(root)
    check = CandidateCheck("task", "issue_repair")
    with pytest.raises(RuntimeError, match="budget"):
        evaluator.run_trial(root, identity, check, 0, descriptor, cost_limit_usd=0.5)
    result = evaluator.run_trial(root, identity, check, 0, descriptor, cost_limit_usd=1)
    assert result["passed"] is True and result["estimated_cost_usd"] == 0.1
    assert seen[0]["experiment"]["shared_seed_digest"] == digest(repair_seed(state))
    with pytest.raises(FileExistsError):
        evaluator.run_trial(root, identity, check, 0, descriptor, cost_limit_usd=1)
    evaluator.tasks["task"]["issue"]["body"] = "changed"
    with pytest.raises(RuntimeError, match="configuration changed"):
        evaluator.run_trial(root, identity, check, 1, descriptor, cost_limit_usd=1)


@pytest.mark.parametrize("status", ["budget_exhausted", "environment_blocked", "execution_failed"])
def test_unverified_failure_never_becomes_quality_score(status):
    result = trial_result({"case_id": "fixture", "status": status, "runs": [{"phase": "fix"}]})
    assert result["passed"] is None and result["score"] is None
    assert result["estimated_cost_usd"] is None


def test_existing_evolver_drives_both_issue_arms_without_activation(case, tmp_path):
    from repoagent.evolver import ControlledEvolver, EvolutionLedger, EvolutionRunBudget, PairedPromotionGate
    from repoagent.evolver.contracts import CandidateBudget, FailureEvidence
    from repoagent.evolver.generator import CandidateGenerator
    from repoagent.issue_agent.strategy import SKILL_PATH, SKILL_HEADER

    state = run(case)
    root = Path(state["repository"]["path"])
    base = state["repository"]["base_revision"]
    seen = []

    def execute(store, cid, phase, **options):
        current = store.load(cid)
        seen.append(current)
        current["runs"].append({"phase": "fix", "status": "completed",
            "verification": {"baseline": {"reproduced": True},
                             "candidate": {"status": "completed", "exit_code": 0,
                                           "output_truncated": False}},
            "agent": {"model": {"measurement_valid": True, "cost_complete": True,
                                 "known_estimated_cost_usd": 0.01, "calls_reserved": 1}}})
        current["status"] = "candidate_ready"
        store.save(current)

    evaluator = IssueRepairEvaluator(baseline_commit=base, tasks={"task": state},
        output_root=tmp_path / "paired", client_factory=Client, execute=execute)
    skill = SKILL_HEADER + "Run targeted checks."
    proposal = CandidateGenerator({"skill": lambda _: {SKILL_PATH: skill.encode()}}).generate(
        label="skill", base_commit=base,
        evidence=[FailureEvidence("fixture", "task", "fixture", "synthetic test only", "sha256:" + "a" * 64)],
        repository_reader=lambda _: None, budget=CandidateBudget(max_estimated_cost_usd=2))
    evolver = ControlledEvolver(EvolutionLedger(tmp_path / "ledger.jsonl"))
    assert evolver.evaluate_candidate(root, proposal,
        [CandidateCheck("shape", "validate_issue_skill")], evaluator).passed
    decision = evolver.evaluate_paired_checks(root, proposal,
        [CandidateCheck("task", "issue_repair")], evaluator,
        repetitions=1, gate=PairedPromotionGate(min_unique_tasks=1, min_mean_lift=0.1),
        run_budget=EvolutionRunBudget(max_pairs=1, max_estimated_cost_usd=2), max_trial_cost_usd=1)
    assert not decision.passed  # A tie is not an improvement.
    assert len(seen) == 2
    assert seen[0]["experiment"]["shared_seed_digest"] == seen[1]["experiment"]["shared_seed_digest"]
    assert "strategy_skill" not in seen[0]["execution_config"]
    assert seen[1]["execution_config"]["strategy_skill"] == skill
    assert not any("activat" in e["event_type"] for e in evolver.ledger.events())
