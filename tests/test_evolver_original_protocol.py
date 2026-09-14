from dataclasses import replace
import json

import pytest

from repoagent.evolver.module_repair import ModuleRepairError, ModuleRepairProtocol
from repoagent.evolver.focused_fisher import FocusedFisherGate, TaskTrialSummary, fisher_one_sided
from test_evolver_benchmark_target import benchmark_repo, TARGET
from test_evolver_contracts import _evidence
from test_evolver_model_budget import LeafClient, _client


def repair():
    return ModuleRepairProtocol(TARGET, ("solve",), "missing-rule", "Restore the required rule.",
        json.dumps({"task-1": {"description": "a training case", "cases": [
            {"fn": "solve", "args": [2], "actual": 0, "expect": 2}]}}))


def model(benchmark_repo, tmp_path, leaf, **options):
    from repoagent.evolver import ModelCandidateProposer
    root, target = benchmark_repo
    return ModelCandidateProposer(
        root, base_commit=target.base_commit, label="benchmark", paths=[TARGET],
        benchmark_target=target, evidence=[_evidence()], client=_client(leaf),
        journal_directory=tmp_path / "model", repair_protocol=repair(), **options)


def test_repair_uses_structured_messages_and_bounded_parse_retry(benchmark_repo, tmp_path):
    leaf = LeafClient(outputs=["bad", "```python\ndef solve(x): return x\n```"])
    proposer = model(benchmark_repo, tmp_path, leaf)
    candidate = proposer({"base_commit": proposer.base})
    assert candidate.content[TARGET] == b"def solve(x): return x\n"
    first, second = leaf.requests
    assert [m.role for m in first.messages] == ["system", "user"]
    assert [m.role for m in second.messages] == ["system", "user", "assistant", "user"]
    assert second.messages[-2].content == "bad"
    assert "exactly one fenced" in second.messages[-1].content
    assert first.prompt == first.messages[-1].content
    assert proposer.client.evidence()["calls_reserved"] == 2
    assert proposer.descriptor()["repair_protocol"] == repair().descriptor()


def test_parse_repairs_do_not_bypass_gateway_call_limit(benchmark_repo, tmp_path):
    from repoagent.evolver.model_proxy import ModelProxyError
    leaf = LeafClient(outputs=["bad"])
    proposer = model(benchmark_repo, tmp_path, leaf)
    with pytest.raises(ModelProxyError):
        proposer({"base_commit": proposer.base})
    assert len(leaf.requests) == 2
    assert not any(e["payload"].get("status") == "candidate_generated" for e in proposer.journal.ledger.events())


def test_parser_exhausts_exactly_three_attempts(benchmark_repo, tmp_path):
    from repoagent.evolver import EvaluationModelLimits
    leaf = LeafClient(outputs=["bad"])
    proposer = model(benchmark_repo, tmp_path, leaf)
    client = _client(leaf, limits=EvaluationModelLimits(max_calls=5, max_input_tokens=100, max_output_tokens=20))
    from repoagent.evolver.model_proxy import HostModelProxy
    proposer.client = client
    proposer.proxy = HostModelProxy(client, evidence_sink=proposer.journal)
    with pytest.raises(ModuleRepairError):
        proposer({"base_commit": proposer.base})
    assert len(leaf.requests) == 3
    assert [len(request.messages) for request in leaf.requests] == [2, 4, 6]


@pytest.mark.parametrize("text", ["nothing", "```python\n\n```", "```python\nx=1\n```",
                                  "```python\ninvalid python !!\n```", "```\na\n```\n```\nb\n```"])
def test_module_parser_rejects_malformed_candidates(text):
    with pytest.raises(ModuleRepairError):
        repair().parse(text)


def test_parser_matches_original_acceptance_of_prose_but_not_execution():
    source = "import os\ndef solve(x): return x\n"
    assert repair().parse("prose\n```python\n" + source + "```\nprose")[TARGET] == source
    # Imports are a prompt/grader constraint, not an AST-parser sandbox.
    with pytest.raises(ModuleRepairError, match="byte cap"):
        replace(repair(), max_bytes=2).parse("```python\n" + source + "```")


def test_repair_scope_cannot_include_unrelated_training_evidence(benchmark_repo, tmp_path):
    from repoagent.evolver import ModelCandidateProposer
    root, target = benchmark_repo
    protocol = replace(repair(), training_failures_json=json.dumps({"unlisted": {
        "description": "hidden", "cases": [{"detail": "not training evidence"}]}}))
    with pytest.raises(ValueError, match="training evidence"):
        ModelCandidateProposer(root, base_commit=target.base_commit, label="benchmark",
            paths=[TARGET], benchmark_target=target, evidence=[_evidence()],
            client=_client(), journal_directory=tmp_path / "model", repair_protocol=protocol)


def rows(counts, k=2):
    return {tid: TaskTrialSummary(tid, count, k) for tid, count in counts.items()}


def decide(base, probe, confirm, *, focused=("f",), sentinels=("s",), **options):
    calls = []
    def evaluate(ids, k, phase):
        calls.append((tuple(ids), k, phase))
        values = probe if phase == "focused" else confirm
        return {tid: values[tid] for tid in ids if tid in values}
    result = FocusedFisherGate(k=2).decide(baseline=base, train_ids=list(base),
        focused_ids=focused, sentinel_ids=sentinels, evaluate=evaluate, **options)
    return result, calls


