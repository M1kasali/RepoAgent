import pytest

from repoagent import EvaluationCase, Grade, PairedEvaluator, TrialOutput


def test_paired_context_and_memory_evaluation_isolates_answers_from_runner():
    runner_inputs = []

    def runner(item, variant, repetition):
        runner_inputs.append(item)
        assert not hasattr(item, "expected")
        if item.category == "context":
            history = str(item.metadata["history"])
            text = (
                history + "\n" + item.prompt
                if variant == "control"
                else history[-40:] + "\n" + item.prompt
            )
            return TrialOutput(
                text,
                metrics={"input_chars": len(text), "repetition": repetition},
            )
        text = (
            "unknown"
            if variant == "control"
            else str(item.metadata["memory_fact"])
        )
        return TrialOutput(text, metrics={"repetition": repetition})

    def grader(case, output):
        if case.category == "context":
            passed = str(case.expected) in output.text
        else:
            passed = output.text == case.expected
        return Grade(passed=passed, score=1.0 if passed else 0.0)

    artifact = PairedEvaluator(runner, grader).run(
        [
            EvaluationCase(
                task_id="context-1",
                prompt="Current request: KEEP_REQUEST",
                expected="KEEP_REQUEST",
                category="context",
                metadata={"history": "old context " * 100},
            ),
            EvaluationCase(
                task_id="memory-1",
                prompt="What color is the deploy key?",
                expected="blue",
                category="memory",
                metadata={"memory_fact": "blue"},
            ),
        ],
        repetitions=2,
    )

    assert artifact["schema_version"] == "paired-evaluation/v1"
    assert artifact["summary"] == {
        "effective_n": 2,
        "run_n": 8,
        "pair_n": 4,
        "wins": 2,
        "ties": 2,
        "losses": 0,
        "control_passes": 2,
        "treatment_passes": 4,
    }
    assert len(runner_inputs) == 8
    context_rows = [
        row for row in artifact["rows"] if row["category"] == "context"
    ]
    for repetition in range(2):
        control, treatment = [
            row for row in context_rows if row["repetition"] == repetition
        ]
        assert control["passed"] is True
        assert treatment["passed"] is True
        assert treatment["metrics"]["input_chars"] < control["metrics"]["input_chars"]


def test_paired_evaluator_validates_design_and_output_contracts():
    case = EvaluationCase("task", "prompt", "answer", "context")

    with pytest.raises(ValueError):
        PairedEvaluator(lambda *_: None, lambda *_: None).run(
            [case], variants=("same", "same")
        )
    with pytest.raises(ValueError):
        PairedEvaluator(lambda *_: None, lambda *_: None).run(
            [case, case]
        )
    with pytest.raises(TypeError):
        PairedEvaluator(lambda *_: "invalid", lambda *_: Grade(True, 1)).run(
            [case]
        )


def test_paired_evaluator_rejects_shared_runner_and_grader_callable():
    def shared(*_):
        return None

    with pytest.raises(ValueError):
        PairedEvaluator(shared, shared)
