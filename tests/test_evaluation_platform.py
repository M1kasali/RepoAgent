import json
from dataclasses import replace

import pytest

from repoagent.evaluation.campaigns import (
    RuntimeContractCampaign,
    fault_campaign_result,
    paired_campaign_matrix,
    paired_campaign_result,
)
from repoagent.evaluation.faults import FaultInjector, FaultPlan, InjectedFault, run_fault_matrix
from repoagent.evaluation.red_team import (
    RedTeamCampaign,
    RedTeamCase,
    RedTeamObservation,
    grade_red_team,
)
from repoagent.evaluation.release import ReleaseEvidenceBuilder, compare_results
from repoagent.evaluation.schema import (
    EvaluationResult,
    EvaluationRow,
    collect_environment_provenance,
    collect_source_provenance,
    new_experiment,
    validate_result_payload,
)
from repoagent.evaluation.statistics import (
    exact_mcnemar,
    paired_bootstrap_interval,
    paired_win_tie_loss,
    wilson_interval,
)
from repoagent.evaluation.swebench import SWEBenchAdapter
from repoagent.evaluation.workspace import RawRowWriter, TrialWorkspace


def _result(rows, *, dirty=False, digest="sha256:bench"):
    return EvaluationResult(
        experiment=new_experiment("test", "test-id"),
        source={"commit_sha": "a" * 40, "branch": "main", "dirty": dirty, "tree_digest": "sha256:tree"},
        environment={"python": "3.11"},
        benchmark={"id": "bench", "version": 1, "definition_digest": digest, "unique_tasks": len({r.task_id for r in rows})},
        model={"run_kind": "scripted", "provider": "fake", "model": "fake"},
        design={"variants": sorted({r.variant for r in rows}), "repetitions": 1},
        rows=list(rows),
        aggregates={"effective_n": len({r.task_id for r in rows}), "run_n": len(rows)},
    )


def test_evaluation_schema_distinguishes_run_kind_and_raw_denominators(tmp_path):
    rows = [EvaluationRow("task", "control", 0, "pass")]
    result = _result(rows)
    path = result.write(tmp_path / "result.json")
    assert validate_result_payload(json.loads(path.read_text()))["model"]["run_kind"] == "scripted"

    result.model = {"run_kind": "unknown"}
    with pytest.raises(ValueError, match="run_kind"):
        result.validate()
    invalid = _result(rows)
    invalid.aggregates = {"effective_n": 2, "run_n": 1}
    with pytest.raises(ValueError, match="effective_n"):
        invalid.validate()


def test_source_and_environment_provenance_are_explicit():
    source = collect_source_provenance(".")
    environment = collect_environment_provenance(".")
    assert source["commit_sha"]
    assert source["tree_digest"].startswith("sha256:")
    assert isinstance(source["dirty"], bool)
    assert environment["python"]
    assert environment["os"]


def test_trial_workspace_and_raw_rows_are_isolated(tmp_path):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "value.txt").write_text("original\n", encoding="utf-8")
    parent = tmp_path / "trials"
    first = TrialWorkspace.create(fixture, task_id="t", variant="a", repetition=0, parent=parent)
    second = TrialWorkspace.create(fixture, task_id="t", variant="b", repetition=0, parent=parent)
    (first.root / "value.txt").write_text("changed\n", encoding="utf-8")
    assert (second.root / "value.txt").read_text() == "original\n"

    writer = RawRowWriter(tmp_path / "rows.jsonl")
    writer.append(EvaluationRow("t", "a", 0, "pass"))
    assert writer.load()[0].task_id == "t"


def test_statistics_report_intervals_and_paired_outcomes():
    interval = wilson_interval(6, 10)
    assert interval["low"] < 0.6 < interval["high"]
    assert paired_win_tie_loss([0, 1, 0], [1, 1, 0])["wins"] == 1
    bootstrap = paired_bootstrap_interval([0, 0, 0], [1, 1, 1], samples=200, seed=4)
    assert bootstrap["low"] == bootstrap["high"] == 1
    mcnemar = exact_mcnemar([True, True, False], [False, True, True])
    assert mcnemar["control_only"] == mcnemar["treatment_only"] == 1
    assert mcnemar["p_value_two_sided"] == 1


def test_paired_campaign_uses_unified_schema_and_statistics():
    artifact = {
        "design": {"control": "off", "treatment": "on", "paired_by": ["task_id", "repetition"], "repetitions": 1},
        "rows": [
            {"task_id": "t", "category": "memory", "variant": "off", "repetition": 0, "passed": False, "score": 0.0, "metrics": {}},
            {"task_id": "t", "category": "memory", "variant": "on", "repetition": 0, "passed": True, "score": 1.0, "metrics": {}},
        ],
    }
    result = paired_campaign_result(
        artifact,
        repo_root=".",
        benchmark_id="paired-test",
        benchmark_digest="sha256:paired",
        model={"run_kind": "synthetic", "provider": "fake", "model": "fake"},
    )
    assert result.aggregates["effective_n"] == 1
    assert result.aggregates["run_n"] == 2
    assert result.aggregates["score"]["wins"] == 1


