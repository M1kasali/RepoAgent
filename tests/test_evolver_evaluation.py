import json
import shutil
from concurrent.futures import ThreadPoolExecutor

import pytest

from repoagent.evolver.evaluation import CandidateCheck, CandidateEvaluationError, DockerCandidateEvaluator, evaluation_plan
from repoagent.tool_execution import ProcessOutcome
from repoagent.evolver.orchestrator import ControlledEvolver
from repoagent.evolver.ledger import EvolutionLedger
from repoagent.evolver.workspace import candidate_ref
from test_evolver_contracts import _git
from test_evolver_materialization import repository as _repository


repository = _repository


class FixtureEvaluator:
    def __init__(self, *, fail=False, error=False, missing=False):
        self.calls = 0
        self.fail, self.error, self.missing = fail, error, missing

    def descriptor(self, root):
        return {"kind": "test-only/v1"}

    def evaluate(self, root, identity, checks, descriptor):
        self.calls += 1
        if self.error:
            raise RuntimeError("evaluator stopped")
        if self.missing:
            return []
        return [{"check_id": check.check_id, "status": "fail" if self.fail else "pass"} for check in checks]


def test_evaluation_persists_receipt_and_replays_without_execution(repository):
    root, proposal, evolver = repository
    evaluator = FixtureEvaluator()
    checks = [CandidateCheck("tests", "python -m pytest")]
    decision = evolver.evaluate_candidate(root, proposal, checks, evaluator)
    assert decision.passed
    assert evolver.evaluate_candidate(root, proposal, checks, evaluator).to_dict() == decision.to_dict()
    assert evaluator.calls == 1
    assert [event["event_type"] for event in evolver.ledger.events()] == [
        "candidate.created", "candidate.materialized", "evaluation.started", "evaluation.completed", "gate.evaluated"
    ]
    receipt = json.loads(next((evolver.ledger.path.parent / "evidence").glob("*.json")).read_text())
    assert receipt["plan"]["identity"]["candidate_id"] == proposal.manifest.candidate_id
    assert receipt["plan"]["checks"][0]["command"] == checks[0].command


@pytest.mark.parametrize("mode", ["failure", "missing"])
def test_failed_or_missing_checks_do_not_pass(repository, mode):
    root, proposal, evolver = repository
    evaluator = FixtureEvaluator(fail=mode == "failure", missing=mode == "missing")
    decision = evolver.evaluate_candidate(root, proposal, [CandidateCheck("tests", "test")], evaluator)
    assert not decision.passed
    assert not any(event["event_type"].startswith("approval") for event in evolver.ledger.events())


def test_exception_records_failure_and_refuses_automatic_rerun(repository):
    root, proposal, evolver = repository
    evaluator = FixtureEvaluator(error=True)
    checks = [CandidateCheck("tests", "test")]
    with pytest.raises(RuntimeError, match="stopped"):
        evolver.evaluate_candidate(root, proposal, checks, evaluator)
    assert evolver.ledger.events()[-1]["event_type"] == "evaluation.failed"
    with pytest.raises(CandidateEvaluationError, match="interrupted"):
        evolver.evaluate_candidate(root, proposal, checks, evaluator)
    assert evaluator.calls == 1


def test_completed_receipt_recovers_missing_gate_without_rerunning(repository, monkeypatch):
    root, proposal, evolver = repository
    evaluator = FixtureEvaluator()
    checks = [CandidateCheck("tests", "test")]
    original = evolver.record_gate
    monkeypatch.setattr(evolver, "record_gate", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("journal interrupted")))
    with pytest.raises(OSError):
        evolver.evaluate_candidate(root, proposal, checks, evaluator)
    monkeypatch.setattr(evolver, "record_gate", original)
    assert evolver.evaluate_candidate(root, proposal, checks, evaluator).passed
    assert evaluator.calls == 1


def test_changed_receipt_is_not_recomputed_as_new_evidence(repository):
    root, proposal, evolver = repository
    evaluator = FixtureEvaluator()
    checks = [CandidateCheck("tests", "test")]
    evolver.evaluate_candidate(root, proposal, checks, evaluator)
    path = next((evolver.ledger.path.parent / "evidence").glob("*.json"))
    receipt = json.loads(path.read_text())
    receipt["results"][0]["status"] = "fail"
    path.write_text(json.dumps(receipt))
    with pytest.raises(CandidateEvaluationError, match="evidence changed"):
        evolver.evaluate_candidate(root, proposal, checks, evaluator)
    assert evaluator.calls == 1


