"""Concrete sealed backend using the same host-budgeted pinned Agent worker."""

from decimal import Decimal
import math
from pathlib import Path

from .evaluation import CandidateEvaluationError, payload_digest
from .hosted_snapshot import HostedAgentSnapshotEvaluator
from .workspace import _git


class SnapshotSealedBackend:
    is_isolated = True

    def __init__(self, repo_root, evaluator, *, baseline_commit=None):
        if not isinstance(evaluator, HostedAgentSnapshotEvaluator):
            raise TypeError("sealed snapshot requires a hosted evaluator")
        self.repo_root = Path(repo_root).resolve()
        self.evaluator = evaluator
        self._descriptor = evaluator.descriptor(self.repo_root)
        self.baseline = None
        if baseline_commit is not None:
            commit = _git(self.repo_root, "rev-parse", baseline_commit + "^{commit}")
            self.baseline = {
                "commit_sha": commit,
                "tree_sha": _git(self.repo_root, "rev-parse", commit + "^{tree}"),
            }
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
            **(
                {"kind": "snapshot-sealed-paired/v1", "baseline": dict(self.baseline)}
                if self.baseline is not None
                else {}
            ),
        }

    def evaluate(
        self, *, candidate_ref, task_ids, grader_digest, max_estimated_cost_usd
    ):
        tasks = tuple(task_ids)
        if (
            type(max_estimated_cost_usd) not in {int, float}
            or not math.isfinite(max_estimated_cost_usd)
            or max_estimated_cost_usd < 0
        ):
            raise CandidateEvaluationError(
                "sealed budget must be finite and nonnegative"
            )
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
        total = Decimal(str(cap)) * len(tasks) * (2 if self.baseline else 1)
        if total > Decimal(str(max_estimated_cost_usd)):
            raise CandidateEvaluationError("sealed budget cannot reserve every task")
        commit = _git(self.repo_root, "rev-parse", candidate_ref + "^{commit}")
        identity = {
            "commit_sha": commit,
            "tree_sha": _git(self.repo_root, "rev-parse", commit + "^{tree}"),
        }
        rows = []

        def trial(task_id, source):
            result = self.evaluator.run_trial(
                self.repo_root,
                source,
                self.evaluator.tasks[task_id].check(),
                0,
                self._descriptor,
                cost_limit_usd=cap,
            )
            amount = result["estimated_cost_usd"]
            if (
                result["status"] != "completed"
                or type(result["passed"]) is not bool
                or type(amount) not in {int, float}
                or not math.isfinite(amount)
                or not 0 <= amount <= cap
            ):
                raise CandidateEvaluationError(
                    "sealed snapshot requires execution review"
                )
            return result

        for index, task_id in enumerate(tasks):
            if self.baseline is not None:
                order = (
                    ["baseline", "candidate"]
                    if index % 2 == 0
                    else ["candidate", "baseline"]
                )
                sources = {"baseline": self.baseline, "candidate": identity}
                arms = {}
                for arm in order:
                    result = trial(task_id, sources[arm])
                    arms[arm] = {
                        "source": dict(sources[arm]),
                        "passed": result["passed"],
                        "estimated_cost_usd": result["estimated_cost_usd"],
                        "evidence": result["raw"],
                    }
                rows.append(
                    {
                        "task_id": task_id,
                        "passed": arms["candidate"]["passed"],
                        "estimated_cost_usd": sum(
                            a["estimated_cost_usd"] for a in arms.values()
                        ),
                        "arms": arms,
                        "execution_order": order,
                    }
                )
                continue
            result = trial(task_id, identity)
            rows.append(
                {
                    "task_id": task_id,
                    "passed": result["passed"],
                    "estimated_cost_usd": result["estimated_cost_usd"],
                    "evidence": result["raw"],
                }
            )
        return rows
