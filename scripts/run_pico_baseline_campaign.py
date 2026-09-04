#!/usr/bin/env python3
"""Run pico-harness as the strict external baseline for a Polyglot campaign."""

# ruff: noqa: E402 - bootstrap the repository root before importing RepoAgent.

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from repoagent.cli import (
    _build_model_client,
    build_arg_parser as build_agent_arg_parser,
)
from repoagent.config import load_project_env
from repoagent.evaluation.container import (
    DockerContainerRunner,
    is_immutable_container_image,
    wsl_windows_path,
)
from repoagent.evaluation.pico_baseline import (
    PICO_BASELINE_VARIANT,
    PicoHarnessAgent,
)
from repoagent.evaluation.polyglot import PolyglotAdapter, PolyglotContainerGrader
from repoagent.evaluation.schema import collect_source_provenance
from repoagent.evaluation.polyglot_suite import CampaignBudget, PolyglotCampaign
from repoagent.pricing import ModelPricing
from scripts.run_polyglot_campaign import build_arg_parser as build_campaign_parser


def build_arg_parser():
    parser = build_campaign_parser()
    parser.description = __doc__
    parser.add_argument(
        "--pico-repo",
        required=True,
        help="Clean pico-harness source checkout used by the baseline runtime.",
    )
    return parser


def _activate_pico_checkout(path) -> Path:
    root = Path(path).resolve()
    if not (root / "pico" / "__init__.py").is_file():
        raise FileNotFoundError(f"pico-harness package does not exist: {root}")
    sys.path.insert(0, str(root))
    try:
        import pico
    except ImportError as exc:
        raise RuntimeError(
            "pico-harness dependencies are unavailable; run this script with "
            "the pico-harness virtualenv Python"
        ) from exc
    imported = Path(pico.__file__).resolve()
    if not imported.is_relative_to(root):
        raise RuntimeError(
            f"imported pico from {imported}, expected the checkout under {root}"
        )
    return root


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    if not args.allow_dirty_source and not is_immutable_container_image(args.image):
        raise ValueError(
            "formal pico baseline requires image@sha256:<64-hex-digest>"
        )
    repo_root = Path(args.repo_root).resolve()
    pico_root = _activate_pico_checkout(args.pico_repo)
    adapter_source = collect_source_provenance(repo_root)
    if not args.allow_dirty_source:
        if adapter_source.get("dirty"):
            raise ValueError("formal pico baseline requires a clean RepoAgent adapter")
        if not str(adapter_source.get("commit_sha", "")).strip():
            raise ValueError("formal pico baseline requires a committed RepoAgent adapter")
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
    base_agent_args.max_new_tokens = args.max_new_tokens
    base_agent_args.context_window_tokens = args.context_window_tokens
    base_agent_args.input_cost_per_1m_usd = args.input_cost_per_1m_usd
    base_agent_args.output_cost_per_1m_usd = args.output_cost_per_1m_usd
    base_agent_args.cache_read_cost_per_1m_usd = args.cache_read_cost_per_1m_usd
    base_agent_args.cache_write_cost_per_1m_usd = args.cache_write_cost_per_1m_usd
    base_agent_args.pricing_source = args.pricing_source

    path_converter = wsl_windows_path if args.wsl_windows_path else None

    def agent_factory(context):
        client = _build_model_client(copy.copy(base_agent_args))
        profile = client.profile
        return PicoHarnessAgent(
            workspace=context.repo_root,
            model_client=client,
            max_provider_calls=args.max_provider_calls_per_attempt,
            max_output_tokens=args.max_new_tokens,
            context_token_budget=args.context_token_budget,
            context_window_tokens=profile.context_window_tokens,
            context_window_source=profile.context_window_source,
            docker_executable=args.docker,
            docker_image=args.image,
            docker_memory="1g",
            docker_cpus=1.0,
            docker_pids_limit=128,
            docker_workspace_path_converter=path_converter,
            adapter_source=adapter_source,
        )

    container = DockerContainerRunner(
        docker_executable=args.docker,
        image=args.image,
        staging_root=args.staging_root,
        path_converter=path_converter,
        memory="1g",
        cpus=1.0,
        pids_limit=128,
    )
    result = PolyglotCampaign(
        repo_root=pico_root,
        state_root=repo_root,
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
        workspace_root=args.agent_staging_root,
        variant=PICO_BASELINE_VARIANT,
    ).run()
    if collect_source_provenance(repo_root) != adapter_source:
        raise RuntimeError("RepoAgent baseline adapter source changed during campaign")
    payload = {
        "output": str(Path(args.output).resolve()),
        "variant": PICO_BASELINE_VARIANT,
        "planned_run_n": result.aggregates["planned_run_n"],
        "executed_run_n": result.aggregates["executed_run_n"],
        "error_run_n": result.aggregates["error_run_n"],
        "passes": result.aggregates["passes"],
        "pass_rate": result.aggregates["pass_rate"],
        "provider_call_count": result.aggregates["provider_call_count"],
        "partial_estimated_cost_usd": result.aggregates[
            "partial_estimated_cost_usd"
        ],
        "gates": result.gates,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if all(gate["status"] != "fail" for gate in result.gates) else 1


if __name__ == "__main__":
    raise SystemExit(main())
