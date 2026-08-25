"""Paired gate for comparing a role team with a single-agent baseline."""

from __future__ import annotations

from .paired import PairedEvaluator


class SubagentRoleEvaluator:
    def __init__(self, single_agent_runner, role_team_runner, grader):
        if not all(callable(item) for item in (single_agent_runner, role_team_runner, grader)):
            raise TypeError("subagent role evaluators must be callable")
        if single_agent_runner is role_team_runner:
            raise ValueError("single-agent and role-team runners must be isolated")
        self.single_agent_runner = single_agent_runner
        self.role_team_runner = role_team_runner
        self.grader = grader

    def run(self, cases, *, repetitions=1):
        def dispatch(runner_input, variant, repetition):
            runner = (
                self.single_agent_runner
                if variant == "single_agent"
                else self.role_team_runner
            )
            return runner(runner_input, repetition)

        result = PairedEvaluator(dispatch, self.grader).run(
            cases,
            variants=("single_agent", "role_team"),
            repetitions=repetitions,
        )
        summary = result["summary"]
        complementary_passes = summary["wins"]
        gate_passed = (
            complementary_passes > 0
            and summary["treatment_passes"] >= summary["control_passes"]
        )
        result["role_gate"] = {
            "id": "role_team_complements_single_agent",
            "status": "pass" if gate_passed else "fail",
            "complementary_passes": complementary_passes,
            "regressions": summary["losses"],
            "criterion": (
                "role_team wins at least one paired case and does not reduce total passes"
            ),
        }
        return result


__all__ = ["SubagentRoleEvaluator"]
