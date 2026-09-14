from dataclasses import replace
import json

import pytest

from test_evolver_contracts import _evidence, _git

TARGET = "benchmarks/appworld/agent_cli.py"
GRADER = "benchmarks/appworld/evolve/grade.py"


def scope(base="a" * 40, **kwargs):
    from repoagent.evolver.contracts import BenchmarkTarget
    return BenchmarkTarget(
        target_id="small-real", base_commit=base,
        mutable_paths=kwargs.get("mutable_paths", (TARGET,)),
        protected_paths=kwargs.get("protected_paths", (GRADER,)),
    )


def proposal(target, changes=None, **kwargs):
    from repoagent.evolver.generator import CandidateGenerator
    return CandidateGenerator({"benchmark": lambda _: changes or {TARGET: b"after\n"}}).generate(
        label="benchmark", base_commit=kwargs.get("base_commit", target.base_commit),
        evidence=[_evidence()], repository_reader=lambda _: b"before\n",
        benchmark_target=target,
    )


def test_scope_is_frozen_and_bound_to_candidate_digest():
    mutable = [TARGET]
    target = scope(mutable_paths=mutable)
    mutable.append("other.py")
    assert target.mutable_paths == (TARGET,)
    first = proposal(target)
    assert first.manifest.to_dict()["benchmark_target"] == target.to_dict()
    assert first.manifest.schema.endswith("/v2")
    second = proposal(replace(target, target_id="different-protocol"))
    assert first.manifest.patch_digest != second.manifest.patch_digest
    with pytest.raises(ValueError, match="digest"):
        replace(first.manifest, benchmark_target=replace(target, target_id="tampered"))


@pytest.mark.parametrize("path", ["../x", "/tmp/x", "a//x", "a/./x", "a\\x", "C:/x", "a/*", ".git/config", "sealed/task.py"])
def test_mutable_scope_rejects_unsafe_paths(path):
    with pytest.raises(ValueError):
        scope(mutable_paths=(path,))


def test_scope_requires_disjoint_explicit_files_and_exact_base():
    for kwargs in ({"mutable_paths": ()}, {"protected_paths": ()},
                   {"protected_paths": (TARGET,)}, {"mutable_paths": (TARGET, TARGET)}):
        with pytest.raises(ValueError):
            scope(**kwargs)
    with pytest.raises(ValueError):
        scope(base="main")
    with pytest.raises(ValueError, match="base"):
        proposal(scope(), base_commit="b" * 40)


def test_scoring_files_and_other_labels_do_not_gain_mutation_rights():
    from repoagent.evolver.generator import CandidateGenerator
    from repoagent.evolver.contracts import MUTATION_POLICIES, EvolutionLabel
    assert not MUTATION_POLICIES[EvolutionLabel.TOOL_POLICY].allows(TARGET)
    with pytest.raises(ValueError, match="policy"):
        proposal(scope(), {GRADER: b"return True"})
    with pytest.raises(ValueError, match="benchmark"):
        CandidateGenerator({"prompt": lambda _: {TARGET: b"x"}}).generate(
            label="prompt", base_commit="a" * 40, evidence=[_evidence()],
            repository_reader=lambda _: b"before", benchmark_target=scope(),
        )
    with pytest.raises(ValueError, match="target"):
        CandidateGenerator({"benchmark": lambda _: {TARGET: b"x"}}).generate(
            label="benchmark", base_commit="a" * 40, evidence=[_evidence()],
            repository_reader=lambda _: b"before",
        )


@pytest.fixture
def benchmark_repo(tmp_path):
    root = tmp_path / "subject"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.invalid")
    for name, data in ((TARGET, "before\n"), (GRADER, "fixed grader\n")):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data)
    _git(root, "add", ".")
    _git(root, "commit", "-m", "base")
    return root, scope(_git(root, "rev-parse", "HEAD"))


def test_real_materialization_preserves_grader_and_parent(benchmark_repo, tmp_path):
    from repoagent.evolver import ControlledEvolver, EvolutionLedger
    from repoagent.evolver.activation import ActivationError
    root, target = benchmark_repo
    evolver = ControlledEvolver(EvolutionLedger(tmp_path / "ledger.jsonl"))
    candidate = proposal(target)
    identity = evolver.materialize_candidate(root, candidate)
    assert _git(root, "show", identity["commit_sha"] + ":" + TARGET) == "after"
    assert _git(root, "show", identity["commit_sha"] + ":" + GRADER) == "fixed grader"
    assert (root / TARGET).read_text() == "before\n"
    assert _git(root, "rev-parse", "HEAD") == target.base_commit
    assert evolver.materialize_candidate(root, candidate) == identity
    with pytest.raises(ActivationError, match="benchmark"):
        evolver.activations.activate("benchmark", candidate.manifest.candidate_id, actor="operator")


