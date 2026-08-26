#!/usr/bin/env python3
"""Run the credential-free Polyglot campaign fixture used by CI."""

import argparse
import json
import time
from pathlib import Path

from repoagent import FakeModelClient, RepoAgent, SessionStore
from repoagent.evaluation.polyglot import PolyglotAdapter
from repoagent.evaluation.polyglot_suite import PolyglotCampaign


def build_arg_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--dataset",
        default="benchmarks/fixtures/polyglot-mini",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--repetitions", type=int, default=2)
    return parser


class FixtureGrader:
    """Grade fixed scripted output without executing generated code."""

    def grade(self, instance, runner_root):
        started = time.monotonic()
        expected = (instance.exercise_root / "tests" / "expected.txt").read_text(
            encoding="utf-8"
        )
        actual = (Path(runner_root) / "src" / "answer.txt").read_text(encoding="utf-8")
        passed = actual == expected
        return {
            "task_id": instance.runner.task_id,
            "passed": passed,
            "status": "completed",
            "exit_code": 0 if passed else 1,
            "duration_seconds": time.monotonic() - started,
            "stdout": "fixture passed\n" if passed else "",
            "stderr": "" if passed else "fixture output mismatch\n",
            "output_truncated": False,
        }


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    dataset = Path(args.dataset)
    if not dataset.is_absolute():
        dataset = repo_root / dataset
    loaded = PolyglotAdapter().load(dataset, languages=("python",), limit=2)

    def agent_factory(context):
        return RepoAgent(
            model_client=FakeModelClient(
                (
                    '<tool>{"name":"write_file","args":{"path":"src/answer.txt","content":"DONE\\n"}}</tool>',
                    "<final>Done.</final>",
                )
            ),
            workspace=context,
            session_store=SessionStore(
                Path(context.repo_root) / ".repoagent" / "sessions"
            ),
            approval_policy="auto",
        )

    result = PolyglotCampaign(
        repo_root=repo_root,
        output_root=args.output,
        instances=loaded["instances"],
        benchmark=loaded["benchmark"],
        agent_factory=agent_factory,
        grader=FixtureGrader(),
        repetitions=args.repetitions,
        require_provider_probe=False,
        require_clean_source=False,
    ).run()
    payload = {
        "output": str(Path(args.output).resolve()),
        "run_kind": result.model["run_kind"],
        "planned_run_n": result.aggregates["planned_run_n"],
        "passes": result.aggregates["passes"],
        "pass_rate": result.aggregates["pass_rate"],
        "limitations": ["Scripted CI fixture; not a model coding-quality measurement."],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if result.aggregates["passes"] == result.aggregates["planned_run_n"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
