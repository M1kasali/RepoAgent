import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from repoagent.evolver.evaluation import CandidateEvaluationError, payload_digest
from repoagent.evolver.ledger import EvolutionLedger
from repoagent.evolver.model_budget import EvaluationModelLimits
from repoagent.evolver.pilot_protocol import freeze_pilot
from repoagent.evolver.pilot_runner import pilot_model_identity, run_frozen_pilot
from repoagent.pricing import ModelPricing
from repoagent.providers.base import ModelRequest
from test_coding_pilot_protocol import pilot as pilot
from test_evolver_model_budget import LeafClient, _client
from test_evolver_agent_snapshot_live import source_repository as source_repository


@pytest.fixture
def ready(pilot, tmp_path, monkeypatch):
    root, config = pilot
    leaves, calls = [], []
    behavior = {"defect": None, "candidate_passes": True, "baseline_passes": False}

    def factory():
        leaf = LeafClient()
        leaves.append(leaf)
        return _client(
            leaf,
            limits=EvaluationModelLimits(**config["limits"]),
            pricing=ModelPricing(**config["pricing"]),
        )

    config["model"] = pilot_model_identity(factory())
    leaves.clear()
    source = tmp_path / "config.json"
    source.write_text(json.dumps(config))
    frozen = tmp_path / "frozen"
    preflight = freeze_pilot(source, repo_root=root, output_root=frozen)

    class Evaluator:
        def __init__(self, tasks, *, client_factory, model_descriptor, image, **kwargs):
            self.factory, self.model, self.image = (
                client_factory,
                model_descriptor,
                image,
            )
            self.tasks = {t.task_id: t for t in tasks}

        def descriptor(self, repo):
            return {
                "image_id": self.image if behavior["defect"] != "image" else "wrong",
                "model_gateway": self.model,
            }

        def run_trial(
            self, repo, identity, check, repetition, descriptor, *, cost_limit_usd
        ):
            calls.append((check.check_id, identity["commit_sha"], cost_limit_usd))
            client = self.factory()
            client.generate(ModelRequest("fixture", 20))
            if behavior["defect"] == "interrupt":
                raise KeyboardInterrupt()
            cost = client.evidence()["known_estimated_cost_usd"]
            passed = (
                behavior["baseline_passes"]
                if identity["commit_sha"] == config["baseline_commit"]
                else behavior["candidate_passes"]
            )
            result = {
                "status": "completed",
                "passed": passed,
                "estimated_cost_usd": cost,
                "raw": {
                    "source": identity,
                    "task": self.tasks[check.check_id].descriptor(),
                },
            }
            if behavior["defect"] == "missing_cost":
                result["estimated_cost_usd"] = None
            elif behavior["defect"] == "over_cap":
                result["estimated_cost_usd"] = 2
            elif behavior["defect"] == "infrastructure":
                result["status"] = "infrastructure_error"
                result["passed"] = None
            elif behavior["defect"] == "identity":
                result["raw"]["source"] = {}
            elif behavior["defect"] == "drift":
                changed = json.loads((frozen / "private-config.json").read_text())
                changed["tasks"][0]["prompt"] += " drift"
                (frozen / "private-config.json").write_text(json.dumps(changed))
            return result

    monkeypatch.setattr(
        "repoagent.evolver.pilot_runner.HostedAgentSnapshotEvaluator", Evaluator
    )
    options = dict(
        repo_root=root,
        frozen_root=frozen,
        client_factory=factory,
        approved_preflight_digest=payload_digest(preflight),
        actor="test-operator",
    )
    return options, leaves, calls, behavior, config