def test_fisher_wide_pass_advances_unchanged_probe_but_ties_do_not_promote():
    base = rows({"f": 0, "s": 2})
    result, calls = decide(base, base, base)
    assert [c[2] for c in calls] == ["focused", "confirm"]
    assert result["stats"]["fisher_p"] == 1.0
    assert result["verdict"] == "rejected"


def test_stable_sentinel_tolerates_one_loss_not_two():
    base = rows({"f": 0, "s": 2})
    for passes, phase in [(1, "confirm"), (0, "screen")]:
        result, calls = decide(base, rows({"f": 2, "s": passes}), base)
        assert result["phase"] == phase
        assert len(calls) == (2 if phase == "confirm" else 1)


def test_promotion_and_two_sigma_credit_are_separate_and_per_task():
    base = rows({str(i): 0 for i in range(10)})
    candidate = rows({str(i): 2 if i < 2 else 0 for i in range(10)})
    result, _ = decide(base, candidate, candidate, focused=("0", "1"), sentinels=())
    assert result["promoted"]
    assert result["paired"]["mean_lift"] == pytest.approx(.2)
    assert result["paired"]["z"] == pytest.approx(1.5)
    assert not result["paired"]["credited_2sigma"]


def test_measurement_failure_precedes_attribution_and_stops_credit():
    base = rows({"f": 0, "s": 2, "other": 0})
    candidate = rows({"f": 2, "s": 2, "other": 0})
    candidate["other"] = TaskTrialSummary("other", 0, 2, failure="provider")
    result, _ = decide(base, candidate, candidate, fired_tasks={"f"})
    assert result["verdict"] == "failed" and not result["promoted"]


def test_positive_attributed_lift_cannot_hide_full_train_regression():
    base = rows({"f": 0, "s": 2, "other": 2})
    candidate = rows({"f": 1, "s": 0, "other": 0})
    result, _ = decide(base, base, candidate, fired_tasks={"f"})
    assert result["paired"]["promoted"] and not result["promoted"]
    assert result["stats"]["full_lift"] < 0


def test_fisher_known_table_and_degenerate_case():
    assert fisher_one_sided(4, 0, 0, 4) == pytest.approx(1 / 70)
    assert fisher_one_sided(0, 0, 0, 0) == 1


def test_significantly_worse_probe_skips_confirmation():
    base = rows({str(i): 2 for i in range(5)})
    probe = rows({str(i): 0 for i in range(5)})
    result, calls = decide(base, probe, probe, focused=tuple(base), sentinels=())
    assert result["phase"] == "screen" and len(calls) == 1
    assert result["stats"]["pruned_significantly_worse"]


def test_fragile_sentinels_use_worse_fisher_not_stable_guard():
    base = rows({str(i): 1 for i in range(10)})
    probe = rows({str(i): 0 for i in range(10)})
    result, calls = decide(base, probe, probe, focused=(), sentinels=tuple(base))
    assert result["phase"] == "screen" and len(calls) == 1
    assert result["stats"]["sentinel_regression"]
    assert "sentinel_guard" not in result["stats"]


def test_missing_probe_is_inconclusive_not_a_zero_score():
    base = rows({"f": 0, "s": 2})
    result, calls = decide(base, {}, base)
    assert result["verdict"] == "inconclusive" and len(calls) == 1


def test_probe_scope_rejected_before_scoring():
    with pytest.raises(ValueError, match="training"):
        FocusedFisherGate().decide(baseline={}, train_ids=["train"], focused_ids=["test"],
            sentinel_ids=[], evaluate=lambda *args: pytest.fail("must not score"))


def test_native_receipt_replays_without_rescoring(benchmark_repo, tmp_path):
    from repoagent.evolver import ControlledEvolver, EvolutionLedger, CandidateCheck
    from repoagent.evolver.focused_fisher import FocusedBenchmarkEvaluator
    from test_evolver_benchmark_target import proposal
    root, target = benchmark_repo
    class Backend:
        calls = []
        def descriptor(self, root):
            return {"kind": "fixture"}
        def score(self, root, identity, ids, k, phase):
            self.calls.append(phase)
            return rows({tid: 0 if phase == "baseline" else 2 for tid in ids})
    backend = Backend()
    evaluator = FocusedBenchmarkEvaluator(target=target, backend=backend,
        train_ids=["f"], focused_ids=["f"], sentinel_ids=[], gate=FocusedFisherGate(k=2))
    evolver = ControlledEvolver(EvolutionLedger(tmp_path / "ledger.jsonl"))
    candidate = proposal(target)
    checks = [CandidateCheck("focused_fisher", "focused training evaluation")]
    result = evolver.evaluate_candidate(root, candidate, checks, evaluator)
    assert result.passed and backend.calls == ["baseline", "focused", "confirm"]
    assert evolver.evaluate_candidate(root, candidate, checks, evaluator) == result
    assert len(backend.calls) == 3
    artifact = tmp_path / "evidence" / f"{candidate.manifest.candidate_id}-deterministic.json"
    receipt = json.loads(artifact.read_text())
    assert receipt["results"][0]["result"]["paired"]["z"] == "inf"
    assert receipt["results"][0]["result"]["paired"]["credited_2sigma"]
    evaluator.gate = FocusedFisherGate(k=2, alpha=.01)
    from repoagent.evolver import CandidateEvaluationError
    with pytest.raises(CandidateEvaluationError, match="plan changed"):
        evolver.evaluate_candidate(root, candidate, checks, evaluator)
