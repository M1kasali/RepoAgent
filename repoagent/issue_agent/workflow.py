"""Explicit issue phases with durable terminal reports and fail-closed verification."""

import json
from pathlib import Path

from ..atomic_io import atomic_replace_unlocked
from ..evolver.model_budget import (
    BudgetedEvaluationClient,
    EvaluationBudgetError,
)
from ..pricing import ModelPricing
from .cases import digest
from .feedback import diagnose
from .execution import export_revision, make_patch, run_agent, validate_config, verify


class StructuredModelClient:
    """Keep full native tool history on the provider's streaming request path."""

    def __init__(self, client):
        self.client = client
        self.model = client.model

    def generate(self, request):
        from ..providers.base import stream_model

        return stream_model(self.client, request)


def make_client():
    from ..cli import _build_model_client, build_arg_parser
    from ..config import load_project_env
    from ..providers.clients import AnthropicCompatibleModelClient
    from .admission import count_anthropic_request, issue_limits

    load_project_env(Path.cwd())
    leaf = _build_model_client(
        build_arg_parser().parse_args(
            ["--profile", "deepseek", "--model", "deepseek-flash"]
        )
    )
    if not isinstance(leaf, AnthropicCompatibleModelClient):
        raise ValueError("Issue admission currently requires the configured Anthropic-compatible transport")
    pricing = ModelPricing(
        0.3,
        1.2,
        "DeepSeek official pricing 2026-09-14, peak-rate estimate; not invoice",
        cache_read_per_1m_usd=0.006,
        cache_write_per_1m_usd=0.3,
    )

    def count(request):
        return count_anthropic_request(request, model=leaf.model, temperature=leaf.temperature)

    client = BudgetedEvaluationClient(
        StructuredModelClient(leaf),
        limits=issue_limits(),
        pricing=pricing,
        request_token_counter=count,
        counter_identity="anthropic-wire-json-bytes-plus512/v2",
    )
    client.counter_temperature = leaf.temperature
    return client


def write_report(store, state):
    directory = store.directory(state["case_id"])
    latest = state["runs"][-1] if state["runs"] else {}
    report = {
        "schema": "repoagent.issue-report/v1",
        "case_id": state["case_id"],
        "status": state["status"],
        "issue": state["issue"],
        "repository": state["repository"],
        "latest_run": latest,
        "automatic_publication": False,
        "feedback": diagnose(state),
    }
    atomic_replace_unlocked(
        directory / "report.json", json.dumps(report, indent=2) + "\n"
    )
    answer = (
        latest.get("agent", {})
        .get("worker", {})
        .get("answer", "No completed Agent answer.")
    )
    text = (
        f"# Issue Investigation\n\nCase: `{state['case_id']}`\n\nStatus: **{state['status']}**\n\n"
        f"Issue: {state['issue']['url']}\n\nBase: `{state['repository']['base_revision']}`\n\n"
        "## Agent Analysis (Untrusted)\n\n" + answer + "\n\n"
        "## Host Verification\n\n```json\n"
        + json.dumps(latest.get("verification", {}), indent=2)
        + "\n```\n\nThe maintainer supplied the probe. A passing probe does not prove all behavior correct. "
        "No external comment, PR, merge or issue closure was performed.\n"
    )
    atomic_replace_unlocked(directory / "report.md", text)


def execute_case(
    store,
    case_id,
    phase,
    *,
    config=None,
    client_factory=make_client,
    agent_runner=run_agent,
    verifier=verify,
    on_progress=None,
):
    def progress(stage):
        if on_progress is not None:
            on_progress(stage)

    if phase not in {"investigate", "fix"}:
        raise ValueError("unsupported issue phase")
    with store.locked(case_id) as state:
        if any(run["status"] == "running" for run in state["runs"]):
            raise ValueError(
                "previous execution is uncertain; do not automatically replay"
            )
        if phase == "fix" and state["status"] != "reproduced":
            raise ValueError("repair requires a completed reproduced investigation")
        if phase == "investigate" and state["runs"]:
            raise ValueError(
                "investigation already attempted; create a new case for a deliberate retry"
            )
        if phase == "investigate":
            config = validate_config(config)
            state["execution_config"] = config
            state["execution_config_digest"] = digest(config)
        else:
            config = validate_config(state["execution_config"])
            if digest(config) != state["execution_config_digest"]:
                raise ValueError("frozen execution config changed")
        run = {"phase": phase, "status": "running", "config_digest": digest(config)}
        state["runs"].append(run)
        state["status"] = "investigating" if phase == "investigate" else "repairing"
        directory = store.directory(case_id) / phase
        directory.mkdir()
        store.save(state)
        try:
            if not state["issue"]["body"].strip():
                state["status"] = "needs_information"
                run["reason"] = (
                    "Issue body is empty; provide observed and expected behavior."
                )
            else:
                progress("baseline")
                baseline = verifier(directory / "baseline", state["repository"], config)
                run["verification"] = {"baseline": baseline}
                if not baseline["reproduced"]:
                    state["status"] = (
                        "not_reproduced"
                        if baseline["passed"]
                        else "environment_blocked"
                    )
                else:
                    progress("agent")
                    agent = agent_runner(
                        directory, state, config, client_factory(), phase
                    )
                    run["agent"] = agent
                    if agent["worker"].get("agent_status") != "completed":
                        budget_reason = agent["worker"].get("budget_reason")
                        if agent["worker"].get("stop_reason") == "step_limit_reached":
                            budget_reason = budget_reason or "call_limit"
                        state["status"] = "budget_exhausted" if budget_reason else (
                            "investigation_incomplete"
                            if phase == "investigate"
                            else "verification_failed"
                        )
                        if budget_reason:
                            run["budget_reason"] = budget_reason
                    elif phase == "investigate":
                        state["status"] = "reproduced"
                    else:
                        progress("candidate")
                        candidate = verifier(
                            directory / "candidate",
                            state["repository"],
                            config,
                            agent["changes"],
                        )
                        run["verification"]["candidate"] = candidate
                        state["status"] = (
                            "candidate_ready"
                            if candidate["passed"] and agent["changes"]
                            else "verification_failed"
                        )
                        # Diff against a fresh source export, never the mutable worker tree.
                        original = directory / "patch-base"
                        export_revision(state["repository"], original)
                        patch = make_patch(original, agent["changes"])
                        if candidate.get("patch_digest", digest(patch)) != digest(
                            patch
                        ):
                            raise ValueError(
                                "delivered patch differs from verified patch"
                            )
                        atomic_replace_unlocked(directory / "candidate.patch", patch)
                        run["patch"] = str(directory / "candidate.patch")
                        run["changes_digest"] = agent["changes_digest"]
            run["status"] = "completed"
        except BaseException as exc:
            run["status"] = "failed"
            run["error_type"] = type(exc).__name__
            state["status"] = "execution_failed"
            cause = exc
            seen = set()
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                if isinstance(cause, EvaluationBudgetError):
                    state["status"] = "budget_exhausted"
                    run["budget_reason"] = cause.reason
                    break
                cause = cause.__cause__
            budget_path = directory / "model-budget.json"
            if budget_path.is_file():
                try:
                    run["model_evidence"] = json.loads(budget_path.read_text())
                except (OSError, ValueError):
                    run["model_evidence_unavailable"] = True
            store.save(state)
            write_report(store, state)
            progress("failed")
            raise
        store.save(state)
        write_report(store, state)
        progress("finished:" + state["status"])
        return state
