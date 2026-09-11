"""Narrow orchestration facade for recording candidate promotion evidence."""

from __future__ import annotations

from .activation import ActivationRegistry, ApprovalBroker
from .contracts import CandidateProposal
from .gates import GateDecision
from .ledger import EvolutionLedger, _PROTOCOL_AUTHORITY
from .workspace import GitCandidateWorkspace, CandidateWorkspaceError, candidate_ref, verify_candidate_commit, _git
from ..atomic_io import atomic_replace_unlocked, file_lock
from .evaluation import CandidateEvaluationError, evaluation_plan, payload_digest, decision_from_receipt
import json


class ControlledEvolver:
    def __init__(self, ledger):
        if not isinstance(ledger, EvolutionLedger):
            raise TypeError("controlled evolver requires EvolutionLedger")
        self.ledger = ledger
        self.approvals = ApprovalBroker(ledger)
        self.activations = ActivationRegistry(ledger)

    def materialize_candidate(self, repo_root, proposal, *, actor="evolver"):
        """Resume a prepared candidate without regenerating or approving it."""
        if not isinstance(proposal, CandidateProposal):
            raise TypeError("materialize_candidate requires CandidateProposal")
        candidate_id = proposal.manifest.candidate_id
        ref = candidate_ref(candidate_id)
        with file_lock(self.ledger.path.parent / ".lock" / "materialization.lock"):
            events = [event for event in self.ledger.events() if event["candidate_id"] == candidate_id]
            created = [event for event in events if event["event_type"] == "candidate.created"]
            if created:
                if len(created) != 1 or created[0]["payload"]["manifest"] != proposal.manifest.to_dict():
                    raise CandidateWorkspaceError("candidate id is already bound to another manifest")
            else:
                self.record_candidate(proposal, actor=actor)
            recorded = [event for event in events if event["event_type"] == "candidate.materialized"]
            pinned = _git(repo_root, "rev-parse", "--verify", "--quiet", ref, check=False)
            if recorded:
                if len(recorded) != 1 or pinned != recorded[0]["payload"]["commit_sha"]:
                    raise CandidateWorkspaceError("materialized candidate reference is missing or changed")
                identity = verify_candidate_commit(repo_root, proposal, pinned)
                if identity != recorded[0]["payload"]:
                    raise CandidateWorkspaceError("materialized candidate identity drifted")
                return identity
            if pinned:
                identity = verify_candidate_commit(repo_root, proposal, pinned)
            else:
                with GitCandidateWorkspace(repo_root, proposal) as workspace:
                    identity = workspace.finalize()
            self.record_materialized(candidate_id, identity, actor=actor)
            return identity

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

    def evaluate_candidate(self, repo_root, proposal, checks, evaluator, *, actor="evaluator"):
        """Record real checks once; uncertain interrupted attempts never rerun silently."""
        checks = tuple(checks)
        evaluation_plan({}, checks, {})
        identity = self.materialize_candidate(repo_root, proposal)
        plan = evaluation_plan(identity, checks, evaluator.descriptor(repo_root))
        plan_digest = payload_digest(plan)
        candidate_id = proposal.manifest.candidate_id
        with file_lock(self.ledger.path.parent / ".lock" / "evaluation.lock"):
            events = [event for event in self.ledger.events() if event["candidate_id"] == candidate_id]
            started = [event for event in events if event["event_type"] == "evaluation.started"]
            completed = [event for event in events if event["event_type"] == "evaluation.completed"]
            artifact = self.ledger.path.parent / "evidence" / f"{candidate_id}-deterministic.json"
            if started and (len(started) != 1 or started[0]["payload"]["plan_digest"] != plan_digest or started[0]["payload"].get("plan") != plan):
                raise CandidateEvaluationError("candidate evaluation plan changed")
            if completed:
                if not started or len(completed) != 1 or completed[0]["payload"].get("plan_digest") != plan_digest or completed[0]["sequence"] <= started[0]["sequence"]:
                    raise CandidateEvaluationError("candidate evaluation journal is inconsistent")
                receipt = json.loads(artifact.read_text(encoding="utf-8"))
                if receipt["plan"] != plan or payload_digest(receipt) != completed[0]["payload"]["receipt_digest"]:
                    raise CandidateEvaluationError("candidate evaluation evidence changed")
            elif started:
                raise CandidateEvaluationError("candidate evaluation was interrupted; automatic rerun refused")
            else:
                self.ledger._append_protocol("evaluation.started", actor=actor, candidate_id=candidate_id,
                    payload={"plan_digest": plan_digest, "plan": plan}, authority=_PROTOCOL_AUTHORITY)
                try:
                    results = evaluator.evaluate(repo_root, identity, checks, plan["evaluator"])
                    verify_candidate_commit(repo_root, proposal, identity["commit_sha"])
                    if _git(repo_root, "rev-parse", candidate_ref(candidate_id)) != identity["commit_sha"]:
                        raise CandidateEvaluationError("candidate reference changed during evaluation")
                except Exception as exc:
                    self.ledger._append_protocol("evaluation.failed", actor=actor, candidate_id=candidate_id,
                        payload={"plan_digest": plan_digest, "error_type": type(exc).__name__}, authority=_PROTOCOL_AUTHORITY)
                    raise
                receipt = {"schema": "repoagent.candidate-check-receipt/v1", "plan": plan, "results": results}
                decision_from_receipt(receipt)
                atomic_replace_unlocked(artifact, json.dumps(receipt, sort_keys=True, indent=2, allow_nan=False) + "\n")
                self.ledger._append_protocol("evaluation.completed", actor=actor, candidate_id=candidate_id,
                    payload={"plan_digest": plan_digest, "receipt_digest": payload_digest(receipt)}, authority=_PROTOCOL_AUTHORITY)
            decision = decision_from_receipt(receipt)
            gates = [event for event in events if event["event_type"] == "gate.evaluated" and event["payload"].get("stage") == "deterministic"]
            if gates:
                if len(gates) != 1 or gates[0]["payload"] != decision.to_dict():
                    raise CandidateEvaluationError("candidate deterministic gate evidence differs")
            else:
                self.record_gate(candidate_id, decision, actor=actor)
            return decision

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

    def evaluate_paired_checks(self, repo_root, proposal, checks, evaluator, *,
            repetitions=1, gate, run_budget, max_trial_cost_usd=0.0,
            fired_tasks=None, actor="evaluator"):
        """Execute a reserved two-arm plan; never implicitly approve or activate."""
        from .paired_execution import execute_paired_checks

        return execute_paired_checks(self, repo_root, proposal, checks, evaluator,
            repetitions=repetitions, gate=gate, run_budget=run_budget,
            max_trial_cost_usd=max_trial_cost_usd, fired_tasks=fired_tasks, actor=actor)

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

    def search(self, repo_root, **options):
        """Generate and compare bounded rounds; never approve or activate."""
        from .search import run_search

        return run_search(self, repo_root, **options)

    def finalize_search(self, repo_root, **options):
        from .finalization import finalize_search

        return finalize_search(self, repo_root, **options)

    def request_finalist_approval(self, **options):
        from .finalization import request_finalist_approval

        return request_finalist_approval(self, **options)

    def settle_paired_costs(self, candidate_id):
        from .settlement import settle_paired_costs

        return settle_paired_costs(self, candidate_id)

    def reconcile_trial_cost(self, candidate_id, index, *, journal_path, actor):
        from .settlement import reconcile_trial_cost

        return reconcile_trial_cost(self, candidate_id, index, journal_path=journal_path, actor=actor)

    def prepare_evolution(self, repo_root, *, search_options, select_finalist,
                          vault, sealed_backend, sealed_cost_limit_usd):
        """Search through sealed validation; human confirmation stays separate."""
        from .sealed import SealedEvaluationVault, assert_disjoint_splits

        if not isinstance(vault, SealedEvaluationVault) or not callable(select_finalist):
            raise TypeError("workflow requires a sealed vault and finalist selector")
        training_ids = {check.check_id for check in search_options["paired_checks"]}
        if set(vault.training_task_ids) != training_ids:
            raise CandidateEvaluationError("workflow training split differs from vault")
        assert_disjoint_splits(training_ids, vault._sealed_task_ids)
        state = self.search(repo_root, **search_options)
        if not state["qualified_candidates"]:
            return {"status": "no_qualified_candidate", "search": state}
        for candidate in state["qualified_candidates"]:
            self.settle_paired_costs(candidate)
        selected = select_finalist(json.loads(json.dumps(state)))
        if selected not in state["qualified_candidates"]:
            raise CandidateEvaluationError("selected finalist did not qualify")
        result = self.finalize_search(repo_root, run_id=state["run_id"],
            candidate_id=selected, vault=vault, backend=sealed_backend,
            max_estimated_cost_usd=sealed_cost_limit_usd)
        if not result["passed"]:
            return {"status": "sealed_rejected", "search": state, "sealed": result}
        return {"status": "awaiting_human_approval", "search": state, "sealed": result,
                "approval_token": self.request_finalist_approval(
                    run_id=state["run_id"], candidate_id=selected)}


__all__ = ["ControlledEvolver"]
