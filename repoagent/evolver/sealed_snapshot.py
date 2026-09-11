"""Concrete sealed backend using the same host-budgeted pinned Agent worker."""

from decimal import Decimal
from pathlib import Path

from .evaluation import CandidateEvaluationError, payload_digest
from .hosted_snapshot import HostedAgentSnapshotEvaluator
from .workspace import _git


class SnapshotSealedBackend:
    is_isolated = True

    def __init__(self, repo_root, evaluator):
        if not isinstance(evaluator, HostedAgentSnapshotEvaluator):
            raise TypeError("sealed snapshot requires a hosted evaluator")
        self.repo_root = Path(repo_root).resolve()
        self.evaluator = evaluator
        self._descriptor = evaluator.descriptor(self.repo_root)
        self.grader_digest = payload_digest(
            {
                "grader": self._descriptor["grader"],
                "tasks": self._descriptor["tasks"],
            }
        )

    def descriptor(self, repo_root):
        if Path(repo_root).resolve() != self.repo_root:
            raise CandidateEvaluationError("sealed repository changed")
        return {
            "kind": "snapshot-sealed/v1",
            "snapshot": self._descriptor,
            "grader_digest": self.grader_digest,
        }

    def evaluate(
        self, *, candidate_ref, task_ids, grader_digest, max_estimated_cost_usd
    ):
        tasks = tuple(task_ids)
        if len(set(tasks)) != len(tasks) or set(tasks) != set(self.evaluator.tasks):
            raise CandidateEvaluationError(
                "sealed task set differs from frozen backend"
            )
        if (
            grader_digest != self.grader_digest
            or self.evaluator.descriptor(self.repo_root) != self._descriptor
        ):
            raise CandidateEvaluationError("sealed evaluator configuration changed")
        cap = self._descriptor["model_gateway"]["limits"]["max_estimated_cost_usd"]
        total = Decimal(str(cap)) * len(tasks)
        if total > Decimal(str(max_estimated_cost_usd)):
            raise CandidateEvaluationError("sealed budget cannot reserve every task")
        commit = _git(self.repo_root, "rev-parse", candidate_ref + "^{commit}")
        identity = {
            "commit_sha": commit,
            "tree_sha": _git(self.repo_root, "rev-parse", commit + "^{tree}"),
        }
        rows = []
        for task_id in tasks:
            result = self.evaluator.run_trial(
                self.repo_root,
                identity,
                self.evaluator.tasks[task_id].check(),
                0,
                self._descriptor,
                cost_limit_usd=cap,
            )
            if result["status"] != "completed" or result["estimated_cost_usd"] is None:
                raise CandidateEvaluationError(
                    "sealed snapshot requires execution review"
                )
            rows.append(
                {
                    "task_id": task_id,
                    "passed": result["passed"],
                    "estimated_cost_usd": result["estimated_cost_usd"],
                    "evidence": result["raw"],
                }
            )
        return rows
