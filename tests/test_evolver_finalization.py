import pytest

from repoagent.evolver import SealedEvaluationVault
from repoagent.evolver.evaluation import CandidateEvaluationError
from test_evolver_materialization import repository as repository
from test_evolver_search import run


class Backend:
    is_isolated = True

    def __init__(self, passed=True, broken=False):
        self.calls = 0
        self.passed, self.broken = passed, broken

    def descriptor(self, root):
        return {"kind": "fixture-only"}

    def evaluate(
        self, *, candidate_ref, task_ids, grader_digest, max_estimated_cost_usd
    ):
        self.calls += 1
        if self.broken:
            raise RuntimeError("private failure")
        return [
            {"task_id": task, "passed": self.passed, "estimated_cost_usd": 0}
            for task in task_ids
        ]


def prepare(repository, tmp_path):
    state = run(repository)
    vault = SealedEvaluationVault(
        tmp_path / "sealed",
        training_task_ids=["quality"],
        sealed_task_ids=["hidden"],
        grader_digest="sha256:" + "a" * 64,
    )
    return state["qualified_candidates"][0], vault


def test_finished_search_sealed_once_then_explicit_approval(repository, tmp_path):
    root, _, evolver = repository
    candidate, vault = prepare(repository, tmp_path)
    backend = Backend()
    options = dict(
        run_id="search-one",
        candidate_id=candidate,
        vault=vault,
        backend=backend,
        max_estimated_cost_usd=0,
    )
    result = evolver.finalize_search(root, **options)
    assert result["passed"]
    assert not any(
        e["event_type"].startswith("approval") for e in evolver.ledger.events()
    )
    token = evolver.request_finalist_approval(
        run_id="search-one", candidate_id=candidate
    )
    evolver.approvals.confirm(token, actor="human-reviewer")
    assert backend.calls == 1
    with pytest.raises(CandidateEvaluationError, match="already frozen"):
        evolver.finalize_search(root, **options)
    assert backend.calls == 1
    assert not any(
        e["event_type"].startswith("activation") for e in evolver.ledger.events()
    )


@pytest.mark.parametrize("broken", [False, True])
def test_failed_or_uncertain_sealed_blocks_approval_and_rerun(
    repository, tmp_path, broken
):
    root, _, evolver = repository
    candidate, vault = prepare(repository, tmp_path)
    backend = Backend(passed=False, broken=broken)
    options = dict(
        run_id="search-one",
        candidate_id=candidate,
        vault=vault,
        backend=backend,
        max_estimated_cost_usd=0,
    )
    if broken:
        with pytest.raises(RuntimeError):
            evolver.finalize_search(root, **options)
    else:
        assert not evolver.finalize_search(root, **options)["passed"]
    with pytest.raises(CandidateEvaluationError):
        evolver.request_finalist_approval(run_id="search-one", candidate_id=candidate)
    with pytest.raises(CandidateEvaluationError):
        evolver.finalize_search(root, **options)
    assert backend.calls == 1
    paired = [
        e
        for e in evolver.ledger.events()
        if e["candidate_id"] == candidate
        and e["event_type"] == "gate.evaluated"
        and e["payload"]["stage"] == "paired"
    ][-1]
    token = evolver.approvals.request(candidate, paired["payload"]["evidence_digest"])
    evolver.approvals.confirm(token, actor="human-reviewer")
    from repoagent.evolver.activation import ActivationError

    with pytest.raises(ActivationError, match="sealed"):
        evolver.activations.activate("prompt", candidate, actor="human-reviewer")


def test_cannot_finalize_other_candidate_after_seeing_sealed_result(
    repository, tmp_path
):
    root, _, evolver = repository
    first, vault = prepare(repository, tmp_path)
    state = next(
        e["payload"]["state"]
        for e in evolver.ledger.events()
        if e["event_type"] == "search.finished"
    )
    backend = Backend()
    evolver.finalize_search(
        root,
        run_id="search-one",
        candidate_id=first,
        vault=vault,
        backend=backend,
        max_estimated_cost_usd=0,
    )
    with pytest.raises(CandidateEvaluationError, match="frozen"):
        evolver.finalize_search(
            root,
            run_id="search-one",
            candidate_id=state["qualified_candidates"][1],
            vault=vault,
            backend=backend,
            max_estimated_cost_usd=0,
        )
    assert backend.calls == 1
