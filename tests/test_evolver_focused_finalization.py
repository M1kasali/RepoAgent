import json
from dataclasses import asdict

import pytest

from repoagent.evolver import CandidateEvaluationError, TaskTrialSummary
from repoagent.evolver.focused_finalization import freeze_focused_sealed_plan, retention_summary
from repoagent.evolver.sealed import SealedEvaluationVault, SealedBoundaryError
from repoagent.evolver.workspace import _git
import test_evolver_focused_search as training_fixtures

setup = training_fixtures.setup
benchmark_repo = training_fixtures.benchmark_repo


class SealedBackend:
    is_isolated = True

    def __init__(self, root):
        self.root = root
        self.calls = 0
        self.version = 1
        self.mutate = lambda rows: None

    def descriptor(self, root):
        return {"kind": "isolated-test-fixture", "version": self.version}

    def evaluate(self, *, candidate_ref, baseline_ref, task_ids, k, **kwargs):
        self.calls += 1
        rows = []
        for tid in task_ids:
            arms = {}
            for arm, sha in (("baseline", baseline_ref), ("candidate", candidate_ref)):
                arms[arm] = {"source": {"commit_sha": sha, "tree_sha": _git(self.root, "rev-parse", sha + "^{tree}")},
                    "measurement": asdict(TaskTrialSummary(tid, k if arm == "candidate" and sha != baseline_ref else 0, k)),
                    "estimated_cost_usd": 0.0}
            rows.append({"task_id": tid, "arms": arms})
        self.mutate(rows)
        return rows


@pytest.fixture
def sealed(setup, tmp_path):
    backend = SealedBackend(setup[0])
    vault = SealedEvaluationVault(tmp_path / "vault", training_task_ids=["a", "b"],
        sealed_task_ids=["c", "d", "e", "f"], grader_digest="sha256:" + "a" * 64)
    setup[2]["sealed_plan"] = freeze_focused_sealed_plan(setup[0], vault=vault, backend=backend,
        k=2, max_estimated_cost_usd=0)
    return vault, backend


def finish(setup, sealed):
    root, evolver, options, _, _ = setup
    evolver.search_focused(root, **options)
    return evolver.finalize_focused_search(root, run_id=options["run_id"], vault=sealed[0], backend=sealed[1])


def test_full_workflow_reports_and_never_activates(setup, sealed):
    root, evolver, options, _, _ = setup
    result = evolver.prepare_focused_evolution(root, search_options=options,
        vault=sealed[0], sealed_backend=sealed[1])["finalization"]
    assert result["execution_completed"] and result["passed"]
    assert not result["automatic_activation"]
    directory = evolver.ledger.path.parent / "focused-searches" / options["run_id"]
    assert json.loads((directory / "report.json").read_text()) == result
    assert "No automatic activation" in (directory / "report.md").read_text()
    assert not any(e["event_type"].startswith("activation.") for e in evolver.ledger.events())
    with pytest.raises(CandidateEvaluationError, match="already started"):
        evolver.finalize_focused_search(root, run_id=options["run_id"], vault=sealed[0], backend=sealed[1])
    assert sealed[1].calls == 1


def test_missing_frozen_plan_rejected(setup, sealed):
    del setup[2]["sealed_plan"]
    with pytest.raises(CandidateEvaluationError, match="not frozen"):
        finish(setup, sealed)
    assert sealed[1].calls == 0


def test_changed_backend_rejected_before_scoring(setup, sealed):
    sealed[1].version += 1
    with pytest.raises(CandidateEvaluationError, match="changed since training"):
        finish(setup, sealed)
    assert sealed[1].calls == 0


def test_overlap_rejected_before_training(setup, sealed):
    setup[2]["sealed_plan"]["tasks"] = ["a"]
    with pytest.raises(SealedBoundaryError):
        finish(setup, sealed)
    assert not setup[3].calls and not setup[4].requests


@pytest.mark.parametrize("mutation", [
    lambda rows: rows.pop(),
    lambda rows: rows[0].update(task_id="wrong"),
    lambda rows: rows[0]["arms"].pop("baseline"),
    lambda rows: rows[0]["arms"]["candidate"].update(estimated_cost_usd=None),
    lambda rows: rows[0]["arms"]["candidate"].update(estimated_cost_usd=1),
    lambda rows: rows[0]["arms"]["candidate"]["source"].update(tree_sha="wrong"),
    lambda rows: rows[0]["arms"]["candidate"]["measurement"].update(attempts=1),
    lambda rows: rows[0]["arms"]["candidate"]["measurement"].update(infra_attempts=1),
])
def test_bad_evidence_fails_closed_without_retry(setup, sealed, mutation):
    sealed[1].mutate = mutation
    with pytest.raises(CandidateEvaluationError):
        finish(setup, sealed)
    with pytest.raises(CandidateEvaluationError, match="already started"):
        setup[1].finalize_focused_search(setup[0], run_id="focused", vault=sealed[0], backend=sealed[1])
    assert sealed[1].calls == 1


def test_rejected_training_selects_baseline_without_credit(setup, sealed):
    setup[3].reject = True
    result = finish(setup, sealed)
    assert result["baseline_selected"] and not result["passed"]
    assert result["metrics"]["test_lift"] == 0


def test_small_original_gain_is_not_statistical_credit():
    ids = ["a", "b", "c", "d"]
    baseline = {tid: TaskTrialSummary(tid, 2 if tid == "a" else 0, 2) for tid in ids}
    candidate = {tid: TaskTrialSummary(tid, 2 if tid in ("a", "b") else 0, 2) for tid in ids}
    result = retention_summary(.4, .6, baseline, candidate, ids, 2)
    assert result["baseline_test"] == .25 and result["candidate_test"] == .5
    assert result["sealed_z"] == 1 and not result["sealed_credited_2sigma"]


def test_unfinished_search_is_rejected(setup, sealed):
    with pytest.raises(CandidateEvaluationError, match="not finished"):
        setup[1].finalize_focused_search(setup[0], run_id="focused", vault=sealed[0], backend=sealed[1])
    assert sealed[1].calls == 0


def test_training_split_mismatch_fails_before_model_call(setup, sealed):
    sealed[0].training_task_ids = ("a",)
    with pytest.raises(CandidateEvaluationError, match="training split"):
        setup[1].prepare_focused_evolution(setup[0], search_options=setup[2],
            vault=sealed[0], sealed_backend=sealed[1])
    assert not setup[4].requests and not setup[3].calls


@pytest.mark.parametrize("tamper_ref", [False, True])
def test_changed_training_evidence_blocks_sealed_scoring(setup, sealed, tamper_ref):
    from repoagent.evolver.workspace import candidate_ref
    root, evolver, options, _, _ = setup
    state = evolver.search_focused(root, **options)
    cid = state["rounds"][0]["candidate_id"]
    if tamper_ref:
        _git(root, "update-ref", candidate_ref(cid), options["target"].base_commit)
    else:
        artifact = evolver.ledger.path.parent / "evidence" / f"{cid}-deterministic.json"
        payload = json.loads(artifact.read_text())
        payload["tampered"] = True
        artifact.write_text(json.dumps(payload))
    with pytest.raises(CandidateEvaluationError, match="changed"):
        evolver.finalize_focused_search(root, run_id="focused", vault=sealed[0], backend=sealed[1])
    assert sealed[1].calls == 0
