import json

import pytest

from repoagent.evolver.activation import ActivationError
from repoagent.evolver.gates import GateDecision, GateObservation
from repoagent.evolver.ledger import EvolutionLedger, LedgerIntegrityError
from repoagent.evolver.orchestrator import ControlledEvolver

from test_evolver_contracts import _proposal


def _decision(stage, passed=True, digest_character="1"):
    return GateDecision(
        stage,
        passed,
        "sha256:" + digest_character * 64,
        (GateObservation("check", "pass" if passed else "fail"),),
    )


def _ready_candidate(evolver, proposal, *, commit_character="b"):
    candidate_id = proposal.manifest.candidate_id
    evolver.record_candidate(proposal)
    evolver.record_materialized(
        candidate_id,
        {
            "candidate_id": candidate_id,
            "base_commit": proposal.manifest.base_commit,
            "commit_sha": commit_character * 40,
            "tree_sha": "c" * 40,
            "patch_digest": proposal.manifest.patch_digest,
        },
    )
    evolver.record_gate(candidate_id, _decision("deterministic", digest_character="2"))
    paired = _decision("paired", digest_character="3")
    evolver.record_gate(candidate_id, paired)
    token = evolver.approvals.request(candidate_id, paired.evidence_digest)
    evolver.approvals.confirm(token, actor="human:reviewer")
    return candidate_id


def test_ledger_hash_chain_detects_historical_tampering(tmp_path):
    ledger = EvolutionLedger(tmp_path / "evolution.jsonl")
    ledger.append("audit.started", actor="test", candidate_id="candidate_x")
    ledger.append("audit.finished", actor="test", candidate_id="candidate_x")
    assert ledger.verify() is True

    rows = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    rows[0]["actor"] = "forged"
    ledger.path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    with pytest.raises(LedgerIntegrityError, match="digest"):
        ledger.events()


def test_ledger_public_append_cannot_forge_protocol_events(tmp_path):
    ledger = EvolutionLedger(tmp_path / "evolution.jsonl")
    with pytest.raises(PermissionError, match="ControlledEvolver"):
        ledger.append(
            "approval.confirmed",
            actor="fake-human",
            candidate_id="candidate_x",
            payload={"evidence_digest": "sha256:" + "1" * 64},
        )


def test_activation_requires_latest_gates_human_approval_and_manifest_identity(tmp_path):
    evolver = ControlledEvolver(EvolutionLedger(tmp_path / "ledger.jsonl"))
    proposal = _proposal()
    evolver.record_candidate(proposal)
    with pytest.raises(ActivationError, match="requires"):
        evolver.activations.activate("prompt", proposal.manifest.candidate_id, actor="ops")

    candidate_id = _ready_candidate(
        ControlledEvolver(EvolutionLedger(tmp_path / "ready.jsonl")), proposal
    )
    ready = ControlledEvolver(EvolutionLedger(tmp_path / "ready.jsonl"))
    ready.record_gate(candidate_id, _decision("paired", False, "4"))
    with pytest.raises(ActivationError, match="latest paired"):
        ready.activations.activate("prompt", candidate_id, actor="ops")


def test_approval_token_is_single_use_and_wrong_token_fails(tmp_path):
    evolver = ControlledEvolver(EvolutionLedger(tmp_path / "ledger.jsonl"))
    token = evolver.approvals.request("candidate_x", "sha256:" + "1" * 64)
    with pytest.raises(ActivationError, match="invalid"):
        evolver.approvals.confirm("wrong", actor="human")
    evolver.approvals.confirm(token, actor="human")
    with pytest.raises(ActivationError, match="already consumed"):
        evolver.approvals.confirm(token, actor="human")


def test_activation_routes_approved_candidate_and_rollback_restores_previous(tmp_path):
    evolver = ControlledEvolver(EvolutionLedger(tmp_path / "ledger.jsonl"))
    first = _proposal(content=b"first\n")
    first_id = _ready_candidate(evolver, first, commit_character="a")
    evolver.activations.activate("prompt", first_id, actor="ops")
    assert evolver.activations.resolve("prompt").candidate_id == first_id

    second = _proposal(content=b"second\n")
    second_id = _ready_candidate(evolver, second, commit_character="b")
    evolver.activations.activate("prompt", second_id, actor="ops")
    assert evolver.activations.resolve("prompt").candidate_id == second_id

    before = len(evolver.ledger.events())
    evolver.activations.rollback("prompt", actor="ops")
    assert len(evolver.ledger.events()) == before + 1
    assert evolver.activations.resolve("prompt").candidate_id == first_id
