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


class PairedBackend(Backend):
    def __init__(self, root, baseline, baseline_passed, candidate_passed, defect=None):
        super().__init__()
        from repoagent.evolver.workspace import _git

        self.root = root
        self.baseline = {
            "commit_sha": baseline,
            "tree_sha": _git(root, "rev-parse", baseline + "^{tree}"),
        }
        self.baseline_passed = baseline_passed
        self.candidate_passed = candidate_passed
        self.defect = defect

    def descriptor(self, root):
        return {"kind": "snapshot-sealed-paired/v1", "baseline": self.baseline}

    def evaluate(self, *, candidate_ref, task_ids, **kwargs):
        from repoagent.evolver.workspace import _git

        self.calls += 1
        row = {
            "task_id": task_ids[0],
            "passed": self.candidate_passed,
            "estimated_cost_usd": 0.5,
            "execution_order": ["baseline", "candidate"],
            "arms": {
                "baseline": {
                    "source": self.baseline,
                    "passed": self.baseline_passed,
                    "estimated_cost_usd": 0.2,
                },
                "candidate": {
                    "source": {
                        "commit_sha": candidate_ref,
                        "tree_sha": _git(
                            self.root, "rev-parse", candidate_ref + "^{tree}"
                        ),
                    },
                    "passed": self.candidate_passed,
                    "estimated_cost_usd": 0.3,
                },
            },
        }
        if self.defect == "cost":
            row["arms"]["baseline"]["estimated_cost_usd"] = None
        elif self.defect == "summary":
            row["estimated_cost_usd"] = 0
        elif self.defect == "identity":
            row["arms"]["candidate"]["source"] = self.baseline
        elif self.defect == "missing":
            del row["arms"]["baseline"]
        return [row]


@pytest.mark.parametrize(
    "control,treatment,passed,comparison",
    [
        (False, True, True, {"wins": 1, "ties": 0, "losses": 0}),
        (True, True, False, {"wins": 0, "ties": 1, "losses": 0}),
        (True, False, False, {"wins": 0, "ties": 0, "losses": 1}),
        (False, False, False, {"wins": 0, "ties": 1, "losses": 0}),
    ],
)
def test_sealed_comparison_recomputes_gate(
    repository, tmp_path, control, treatment, passed, comparison
):
    root, _, evolver = repository
    candidate, vault = prepare(repository, tmp_path)
    state = next(
        e["payload"]["state"]
        for e in evolver.ledger.events()
        if e["event_type"] == "search.finished"
    )
    backend = PairedBackend(root, state["base_commit"], control, treatment)
    options = dict(
        run_id="search-one",
        candidate_id=candidate,
        vault=vault,
        backend=backend,
        max_estimated_cost_usd=0.5,
    )
    result = evolver.finalize_search(root, **options)
    assert result["passed"] is passed
    assert result["comparison"] == comparison
    assert result["estimated_cost_usd"] == 0.5
    if not passed:
        with pytest.raises(CandidateEvaluationError, match="passing sealed"):
            evolver.request_finalist_approval(
                run_id="search-one", candidate_id=candidate
            )
    with pytest.raises(CandidateEvaluationError, match="already frozen"):
        evolver.finalize_search(root, **options)
    assert backend.calls == 1


@pytest.mark.parametrize(
    "defect", ["cost", "summary", "identity", "missing", "baseline"]
)
def test_sealed_pair_rejects_invalid_evidence(repository, tmp_path, defect):
    root, _, evolver = repository
    candidate, vault = prepare(repository, tmp_path)
    state = next(
        e["payload"]["state"]
        for e in evolver.ledger.events()
        if e["event_type"] == "search.finished"
    )
    backend = PairedBackend(root, state["base_commit"], False, True, defect)
    if defect == "baseline":
        backend.baseline = {"commit_sha": "wrong", "tree_sha": "wrong"}
    options = dict(
        run_id="search-one",
        candidate_id=candidate,
        vault=vault,
        backend=backend,
        max_estimated_cost_usd=0.5,
    )
    with pytest.raises(CandidateEvaluationError):
        evolver.finalize_search(root, **options)
    assert backend.calls == (0 if defect == "baseline" else 1)
    with pytest.raises(CandidateEvaluationError):
        evolver.request_finalist_approval(run_id="search-one", candidate_id=candidate)