def test_pilot_full_matrix_is_ordered_priced_and_one_shot(ready):
    options, leaves, calls, _, config = ready
    result = run_frozen_pilot(**options)
    assert result["status"] == "completed"
    assert result["completed_trials"] == result["attempted_trials"] == 48
    assert result["cost_complete"] is True
    assert float(result["known_estimated_cost_usd"]) == pytest.approx(48 * 0.00002)
    assert result["automatic_promotion"] is False
    assert all(
        s["wins"] == 12 and s["ties"] == s["losses"] == 0
        for s in result["comparison"].values()
    )
    assert [c[1] for c in calls[:4]] == [
        config["baseline_commit"],
        config["candidate_commit"],
        config["candidate_commit"],
        config["baseline_commit"],
    ]
    assert len(leaves) == 48 and all(len(leaf.requests) == 1 for leaf in leaves)
    root = options["frozen_root"] / "execution"
    events = EvolutionLedger(root / "ledger.jsonl").events()
    assert sum(e["event_type"] == "pilot.trial_started" for e in events) == 48
    for event in events:
        if event["event_type"] == "pilot.trial_completed":
            row = json.loads(
                (root / f"trial-{event['payload']['index']:03d}.json").read_text()
            )
            assert payload_digest(row) == event["payload"]["receipt_digest"]
    with pytest.raises(FileExistsError):
        run_frozen_pilot(**options)
    assert len(leaves) == 48


@pytest.mark.parametrize(
    "baseline,candidate,field", [(True, True, "ties"), (True, False, "losses")]
)
def test_pilot_reports_ties_and_regressions_without_promoting(
    ready, baseline, candidate, field
):
    options, _, _, behavior, _ = ready
    behavior.update(baseline_passes=baseline, candidate_passes=candidate)
    result = run_frozen_pilot(**options)
    assert result["status"] == "completed"
    assert all(s[field] == 12 for s in result["comparison"].values())
    assert result["automatic_promotion"] is False


def test_wrong_acknowledgement_never_constructs_gateway(ready):
    options, leaves, _, _, _ = ready
    options["approved_preflight_digest"] = "wrong"
    with pytest.raises(PermissionError):
        run_frozen_pilot(**options)
    assert leaves == []
    assert not (options["frozen_root"] / "execution").exists()


def test_concurrent_attempt_is_rejected_before_factory(ready):
    from repoagent.atomic_io import LockUnavailableError, file_lock

    options, leaves, _, _, _ = ready
    with file_lock(options["frozen_root"] / ".execution.lock"):
        with pytest.raises(LockUnavailableError):
            run_frozen_pilot(**options)
    assert leaves == []
    assert not (options["frozen_root"] / "execution").exists()


def test_used_gateway_cannot_send_a_second_trial(ready):
    options, _, _, _, _ = ready
    client = options["client_factory"]()
    options["client_factory"] = lambda: client
    with pytest.raises(CandidateEvaluationError, match="already used"):
        run_frozen_pilot(**options)
    assert client.evidence()["calls_reserved"] == 1
    summary = json.loads(
        (options["frozen_root"] / "execution/summary.json").read_text()
    )
    assert summary["status"] == "needs_review"
    assert summary["completed_trials"] == 1


@pytest.mark.parametrize(
    "defect",
    [
        "missing_cost",
        "over_cap",
        "infrastructure",
        "identity",
        "interrupt",
        "image",
        "drift",
    ],
)
def test_uncertain_run_stops_and_cannot_retry(ready, defect):
    options, leaves, calls, behavior, _ = ready
    behavior["defect"] = defect
    with pytest.raises((CandidateEvaluationError, KeyboardInterrupt, ValueError)):
        run_frozen_pilot(**options)
    assert len(calls) == (0 if defect == "image" else 1)
    summary = json.loads(
        (options["frozen_root"] / "execution/summary.json").read_text()
    )
    assert summary["status"] == "needs_review"
    assert summary["cost_complete"] is False
    assert "comparison" not in summary
    before = len(leaves)
    with pytest.raises((FileExistsError, ValueError)):
        run_frozen_pilot(**options)
    assert len(leaves) == before


def test_model_identity_mismatch_blocks_inference(ready):
    options, leaves, calls, _, _ = ready
    options["client_factory"] = lambda: _client()
    with pytest.raises(CandidateEvaluationError, match="model configuration"):
        run_frozen_pilot(**options)
    assert calls == leaves == []