def test_scope_rejects_missing_protected_file_before_checkout(benchmark_repo):
    from repoagent.evolver.workspace import CandidateWorkspaceError, GitCandidateWorkspace
    root, target = benchmark_repo
    candidate = proposal(replace(target, protected_paths=("missing-grader.py",)))
    with pytest.raises(CandidateWorkspaceError, match="regular"):
        with GitCandidateWorkspace(root, candidate):
            pytest.fail("invalid target must not materialize")


def test_scope_rejects_symlink_grader(benchmark_repo):
    from repoagent.evolver.workspace import CandidateWorkspaceError, verify_benchmark_target
    root, target = benchmark_repo
    (root / GRADER).unlink()
    (root / GRADER).symlink_to("../agent_cli.py")
    _git(root, "add", GRADER)
    _git(root, "commit", "-m", "symlink grader")
    target = replace(target, base_commit=_git(root, "rev-parse", "HEAD"))
    with pytest.raises(CandidateWorkspaceError, match="regular"):
        verify_benchmark_target(root, target)


def test_legacy_manifest_keeps_v1_digest_and_shape():
    from repoagent.evolver.contracts import sha256_bytes
    from test_evolver_contracts import _proposal
    manifest = _proposal().manifest
    payload = [item.to_dict() for item in manifest.mutations]
    assert manifest.patch_digest == sha256_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    assert manifest.schema.endswith("/v1")
    assert "benchmark_target" not in manifest.to_dict()


def test_model_proposer_freezes_scope_and_never_exposes_grader(benchmark_repo, tmp_path):
    from repoagent.evolver import ModelCandidateProposer
    from test_evolver_model_budget import _client, LeafClient
    root, target = benchmark_repo
    client = _client(LeafClient(outputs=[json.dumps({"files": {TARGET: "after\n"}})]))
    proposer = ModelCandidateProposer(
        root, base_commit=target.base_commit, label="benchmark", paths=[TARGET],
        benchmark_target=target, evidence=[_evidence()], client=client,
        journal_directory=tmp_path / "model-journal",
    )
    assert proposer.descriptor()["benchmark_target"] == target.to_dict()
    assert set(proposer.before) == {TARGET}
    candidate = proposer({"base_commit": target.base_commit})
    assert candidate.manifest.benchmark_target == target


@pytest.mark.parametrize("declared", [False, True])
def test_search_rejects_candidate_selected_scope(benchmark_repo, tmp_path, declared):
    from repoagent.evolver import ControlledEvolver, EvolutionLedger
    from test_evolver_search import run
    from test_evolver_paired_execution import TrialBackend
    root, target = benchmark_repo
    candidate = proposal(target)
    evolver = ControlledEvolver(EvolutionLedger(tmp_path / "ledger.jsonl"))
    backend = TrialBackend()
    result = run(
        (root, candidate, evolver), propose=lambda _: candidate,
        paired_evaluator=backend,
        benchmark_target=replace(target, target_id="other") if declared else None,
    )
    assert all(row["status"] == "generation_failed" for row in result["rounds"])
    assert not backend.trials


def test_search_freezes_scope_and_rejects_resume_drift(benchmark_repo, tmp_path):
    from repoagent.evolver import ControlledEvolver, EvolutionLedger
    from repoagent.evolver.evaluation import CandidateEvaluationError
    from repoagent.evolver.search import SearchLimits
    from test_evolver_search import run
    from test_evolver_evaluation import FixtureEvaluator
    root, target = benchmark_repo
    candidate = proposal(target)
    evolver = ControlledEvolver(EvolutionLedger(tmp_path / "ledger.jsonl"))
    repository = (root, candidate, evolver)
    options = dict(propose=lambda _: candidate, benchmark_target=target,
                   deterministic_evaluator=FixtureEvaluator(fail=True),
                   limits=SearchLimits(max_rounds=1))
    result = run(repository, **options)
    assert result["plan"]["benchmark_target"] == target.to_dict()
    assert result["rounds"][0]["status"] == "rejected"
    assert run(repository, **options, resume=True) == result
    options["benchmark_target"] = replace(target, target_id="changed")
    with pytest.raises(CandidateEvaluationError, match="configuration changed"):
        run(repository, **options, resume=True)
