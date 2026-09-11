"""Verified deployment routes for isolated Agent task execution."""

import json
from pathlib import Path
from uuid import uuid4

from ..atomic_io import atomic_replace_unlocked, file_lock
from .activation import ActivationError
from .evaluation import payload_digest
from .hosted_snapshot import HostedAgentSnapshotEvaluator
from .workspace import _git, candidate_ref


class SnapshotDeployment:
    """Resolve on each task; existing tasks keep their originally pinned commit.

    Deploys a complete candidate snapshot, not a mix of routes from different
    commits. Skill routes require explicit skill-enabled task configuration.
    User checkout files are never replaced by approval or rollback.
    """

    def __init__(self, evolver, repo_root, *, baseline_commit, evaluator):
        if not isinstance(evaluator, HostedAgentSnapshotEvaluator):
            raise TypeError("snapshot deployment requires a hosted evaluator")
        self.evolver = evolver
        self.repo_root = Path(repo_root).resolve()
        self.baseline = _git(self.repo_root, "rev-parse", baseline_commit + "^{commit}")
        self.evaluator = evaluator

    def _verify(self, candidate_id, commit):
        events = [
            e for e in self.evolver.ledger.events() if e["candidate_id"] == candidate_id
        ]
        identities = [
            e["payload"] for e in events if e["event_type"] == "candidate.materialized"
        ]
        if (
            len(identities) != 1
            or identities[0]["commit_sha"] != commit
            or identities[0]["base_commit"] != self.baseline
        ):
            raise ActivationError(
                "deployment candidate differs from baseline or materialization"
            )
        if (
            _git(self.repo_root, "rev-parse", candidate_ref(candidate_id)) != commit
            or _git(self.repo_root, "rev-parse", commit + "^{tree}")
            != identities[0]["tree_sha"]
        ):
            raise ActivationError("deployment source identity changed")
        return identities[0]

    def approve(self, token, *, candidate_id, label, actor):
        if label not in {"prompt", "tool_policy", "routing", "skill"}:
            raise ActivationError(
                "snapshot deployment does not support this strategy label"
            )
        if label == "skill" and not all(
            task.enable_skills for task in self.evaluator.tasks.values()
        ):
            raise ActivationError("skill deployment requires skill-enabled tasks")
        with file_lock(self.evolver.ledger.path.parent / ".lock" / "deployment.lock"):
            events = self.evolver.ledger.events()
            sealed = [
                e
                for e in events
                if e["candidate_id"] == candidate_id
                and e["event_type"] == "sealed.completed"
            ]
            if not sealed or sealed[-1]["payload"]["passed"] is not True:
                raise ActivationError("deployment requires sealed validation")
            self._verify(candidate_id, sealed[-1]["payload"]["commit_sha"])
            # A deployment is one complete snapshot, not a composition of labels.
            for other in ("prompt", "tool_policy", "routing", "skill"):
                if (
                    other != label
                    and self.evolver.activations.resolve(other) is not None
                ):
                    raise ActivationError(
                        "another snapshot route is active; roll it back first"
                    )
            if token is None:
                confirmations = [
                    e
                    for e in events
                    if e["event_type"] == "approval.confirmed"
                    and e["candidate_id"] == candidate_id
                ]
                if not confirmations:
                    raise ActivationError(
                        "deployment has no durable human confirmation"
                    )
                confirmation = confirmations[-1]
                if any(
                    e["event_type"] == "activation.activated"
                    and e["candidate_id"] == candidate_id
                    and e["sequence"] > confirmation["sequence"]
                    for e in events
                ):
                    raise ActivationError("confirmation already activated a deployment")
            else:
                confirmation = self.evolver.approvals.confirm(
                    token, actor=actor, expected_candidate_id=candidate_id
                )
            if confirmation["candidate_id"] != candidate_id:
                raise ActivationError("approval token belongs to another candidate")
            return self.evolver.activations.activate(label, candidate_id, actor=actor)

    def activate_confirmed(self, *, candidate_id, label, actor):
        """Recover after durable human confirmation but before activation."""
        return self.approve(None, candidate_id=candidate_id, label=label, actor=actor)

    def rollback(self, label, *, actor):
        with file_lock(self.evolver.ledger.path.parent / ".lock" / "deployment.lock"):
            return self.evolver.activations.rollback(label, actor=actor)

    def run_task(self, task_id, *, label="prompt", cost_limit_usd):
        if label not in {"prompt", "tool_policy", "routing", "skill"}:
            raise ActivationError("unsupported deployment label")
        if label == "skill" and not self.evaluator.tasks[task_id].enable_skills:
            raise ActivationError("skill route requires skill-enabled task")
        with file_lock(self.evolver.ledger.path.parent / ".lock" / "deployment.lock"):
            active = self.evolver.activations.resolve(label)
            identity = (
                self._verify(active.candidate_id, active.commit_sha)
                if active
                else {
                    "commit_sha": self.baseline,
                    "tree_sha": _git(
                        self.repo_root, "rev-parse", self.baseline + "^{tree}"
                    ),
                }
            )
        task = self.evaluator.tasks[task_id]
        run_id = uuid4().hex
        plan = {
            "run_id": run_id,
            "source": identity,
            "activation_event_id": active.activation_event_id if active else None,
            "task": task.descriptor(),
            "evaluator": self.evaluator.descriptor(self.repo_root),
            "cost_limit_usd": cost_limit_usd,
        }
        self.evolver.ledger.append(
            "deployment.started", actor="snapshot-runtime", payload=plan
        )
        try:
            result = self.evaluator.run_trial(
                self.repo_root,
                identity,
                task.check(),
                0,
                plan["evaluator"],
                cost_limit_usd=cost_limit_usd,
            )
            receipt = {"plan": plan, "result": result}
            path = self.evolver.ledger.path.parent / "deployments" / f"{run_id}.json"
            atomic_replace_unlocked(
                path, json.dumps(receipt, sort_keys=True, allow_nan=False)
            )
            self.evolver.ledger.append(
                "deployment.completed",
                actor="snapshot-runtime",
                payload={
                    "run_id": run_id,
                    "receipt_digest": payload_digest(receipt),
                    "path": str(path),
                },
            )
            return receipt
        except BaseException as exc:
            self.evolver.ledger.append(
                "deployment.failed",
                actor="snapshot-runtime",
                payload={"run_id": run_id, "error_type": type(exc).__name__},
            )
            raise
