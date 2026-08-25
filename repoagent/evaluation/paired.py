"""Paired evaluation runner with answer-isolated graders."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class EvaluationInput:
    task_id: str
    prompt: str
    category: str
    metadata: Mapping = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class EvaluationCase:
    task_id: str
    prompt: str
    expected: object
    category: str
    metadata: Mapping = field(default_factory=dict)

    def runner_input(self):
        return EvaluationInput(
            task_id=self.task_id,
            prompt=self.prompt,
            category=self.category,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class TrialOutput:
    text: str
    metrics: Mapping = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))


@dataclass(frozen=True)
class Grade:
    passed: bool
    score: float
    reason: str = ""

    def __post_init__(self):
        score = float(self.score)
        if not 0.0 <= score <= 1.0:
            raise ValueError("evaluation grade score must be between 0 and 1")
        object.__setattr__(self, "score", score)


class PairedEvaluator:
    def __init__(self, runner, grader):
        if not callable(runner) or not callable(grader):
            raise TypeError("paired evaluator runner and grader must be callable")
        if runner is grader:
            raise ValueError("paired evaluator runner and grader must be isolated")
        self.runner = runner
        self.grader = grader

    def run(self, cases, *, variants=("control", "treatment"), repetitions=1):
        variants = tuple(str(item) for item in variants)
        if len(variants) != 2 or len(set(variants)) != 2:
            raise ValueError("paired evaluation requires two distinct variants")
        if isinstance(repetitions, bool) or not isinstance(repetitions, int):
            raise TypeError("paired repetitions must be an integer")
        if repetitions < 1:
            raise ValueError("paired repetitions must be positive")
        cases = tuple(cases)
        if len({case.task_id for case in cases}) != len(cases):
            raise ValueError("paired evaluation task ids must be unique")

        rows = []
        pairs = []
        for case in cases:
            runner_input = case.runner_input()
            for repetition in range(repetitions):
                pair_rows = []
                for variant in variants:
                    output = self.runner(runner_input, variant, repetition)
                    if not isinstance(output, TrialOutput):
                        raise TypeError("paired runner must return TrialOutput")
                    grade = self.grader(case, output)
                    if not isinstance(grade, Grade):
                        raise TypeError("paired grader must return Grade")
                    row = {
                        "task_id": case.task_id,
                        "category": case.category,
                        "variant": variant,
                        "repetition": repetition,
                        "passed": grade.passed,
                        "score": grade.score,
                        "reason": grade.reason,
                        "output": output.text,
                        "metrics": dict(output.metrics),
                    }
                    rows.append(row)
                    pair_rows.append(row)
                control, treatment = pair_rows
                delta = treatment["score"] - control["score"]
                outcome = "win" if delta > 0 else "loss" if delta < 0 else "tie"
                pairs.append(
                    {
                        "task_id": case.task_id,
                        "category": case.category,
                        "repetition": repetition,
                        "control_score": control["score"],
                        "treatment_score": treatment["score"],
                        "delta": delta,
                        "outcome": outcome,
                    }
                )
        return {
            "schema_version": "paired-evaluation/v1",
            "design": {
                "control": variants[0],
                "treatment": variants[1],
                "paired_by": ["task_id", "repetition"],
                "repetitions": repetitions,
            },
            "summary": {
                "effective_n": len(cases),
                "run_n": len(rows),
                "pair_n": len(pairs),
                "wins": sum(pair["outcome"] == "win" for pair in pairs),
                "ties": sum(pair["outcome"] == "tie" for pair in pairs),
                "losses": sum(pair["outcome"] == "loss" for pair in pairs),
                "control_passes": sum(
                    row["passed"] for row in rows if row["variant"] == variants[0]
                ),
                "treatment_passes": sum(
                    row["passed"] for row in rows if row["variant"] == variants[1]
                ),
            },
            "rows": rows,
            "pairs": pairs,
        }


__all__ = [
    "EvaluationCase",
    "EvaluationInput",
    "Grade",
    "PairedEvaluator",
    "TrialOutput",
]