def test_budget_and_pricing_cannot_hide_behind_matching_model_digest(ready):
    options, _, calls, _, _ = ready
    frozen = options["frozen_root"]
    config = json.loads((frozen / "private-config.json").read_text())
    client = _client()
    config["model"] = pilot_model_identity(client)
    source = frozen.parent / "mismatch.json"
    source.write_text(json.dumps(config))
    new_root = frozen.parent / "mismatch"
    result = freeze_pilot(source, repo_root=options["repo_root"], output_root=new_root)
    options.update(
        frozen_root=new_root,
        client_factory=lambda: client,
        approved_preflight_digest=payload_digest(result),
    )
    with pytest.raises(CandidateEvaluationError, match="limits or pricing"):
        run_frozen_pilot(**options)
    assert not calls


def test_cli_verifies_approval_before_importing_factory(ready):
    options, _, _, _, _ = ready
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts/run_coding_pilot.py"),
            "--repo",
            str(options["repo_root"]),
            "--frozen",
            str(options["frozen_root"]),
            "--factory",
            "missing_module_should_not_import:factory",
            "--actor",
            "test",
            "--approve-preflight-digest",
            "wrong",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "PermissionError" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr


@pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"),
    reason="opt-in hosted pilot Docker matrix",
)
def test_real_hosted_pilot_runs_all_pairs_and_preserves_journals(
    source_repository, pilot, tmp_path
):
    root, proposal, evolver = source_repository
    _, config = pilot
    identity = evolver.materialize_candidate(root, proposal)
    config["baseline_commit"] = proposal.manifest.base_commit
    config["candidate_commit"] = identity["commit_sha"]
    from repoagent.evolver.evaluation import DockerCandidateEvaluator

    config["image_id"] = DockerCandidateEvaluator(
        executable=os.environ["REPOAGENT_TEST_DOCKER"]
    ).descriptor(root)["image_id"]
    for task in config["tasks"]:
        task["behavior_checks"] = [
            {
                "check_id": "answer",
                "program": "import sys,json;sys.path.insert(0,'.');from solution import solve;print(json.dumps(solve()))",
                "expected_json": "42",
            }
        ]
    leaves = []

    def factory():
        leaf = LeafClient(
            outputs=[
                "<tool>"
                + json.dumps(
                    {
                        "name": "write_file",
                        "args": {
                            "path": "solution.py",
                            "content": "def solve(): return 42\n",
                        },
                    }
                )
                + "</tool>",
                "<final>Done.</final>",
            ]
        )
        leaves.append(leaf)
        return _client(
            leaf,
            limits=EvaluationModelLimits(**config["limits"]),
            pricing=ModelPricing(**config["pricing"]),
        )

    config["model"] = pilot_model_identity(factory())
    leaves.clear()
    source = tmp_path / "live-config.json"
    source.write_text(json.dumps(config))
    frozen = tmp_path / "live-frozen"
    receipt = freeze_pilot(source, repo_root=root, output_root=frozen)
    options = dict(
        repo_root=root,
        frozen_root=frozen,
        client_factory=factory,
        approved_preflight_digest=payload_digest(receipt),
        actor="fixture-operator",
        executable=os.environ["REPOAGENT_TEST_DOCKER"],
    )
    result = run_frozen_pilot(**options)
    assert result["completed_trials"] == 48
    assert all(row["ties"] == 12 for row in result["comparison"].values())
    assert len(leaves) == 48
    assert sum(len(leaf.requests) for leaf in leaves) == 96
    assert float(result["known_estimated_cost_usd"]) == pytest.approx(96 * 0.00002)
    journals = list((frozen / "execution/model-journals").glob("*/calls.jsonl"))
    assert len(journals) == 48
    assert all(EvolutionLedger(path).verify() for path in journals)
    with pytest.raises(FileExistsError):
        run_frozen_pilot(**options)
    assert len(leaves) == 48
    assert not (root / "solution.py").exists()
