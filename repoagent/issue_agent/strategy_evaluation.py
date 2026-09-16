"""Adapt real Issue repair runs to the existing Evolver paired executor.

Both arms share a frozen investigation; only the host-installed Skill differs.
This is a trusted operator backend, never a tool available to the repair model.
"""

import json
from pathlib import Path

from ..evolver.evaluation import CandidateEvaluationError
from ..evolver.workspace import _git, _git_bytes
from .cases import CaseStore, bind_repository, digest, issue_identity
from .execution import validate_config
from .strategy import SKILL_PATH, validate_strategy
from .training_evidence import _identifier
from .workflow import execute_case


def repair_seed(state):
    """Exclude prior repairs, receipts and solutions from shared repair input."""
    config = validate_config(state["execution_config"])
    if digest(config) != state["execution_config_digest"] or "strategy_skill" in config:
        raise ValueError("seed requires unchanged unskilled execution config")
    investigations = [r for r in state["runs"] if r.get("phase") == "investigate"]
    if len(investigations) != 1:
        raise ValueError("seed requires exactly one investigation")
    run = investigations[0]
    worker = run.get("agent", {}).get("worker", {})
    if (run.get("status") != "completed" or worker.get("agent_status") != "completed"
            or run.get("verification", {}).get("baseline", {}).get("reproduced") is not True):
        raise ValueError("seed requires completed reproduced investigation")
    seed = {"issue": state["issue"], "repository": state["repository"], "config": config,
            "investigation": {"phase": "investigate", "status": "completed",
                "agent": {"worker": {"agent_status": "completed", "answer": worker["answer"]}}},
            "source_state_digest": digest(state)}
    return json.loads(json.dumps(seed, allow_nan=False))


def trial_result(state):
    run = state["runs"][-1]
    model = run.get("agent", {}).get("model", run.get("model_evidence", {}))
    candidate = run.get("verification", {}).get("candidate", {})
    baseline = run.get("verification", {}).get("baseline", {})
    measured = (model.get("measurement_valid") is True and model.get("cost_complete") is True)
    verified = (state["status"] in {"candidate_ready", "verification_failed"}
                and run.get("status") == "completed"
                and candidate.get("status") == "completed"
                and candidate.get("exit_code") in (0, 1)
                and candidate.get("output_truncated") is False
                and baseline.get("reproduced") is True)
    status = "completed" if measured and verified else "inconclusive"
    if state["status"] in {"execution_failed", "environment_blocked"}:
        status = "infrastructure_error"
    passed = state["status"] == "candidate_ready" if status == "completed" else None
    return {"status": status, "passed": passed,
            "score": float(passed) if passed is not None else None,
            "estimated_cost_usd": model.get("known_estimated_cost_usd") if measured else None,
            "raw": {"case_id": state["case_id"], "case_status": state["status"],
                    "state_digest": digest(state), "calls": model.get("calls_reserved"),
                    "known_estimated_cost_usd": model.get("known_estimated_cost_usd"),
                    "verification": run.get("verification", {})}}


class IssueRepairEvaluator:
    def __init__(self, *, baseline_commit, tasks, output_root, client_factory, execute=execute_case):
        self.base = baseline_commit
        self.tasks = {}
        for task_id, state in tasks.items():
            self.tasks[_identifier(task_id)] = repair_seed(state)
        if not self.tasks:
            raise ValueError("repair evaluation requires tasks")
        self.output_root = Path(output_root).resolve()
        self.client_factory = client_factory
        self.execute = execute

    def descriptor(self, repo_root):
        source = Path(__file__).resolve().parents[1]
        # Workers copy this live implementation; pin it separately from Skill commits.
        code = {str(p.relative_to(source)): p.read_bytes().hex()
                for p in sorted(source.rglob("*.py"))}
        client = self.client_factory()
        return {"kind": "issue-repair-paired/v1", "baseline_commit": self.base,
                "tasks_digest": digest(self.tasks), "implementation_digest": digest(code),
                "gateway": client.descriptor(), "output_root": str(self.output_root),
                "shared_investigation": True, "investigation_cost_included": False,
                "automatic_activation": False}

    def _config(self, repo_root, identity, seed):
        commit = identity["commit_sha"]
        if _git(repo_root, "rev-parse", commit + "^{tree}") != identity["tree_sha"]:
            raise CandidateEvaluationError("candidate tree changed")
        paths = _git(repo_root, "diff", "--name-only", self.base, commit).splitlines()
        if paths and paths != [SKILL_PATH]:
            raise CandidateEvaluationError("only the Issue Skill may differ")
        config = json.loads(json.dumps(seed["config"]))
        if _git(repo_root, "ls-tree", "--name-only", commit, SKILL_PATH):
            config["strategy_skill"] = validate_strategy(
                _git_bytes(repo_root, "show", commit + ":" + SKILL_PATH).decode("utf-8"))
        return config

    def evaluate(self, repo_root, identity, checks, descriptor):
        if descriptor != self.descriptor(repo_root):
            raise CandidateEvaluationError("repair evaluator configuration changed")
        for seed in self.tasks.values():
            self._config(repo_root, identity, seed)
        if any(check.command != "validate_issue_skill" for check in checks):
            raise CandidateEvaluationError("unsupported deterministic Issue check")
        return [{"check_id": c.check_id, "status": "pass"} for c in checks]

    def run_trial(self, repo_root, identity, check, repetition, descriptor, *, cost_limit_usd):
        if descriptor != self.descriptor(repo_root):
            raise CandidateEvaluationError("repair evaluator configuration changed")
        if check.command != "issue_repair" or check.check_id not in self.tasks:
            raise CandidateEvaluationError("unknown repair task")
        if type(repetition) is not int or repetition < 0:
            raise ValueError("invalid repetition")
        seed = self.tasks[check.check_id]
        config = self._config(repo_root, identity, seed)
        repo = seed["repository"]
        repository = issue_identity(seed["issue"]["url"])["repository"]
        if bind_repository(repo["path"], repo["base_revision"], repository) != repo:
            raise CandidateEvaluationError("target repository changed")
        client = self.client_factory()
        if client.descriptor() != descriptor["gateway"]:
            raise CandidateEvaluationError("model configuration changed")
        if client.limits.max_estimated_cost_usd > cost_limit_usd:
            raise CandidateEvaluationError("trial cannot reserve model budget")
        arm = "control" if identity["commit_sha"] == self.base else "treatment"
        root = self.output_root / identity["commit_sha"] / check.check_id / str(repetition)
        root.mkdir(parents=True, exist_ok=False)
        store = CaseStore(root / "cases")
        state = store.create(seed["issue"], repo)
        state.update(status="reproduced", runs=[json.loads(json.dumps(seed["investigation"]))],
                     execution_config=config, execution_config_digest=digest(config),
                     experiment={"arm": arm, "shared_seed_digest": digest(seed),
                                 "source_state_digest": seed["source_state_digest"]})
        store.save(state)
        try:
            self.execute(store, state["case_id"], "fix", client_factory=lambda: client)
        except Exception:
            # execute_case persists failure evidence; never silently retry paid work.
            state = store.load(state["case_id"])
            if state["status"] == "reproduced":
                raise
        state = store.load(state["case_id"])
        if descriptor != self.descriptor(repo_root):
            raise CandidateEvaluationError("repair configuration changed during trial")
        return trial_result(state)