def test_changed_check_plan_refuses_cached_result_and_new_execution(repository):
    root, proposal, evolver = repository
    evaluator = FixtureEvaluator()
    evolver.evaluate_candidate(root, proposal, [CandidateCheck("tests", "old")], evaluator)
    with pytest.raises(CandidateEvaluationError, match="plan changed"):
        evolver.evaluate_candidate(root, proposal, [CandidateCheck("tests", "new")], evaluator)
    assert evaluator.calls == 1


def test_concurrent_evaluation_runs_once(repository):
    root, proposal, evolver = repository
    evaluator = FixtureEvaluator()
    def run(_):
        runner = ControlledEvolver(EvolutionLedger(evolver.ledger.path))
        return runner.evaluate_candidate(root, proposal, [CandidateCheck("tests", "test")], evaluator)
    with ThreadPoolExecutor(max_workers=3) as pool:
        decisions = list(pool.map(run, range(3)))
    assert all(decision.passed for decision in decisions)
    assert evaluator.calls == 1


def test_ref_changed_during_evaluation_cannot_receive_pass_gate(repository):
    root, proposal, evolver = repository
    class DriftingEvaluator(FixtureEvaluator):
        def evaluate(self, root, identity, checks, descriptor):
            _git(root, "update-ref", candidate_ref(proposal.manifest.candidate_id), proposal.manifest.base_commit)
            return super().evaluate(root, identity, checks, descriptor)
    with pytest.raises(CandidateEvaluationError, match="reference changed"):
        evolver.evaluate_candidate(root, proposal, [CandidateCheck("tests", "test")], DriftingEvaluator())
    events = evolver.ledger.events()
    assert events[-1]["event_type"] == "evaluation.failed"
    assert not any(event["event_type"] == "gate.evaluated" for event in events)


def test_unanchored_receipt_after_completion_write_failure_is_not_trusted(repository, monkeypatch):
    root, proposal, evolver = repository
    evaluator = FixtureEvaluator()
    checks = [CandidateCheck("tests", "test")]
    original = evolver.ledger._append_protocol

    def append(event_type, **kwargs):
        if event_type == "evaluation.completed":
            raise OSError("completion not recorded")
        return original(event_type, **kwargs)

    monkeypatch.setattr(evolver.ledger, "_append_protocol", append)
    with pytest.raises(OSError):
        evolver.evaluate_candidate(root, proposal, checks, evaluator)
    assert list((evolver.ledger.path.parent / "evidence").glob("*.json"))
    monkeypatch.setattr(evolver.ledger, "_append_protocol", original)
    with pytest.raises(CandidateEvaluationError, match="interrupted"):
        evolver.evaluate_candidate(root, proposal, checks, evaluator)
    assert evaluator.calls == 1


def test_cleanup_failure_retains_evaluation_worktree_for_recovery(repository, monkeypatch):
    from repoagent.evolver import evaluation

    root, proposal, evolver = repository
    workspaces = []
    class BrokenCleanup:
        def __init__(self, workspace, **kwargs):
            workspaces.append(workspace)

        def execute(self, *args, **kwargs):
            return ProcessOutcome("completed", 0, "", "", 0, 0, False)

        def stop(self):
            raise OSError("daemon unavailable")

    monkeypatch.setattr(evaluation, "PersistentDockerSandboxAdapter", BrokenCleanup)
    backend = DockerCandidateEvaluator()
    monkeypatch.setattr(backend, "descriptor", lambda root: {"image_id": "sha256:" + "a" * 64})
    try:
        with pytest.raises(CandidateEvaluationError, match="retained worktree"):
            evolver.evaluate_candidate(root, proposal, [CandidateCheck("tests", "test")], backend)
        assert workspaces[0].exists()
        assert _git(root, "worktree", "list", "--porcelain").count("worktree ") == 2
        assert evolver.ledger.events()[-1]["event_type"] == "evaluation.failed"
    finally:
        for workspace in workspaces:
            _git(root, "worktree", "remove", "--force", str(workspace))
            shutil.rmtree(workspace.parent)


@pytest.mark.parametrize("checks", [[], [CandidateCheck("same", "x"), CandidateCheck("same", "y")], [CandidateCheck("slow", "x", 301)], [CandidateCheck("large", "x", max_output_chars=1_000_001)]])
def test_plan_admission_rejects_empty_duplicate_and_over_budget(checks):
    with pytest.raises(ValueError):
        evaluation_plan({}, checks, {})


@pytest.mark.parametrize("timeout", [True, 0, -1, float("nan"), float("inf")])
def test_invalid_check_deadline(timeout):
    with pytest.raises(ValueError):
        CandidateCheck("tests", "test", timeout)
