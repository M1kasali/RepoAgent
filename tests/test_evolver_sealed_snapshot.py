import pytest

from repoagent.evolver.evaluation import CandidateEvaluationError
from repoagent.evolver.hosted_snapshot import HostedAgentSnapshotEvaluator
from repoagent.evolver.sealed_snapshot import SnapshotSealedBackend
from repoagent.evolver.workspace import _git
from test_evolver_materialization import repository as repository


class Task:
    def __init__(self, name):
        self.name = name

    def check(self):
        return self.name


class Evaluator(HostedAgentSnapshotEvaluator):
    def __init__(self):
        self.tasks = {name: Task(name) for name in ("one", "two")}
        self.calls = []
        self.cost = 0.1
        self.status = "completed"
        self.version = 1

    def descriptor(self, root):
        return {
            "grader": "fixture",
            "tasks": list(self.tasks),
            "version": self.version,
            "model_gateway": {"limits": {"max_estimated_cost_usd": 1}},
        }

    def run_trial(self, root, identity, check, index, descriptor, *, cost_limit_usd):
        self.calls.append((dict(identity), check, index, cost_limit_usd))
        return {
            "status": self.status,
            "passed": True,
            "estimated_cost_usd": self.cost,
            "raw": {"fixture": True},
        }


def setup_backend(repository, paired=True):
    root, _, _ = repository
    evaluator = Evaluator()
    baseline = _git(root, "rev-parse", "HEAD")
    backend = SnapshotSealedBackend(
        root, evaluator, baseline_commit=baseline if paired else None
    )
    return (
        backend,
        evaluator,
        dict(
            candidate_ref="HEAD",
            task_ids=["one", "two"],
            grader_digest=backend.grader_digest,
        ),
    )


def test_pairs_reserve_both_arms_and_alternate_order(repository):
    backend, evaluator, options = setup_backend(repository)
    with pytest.raises(CandidateEvaluationError, match="reserve"):
        backend.evaluate(**options, max_estimated_cost_usd=3.99)
    assert evaluator.calls == []
    rows = backend.evaluate(**options, max_estimated_cost_usd=4)
    assert len(evaluator.calls) == 4
    assert [row["execution_order"] for row in rows] == [
        ["baseline", "candidate"],
        ["candidate", "baseline"],
    ]
    assert all(row["estimated_cost_usd"] == 0.2 for row in rows)
    assert [call[1] for call in evaluator.calls] == ["one", "one", "two", "two"]
    assert all(call[3] == 1 for call in evaluator.calls)


@pytest.mark.parametrize("cost", [None, float("nan"), float("inf"), -1, True, 1.1])
def test_unpriced_or_invalid_arm_stops_without_retry(repository, cost):
    backend, evaluator, options = setup_backend(repository)
    evaluator.cost = cost
    with pytest.raises(CandidateEvaluationError, match="execution review"):
        backend.evaluate(**options, max_estimated_cost_usd=4)
    assert len(evaluator.calls) == 1


@pytest.mark.parametrize("budget", [float("nan"), float("inf"), -1, True])
def test_invalid_budget_never_calls_model(repository, budget):
    backend, evaluator, options = setup_backend(repository)
    with pytest.raises(CandidateEvaluationError, match="budget"):
        backend.evaluate(**options, max_estimated_cost_usd=budget)
    assert not evaluator.calls


def test_descriptor_drift_never_calls_model(repository):
    backend, evaluator, options = setup_backend(repository)
    evaluator.version += 1
    with pytest.raises(CandidateEvaluationError, match="configuration changed"):
        backend.evaluate(**options, max_estimated_cost_usd=4)
    assert not evaluator.calls


def test_legacy_single_arm_contract(repository):
    backend, evaluator, options = setup_backend(repository, paired=False)
    assert backend.descriptor(backend.repo_root)["kind"] == "snapshot-sealed/v1"
    rows = backend.evaluate(**options, max_estimated_cost_usd=2)
    assert len(evaluator.calls) == 2
    assert all("arms" not in row and row["estimated_cost_usd"] == 0.1 for row in rows)
