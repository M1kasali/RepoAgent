"""Offline context-budget and SQLite recall visibility acceptance."""

import json
from pathlib import Path

from ..context_manager import ContextBudgetExceededError, ContextManager
from ..providers import FakeModelClient
from ..run_store import RunStore
from ..runtime import RepoAgent
from ..session_store import SessionStore
from ..sqlite_memory import SQLiteMemoryBackend
from ..workspace import WorkspaceContext


def _agent(workspace, state, *, backend=None, track=None):
    return RepoAgent(
        model_client=FakeModelClient(["<final>done</final>"]),
        workspace=WorkspaceContext.build(workspace),
        session_store=SessionStore(state / "sessions"),
        run_store=RunStore(state / "runs"),
        memory_backend=backend, memory_track_id=track,
        approval_policy="never", checkpoint_policy="never",
    )


def run_context_memory_experiment(output_root):
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    context_rows = []
    for index, history_turns in enumerate((8, 24)):
        workspace = root / f"context-{index}"
        workspace.mkdir()
        agent = _agent(workspace, workspace / "state")
        checkpoint = "Task checkpoint:\nPreserve required verification before completion."
        agent.render_checkpoint_text = lambda: checkpoint
        for i in range(history_turns):
            agent.record({"role": "user", "content": f"old-{i}: " + "historical detail " * 60})
            agent.record({"role": "assistant", "content": f"acknowledged-{i}"})
        request = "Inspect current changes and preserve exact-request-marker."
        history_before = agent.history_text()
        arms = {}
        order = ["wide", "tight"] if index == 0 else ["tight", "wide"]
        for arm in order:
            budget = 12000 if arm == "wide" else 3000
            manager = ContextManager(
                agent, total_token_budget=budget,
                segment_token_budgets={
                    "prefix": 3000, "memory": 1000, "relevant_memory": 1000,
                    "history": 10000, "skills": 1000,
                },
            )
            try:
                prompt, metadata = manager.build(request)
            except ContextBudgetExceededError as exc:
                arms[arm] = {"status": "budget_rejected", "budget": budget,
                             "observed_tokens": exc.observed_tokens}
            else:
                arms[arm] = {"status": "assembled", "prompt": prompt, "metadata": metadata}
        try:
            ContextManager(agent, total_token_budget=1).build(request)
        except ContextBudgetExceededError:
            impossible_budget_rejected = True
        else:
            impossible_budget_rejected = False
        checks = {
            "impossible_budget_rejected": impossible_budget_rejected,
            "wide_assembled": arms["wide"]["status"] == "assembled",
            "within_budgets": all(not arm["metadata"]["prompt_over_token_budget"] for arm in arms.values() if arm["status"] == "assembled"),
            "request_retained": all(arm["prompt"].endswith("Current user request:\n" + request) for arm in arms.values() if arm["status"] == "assembled"),
            "checkpoint_retained": all(checkpoint in arm["prompt"] for arm in arms.values() if arm["status"] == "assembled"),
            "history_unchanged": agent.history_text() == history_before,
        }
        context_rows.append({"history_turns": history_turns, "arm_order": order,
                             "checks": checks, "passed": all(checks.values()), **arms})

    memory_rows = []
    for index in range(3):
        workspace = root / f"memory-{index}"
        workspace.mkdir()
        database = workspace / "memory.sqlite3"
        fact = f"The stagingcluster marker is recall-value-{index}-q7z."
        query = "What is the stagingcluster marker?"

        def build(label, track):
            return _agent(
                workspace, workspace / label,
                backend=SQLiteMemoryBackend(database, workspace=workspace), track=track,
            )

        seed = build("seed", "shared")
        seed.ask(fact)
        arms = {}
        sessions = [seed.session["id"]]
        order = ["isolated", "shared"] if index % 2 == 0 else ["shared", "isolated"]
        for arm in order:
            agent = build(arm, arm)
            agent.ask(query)
            sessions.append(agent.session["id"])
            arms[arm] = {
                "prompt": agent.model_client.prompts[0],
                "metadata": agent.last_memory_backend_metadata,
                "session_id": agent.session["id"],
            }
        checks = {
            "distinct_sessions": len(set(sessions)) == 3,
            "shared_fact_present": fact in arms["shared"]["prompt"],
            "isolated_fact_absent": fact not in arms["isolated"]["prompt"],
            "shared_hits": arms["shared"]["metadata"]["recalled_count"] > 0,
            "isolated_no_hits": arms["isolated"]["metadata"]["recalled_count"] == 0,
        }
        memory_rows.append({"case": index, "fact": fact, "query": query,
                            "arm_order": order, "checks": checks,
                            "passed": all(checks.values()), **arms})
    result = {
        "schema": "repoagent.context-memory-acceptance/v1",
        "evidence_scope": "offline_context_and_sqlite_visibility_contract",
        "positive_claim_eligible": False,
        "passed": all(row["passed"] for row in context_rows + memory_rows),
        "tight_context_success_count": sum(row["tight"]["status"] == "assembled" for row in context_rows),
        "context": context_rows, "memory": memory_rows,
        "limitations": [
            "passed covers safety and visibility; rejected tight contexts are not compression successes.",
            "Context token counts use the configured estimator, not billed API tokens.",
            "Injected checkpoint text is a fixture; only budget preservation is tested.",
            "SQLite probes use fresh Runtime sessions and backend instances in one process.",
            "Fake model answers are fixed and do not demonstrate factual answer quality.",
            "No actual model, file-reread savings, Myna or LoCoMo results are represented.",
            "Track selection is intentional configuration, not user authentication.",
        ],
    }
    (root / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
