#!/usr/bin/env python3
"""Run one real-provider RepoAgent task against an isolated Polyglot grader."""

import argparse
import copy
import json
from pathlib import Path

from repoagent.cli import build_agent, build_arg_parser as build_agent_arg_parser
from repoagent.config import load_project_env
from repoagent.evaluation.container import DockerContainerRunner, wsl_windows_path
from repoagent.evaluation.polyglot import PolyglotAdapter, PolyglotContainerGrader
from repoagent.evaluation.polyglot_campaign import PolyglotSingleTaskCampaign


def build_arg_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--profile",
        choices=("ollama", "openai", "anthropic", "deepseek"),
        default="deepseek",
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--max-new-tokens", type=int, default=4096)
    parser.add_argument("--context-token-budget", type=int, default=8000)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--image", required=True)
    parser.add_argument("--staging-root", default=None)
    parser.add_argument("--wsl-windows-path", action="store_true")
    parser.add_argument(
        "--allow-dirty-source",
        action="store_true",
        help="Development-only override; formal evidence requires a clean commit.",
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    load_project_env(repo_root)
    language = args.task.split("/", 1)[0]
    loaded = PolyglotAdapter().load(args.dataset, languages=(language,))
    matches = [item for item in loaded["instances"] if item.runner.task_id == args.task]
    if len(matches) != 1:
        raise ValueError(f"Polyglot task not found exactly once: {args.task}")
    base_agent_args = build_agent_arg_parser().parse_args([])
    base_agent_args.profile = args.profile
    base_agent_args.provider = None
    base_agent_args.model = args.model
    base_agent_args.base_url = args.base_url
    base_agent_args.approval = "auto"
    base_agent_args.max_steps = args.max_steps
    base_agent_args.max_new_tokens = args.max_new_tokens
    base_agent_args.context_token_budget = args.context_token_budget
    base_agent_args.sandbox_backend = "docker"
    base_agent_args.sandbox_docker_executable = args.docker
    base_agent_args.sandbox_image = args.image
    base_agent_args.sandbox_memory = "1g"
    base_agent_args.sandbox_cpus = 1.0
    base_agent_args.sandbox_pids_limit = 128
    base_agent_args.require_isolation = True

    def agent_factory(context):
        agent_args = copy.copy(base_agent_args)
        agent_args.cwd = context.repo_root
        agent_args.repo_root_override = context.repo_root
        return build_agent(agent_args)

    container = DockerContainerRunner(
        docker_executable=args.docker,
        image=args.image,
        staging_root=args.staging_root,
        path_converter=wsl_windows_path if args.wsl_windows_path else None,
        memory="1g",
        cpus=1.0,
        pids_limit=128,
    )
    result = PolyglotSingleTaskCampaign(
        repo_root=repo_root,
        output_root=args.output,
        instance=matches[0],
        benchmark=loaded["benchmark"],
        agent_factory=agent_factory,
        grader=PolyglotContainerGrader(container),
        require_provider_probe=True,
        require_clean_source=not args.allow_dirty_source,
    ).run()
    row = result.rows[0]
    print(
        json.dumps(
            {
                "task_id": row.task_id,
                "status": row.status,
                "passed": row.verifier.get("passed", False),
                "code_passed": row.verifier.get("code_passed", False),
                "turn_converged": row.verifier.get("turn_converged", False),
                "stop_reason": row.verifier.get("stop_reason", ""),
                "output": str(Path(args.output).resolve()),
                "usage": row.metrics.get("usage", {}),
                "call_efficiency": row.metrics.get("call_efficiency", {}),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
