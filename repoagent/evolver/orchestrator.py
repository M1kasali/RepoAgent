"""Narrow orchestration facade for recording candidate promotion evidence."""

from __future__ import annotations

from .activation import ActivationRegistry, ApprovalBroker
from .contracts import CandidateProposal
from .gates import GateDecision
from .ledger import EvolutionLedger, _PROTOCOL_AUTHORITY


class ControlledEvolver:
    def __init__(self, ledger):
        if not isinstance(ledger, EvolutionLedger):
            raise TypeError("controlled evolver requires EvolutionLedger")
        self.ledger = ledger
        self.approvals = ApprovalBroker(ledger)
        self.activations = ActivationRegistry(ledger)

    def record_candidate(self, proposal, *, actor="evolver"):
        if not isinstance(proposal, CandidateProposal):
            raise TypeError("record_candidate requires CandidateProposal")
        return self.ledger._append_protocol(
            "candidate.created",
            actor=actor,
            candidate_id=proposal.manifest.candidate_id,
            payload={"manifest": proposal.manifest.to_dict()},
            authority=_PROTOCOL_AUTHORITY,
        )

    def record_materialized(self, candidate_id, identity, *, actor="evolver"):
        required = {"candidate_id", "base_commit", "commit_sha", "tree_sha", "patch_digest"}
        identity = dict(identity)
        if set(identity) != required or identity["candidate_id"] != candidate_id:
            raise ValueError("candidate materialization identity is incomplete or mismatched")
        return self.ledger._append_protocol(
            "candidate.materialized",
            actor=actor,
            candidate_id=candidate_id,
            payload=identity,
            authority=_PROTOCOL_AUTHORITY,
        )

    def record_gate(self, candidate_id, decision, *, actor="evaluator"):
        if not isinstance(decision, GateDecision):
            raise TypeError("record_gate requires GateDecision")
        return self.ledger._append_protocol(
            "gate.evaluated",
            actor=actor,
            candidate_id=candidate_id,
            payload=decision.to_dict(),
            authority=_PROTOCOL_AUTHORITY,
        )


__all__ = ["ControlledEvolver"]
