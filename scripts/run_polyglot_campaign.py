#!/usr/bin/env python3
"""Run a budgeted real-provider RepoAgent Polyglot canary campaign."""

import argparse
import copy
import json
from pathlib import Path

from repoagent.cli import build_agent, build_arg_parser as build_agent_arg_parser
from repoagent.config import load_project_env
from repoagent.evaluation.container import DockerContainerRunner, wsl_windows_path
from repoagent.evaluation.polyglot import PolyglotAdapter, PolyglotContainerGrader
from repoagent.evaluation.polyglot_suite import CampaignBudget, PolyglotCampaign
from repoagent.pricing import ModelPricing


def build_arg_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument(
        "--languages",
        default="cpp,go,java,javascript,python,rust",
        help="Comma-separated benchmark languages.",
    )
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--repetitions", type=int, default=1)
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
    parser.add_argument("--max-provider-calls-per-attempt", type=int, default=24)
    parser.add_argument("--max-input-tokens-per-call", type=int, required=True)
    parser.add_argument("--hard-cost-cap-usd", type=float, required=True)
    parser.add_argument("--input-cost-per-1m-usd", type=float, required=True)
    parser.add_argument("--output-cost-per-1m-usd", type=float, required=True)
    parser.add_argument("--cache-read-cost-per-1m-usd", type=float, default=None)
    parser.add_argument("--cache-write-cost-per-1m-usd", type=float, default=None)
    parser.add_argument("--pricing-source", required=True)
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
    languages = tuple(
        item.strip() for item in args.languages.split(",") if item.strip()
    )
    loaded = PolyglotAdapter().load(
        args.dataset,
        languages=languages,
        limit=args.limit,
    )
    pricing = ModelPricing(
        args.input_cost_per_1m_usd,
        args.output_cost_per_1m_usd,
        args.pricing_source,
        cache_read_per_1m_usd=args.cache_read_cost_per_1m_usd,
        cache_write_per_1m_usd=args.cache_write_cost_per_1m_usd,
    )
    base_agent_args = build_agent_arg_parser().parse_args([])
    base_agent_args.profile = args.profile
    base_agent_args.provider = None
    base_agent_args.model = args.model
    base_agent_args.base_url = args.base_url
    base_agent_args.approval = "auto"
    base_agent_args.max_steps = args.max_steps
    base_agent_args.max_new_tokens = args.max_new_tokens
    base_agent_args.context_token_budget = args.context_token_budget
    base_agent_args.input_cost_per_1m_usd = args.input_cost_per_1m_usd
    base_agent_args.output_cost_per_1m_usd = args.output_cost_per_1m_usd
    base_agent_args.cache_read_cost_per_1m_usd = args.cache_read_cost_per_1m_usd
    base_agent_args.cache_write_cost_per_1m_usd = args.cache_write_cost_per_1m_usd
    base_agent_args.pricing_source = args.pricing_source
    base_agent_args.sandbox_backend = "docker"
    base_agent_args.sandbox_docker_executable = args.docker
    base_agent_args.sandbox_image = args.image
    base_agent_args.sandbox_memory = "1g"
    base_agent_args.sandbox_cpus = 1.0
    base_agent_args.sandbox_pids_limit = 128
    base_agent_args.sandbox_workspace_path_converter = (
        wsl_windows_path if args.wsl_windows_path else None
    )
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
    result = PolyglotCampaign(
        repo_root=repo_root,
        output_root=args.output,
        instances=loaded["instances"],
        benchmark=loaded["benchmark"],
        agent_factory=agent_factory,
        grader=PolyglotContainerGrader(container),
        repetitions=args.repetitions,
        budget=CampaignBudget(
            max_provider_calls_per_attempt=args.max_provider_calls_per_attempt,
            max_input_tokens_per_call=args.max_input_tokens_per_call,
            max_output_tokens_per_call=args.max_new_tokens,
            hard_cost_cap_usd=args.hard_cost_cap_usd,
            pricing=pricing,
        ),
        require_provider_probe=True,
        require_clean_source=not args.allow_dirty_source,
    ).run()
    payload = {
        "output": str(Path(args.output).resolve()),
        "planned_run_n": result.aggregates["planned_run_n"],
        "executed_run_n": result.aggregates["executed_run_n"],
        "skipped_run_n": result.aggregates["skipped_run_n"],
        "passes": result.aggregates["passes"],
        "pass_rate": result.aggregates["pass_rate"],
        "provider_call_count": result.aggregates["provider_call_count"],
        "partial_estimated_cost_usd": result.aggregates["partial_estimated_cost_usd"],
        "gates": result.gates,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if all(gate["status"] != "fail" for gate in result.gates) else 1


if __name__ == "__main__":
    raise SystemExit(main())