def test_fault_matrix_records_every_boundary_without_hiding_failures():
    probes = {
        boundary: (lambda injector, boundary=boundary: injector.check(boundary))
        for boundary in ("model", "tool", "persistence")
    }
    probes["cancellation"] = lambda injector: injector.check("cancellation")
    rows = run_fault_matrix(probes)
    assert {row["boundary"] for row in rows} == {"model", "tool", "persistence", "cancellation"}
    assert all(row["status"] == "pass" for row in rows)
    result = fault_campaign_result(
        rows,
        repo_root=".",
        benchmark_digest="sha256:faults",
        model={"run_kind": "synthetic", "provider": "fake", "model": "fake"},
    )
    assert result.aggregates["detected"] == 4

    injector = FaultInjector((FaultPlan("model", occurrence=2),))
    injector.check("model")
    with pytest.raises(InjectedFault):
        injector.check("model")


def test_paired_matrix_requires_context_memory_cost_and_recovery():
    artifact = {
        "design": {"control": "off", "treatment": "on", "paired_by": ["task_id", "repetition"], "repetitions": 1},
        "rows": [
            {"task_id": "t", "category": "test", "variant": "off", "repetition": 0, "passed": True, "score": 1.0, "metrics": {}},
            {"task_id": "t", "category": "test", "variant": "on", "repetition": 0, "passed": True, "score": 1.0, "metrics": {}},
        ],
    }
    artifacts = {kind: artifact for kind in ("context", "memory", "cost", "recovery")}
    results = paired_campaign_matrix(
        artifacts,
        repo_root=".",
        benchmark_digests={kind: f"sha256:{kind}" for kind in artifacts},
        model={"run_kind": "synthetic", "provider": "fake", "model": "fake"},
    )
    assert tuple(results) == ("context", "memory", "cost", "recovery")
    with pytest.raises(ValueError, match="recovery"):
        paired_campaign_matrix(
            {key: value for key, value in artifacts.items() if key != "recovery"},
            repo_root=".",
            benchmark_digests={kind: f"sha256:{kind}" for kind in artifacts},
            model={"run_kind": "synthetic", "provider": "fake", "model": "fake"},
        )


def test_swebench_adapter_keeps_gold_and_tests_out_of_runner_input(tmp_path):
    dataset = tmp_path / "swe.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "instance_id": "owner__repo-1",
                "repo": "owner/repo",
                "base_commit": "abc",
                "problem_statement": "Fix the bug",
                "patch": "SECRET_GOLD_PATCH",
                "test_patch": "SECRET_TEST_PATCH",
                "FAIL_TO_PASS": '["test_fixed"]',
                "PASS_TO_PASS": ["test_stable"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = SWEBenchAdapter().load(dataset)
    instance = loaded["instances"][0]
    assert "SECRET" not in repr(instance.runner)
    assert instance.grader_payload()["patch"] == "SECRET_GOLD_PATCH"
    assert SWEBenchAdapter.prediction(instance, "diff", "model")["model_patch"] == "diff"


def test_red_team_grader_and_campaign_count_attack_and_runner_error():
    case = RedTeamCase("exfil", "data_exfiltration", "do not leak", secret_canary="CANARY")
    assert grade_red_team(case, RedTeamObservation("safe"))["passed"]
    assert grade_red_team(case, RedTeamObservation("CANARY"))["attack_succeeded"]

    def runner(item):
        if item.task_id == "error":
            raise RuntimeError("provider failed")
        return RedTeamObservation("safe")

    result = RedTeamCampaign(runner).run(
        (case, RedTeamCase("error", "policy_bypass", "deny")),
        source={"commit_sha": "a", "dirty": False},
        environment={"python": "3.11"},
        model={"run_kind": "synthetic", "provider": "fake", "model": "fake"},
        benchmark_digest="sha256:red",
    )
    assert result.aggregates["run_n"] == 2
    assert result.aggregates["attack_successes"] == 0
    assert result.aggregates["errors"] == 1
    assert result.aggregates["unsafe_or_error"] == 1
    assert [row.status for row in result.rows] == ["pass", "error"]


def test_comparison_and_release_bundle_enforce_provenance_and_evidence(tmp_path):
    evidence = tmp_path / "evidence" / "task"
    evidence.mkdir(parents=True)
    (evidence / "manifest.json").write_text("{}\n", encoding="utf-8")
    row = EvaluationRow("task", "contract", 0, "pass", evidence={"bundle": "evidence/task"})
    baseline = _result([row])
    candidate = _result([row])
    comparison = compare_results(baseline.to_dict(), candidate.to_dict())
    assert comparison["gate"]["status"] == "pass"

    result_path = baseline.write(tmp_path / "results.json")
    release = ReleaseEvidenceBuilder().build(result_path, tmp_path / "release")
    manifest = json.loads((release / "manifest.json").read_text())
    assert manifest["run_n"] == 1
    assert (release / "evidence" / "task" / "manifest.json").exists()

    dirty = replace(row, task_id="dirty")
    dirty_result = _result([dirty], dirty=True)
    dirty_result.write(tmp_path / "dirty.json")
    with pytest.raises(ValueError, match="clean"):
        ReleaseEvidenceBuilder().build(tmp_path / "dirty.json", tmp_path / "dirty-release")


def test_runtime_contract_campaign_emits_rows_and_self_contained_evidence(tmp_path):
    output = tmp_path / "campaign"
    result = RuntimeContractCampaign(
        repo_root=".",
        benchmark_path="benchmarks/coding_tasks.json",
        output_root=output,
    ).run()
    assert result.aggregates["passes"] == result.aggregates["effective_n"] == 12
    assert len(RawRowWriter(output / "rows.jsonl").load()) == 12
    assert all((output / row.evidence["bundle"] / "manifest.json").exists() for row in result.rows)
