"""Freeze a host-owned training baseline before model execution; no search or retry."""

import hashlib
import json
from pathlib import Path

from ..atomic_io import atomic_replace_unlocked
from .cases import CaseStore, bind_repository, digest, issue_identity, read_snapshot
from .execution import validate_config
from .feedback import diagnose
from .training_evidence import _identifier
from .workflow import execute_case, make_client


def implementation_digest():
    root = Path(__file__).resolve().parents[1]
    return digest({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(root.rglob("*.py"))})


def prepare_batch(rows, output, *, excluded_sources=(), client_factory=make_client):
    """Read only declared training inputs. Validate all cases before reserving calls."""
    if not rows or len(rows) > 100:
        raise ValueError("training batch requires 1-100 tasks")
    tasks, ids, sources = [], set(), set()
    excluded = {url.rstrip("/").lower() for url in excluded_sources}
    for row in rows:
        task_id, family = _identifier(row["task_id"]), _identifier(row["family"])
        source = issue_identity(row["issue_url"])["url"].lower()
        if task_id in ids or source in sources or source in excluded:
            raise ValueError("duplicate or held-out training task")
        issue = read_snapshot(row["issue_file"], row["issue_url"])
        ids.add(task_id)
        sources.add(source)
        config = validate_config(json.loads(Path(row["config_file"]).read_text()))
        if "strategy_skill" in config:
            raise ValueError("baseline must not contain a candidate Skill")
        repository = bind_repository(row["repo_path"], row["base_revision"], issue["repository"])
        if type(row["previously_seen"]) is not bool:
            raise ValueError("declare previous exposure")
        tasks.append({"task_id": task_id, "family": family, "split": "training",
                      "previously_seen": row["previously_seen"], "issue": issue,
                      "repository": repository, "config": config})
    client = client_factory()
    plan = {"schema": "repoagent.issue-training-batch/v1", "tasks": tasks,
            "implementation_digest": implementation_digest(), "gateway": client.descriptor(),
            "repetitions": 1, "max_phases": 2 * len(tasks),
            "max_cost_usd": 2 * len(tasks) * client.limits.max_estimated_cost_usd,
            "excluded_sources": sorted(excluded), "automatic_retry": False,
            "automatic_candidate_generation": False, "claim": "training baseline only"}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    (output / "plan.json").write_text(json.dumps({"plan": plan, "digest": digest(plan)}, indent=2))
    return plan


def run_batch(output, *, client_factory=make_client, execute=execute_case, progress=print):
    output = Path(output)
    envelope = json.loads((output / "plan.json").read_text())
    plan = envelope["plan"]
    if digest(plan) != envelope["digest"]:
        raise ValueError("training plan changed")
    if plan["implementation_digest"] != implementation_digest():
        raise ValueError("training implementation changed")
    # Exclusive creation also prevents silently replaying an interrupted paid run.
    with (output / "started.json").open("x") as stream:
        json.dump({"plan_digest": envelope["digest"]}, stream)
    store = CaseStore(output / "cases")
    results = [{"task_id": task["task_id"], "family": task["family"],
                "previously_seen": task["previously_seen"], "case_id": None,
                "status": "not_started", "error_type": None, "calls": 0,
                "known_estimated_cost_usd": 0, "measurement_complete": False,
                "feedback": None, "state_digest": None} for task in plan["tasks"]]
    summary = {"plan_digest": envelope["digest"], "complete": False,
               "results": results, "candidate_generation": "not_started",
               "eligible_for_review": []}

    def persist():
        summary["eligible_for_review"] = [r["task_id"] for r in results
            if r["feedback"] and r["feedback"]["eligible_for_manual_training_review"]
            and r["measurement_complete"] and r["error_type"] is None]
        atomic_replace_unlocked(output / "summary.json", json.dumps(summary, indent=2))

    persist()
    for index, task in enumerate(plan["tasks"]):
        state = None
        error = None
        row = results[index]
        try:
            state = store.create(task["issue"], task["repository"])
            for phase in ("investigate", "fix"):
                if phase == "fix" and store.load(state["case_id"])["status"] != "reproduced":
                    break
                if plan["implementation_digest"] != implementation_digest():
                    raise ValueError("training implementation changed during batch")
                client = client_factory()
                if client.descriptor() != plan["gateway"]:
                    raise ValueError("training gateway changed")
                progress(f"{task['task_id']} {phase}")
                try:
                    execute(store, state["case_id"], phase,
                            config=task["config"] if phase == "investigate" else None,
                            client_factory=lambda: client)
                except Exception as exc:
                    error = type(exc).__name__
                    break
            if plan["implementation_digest"] != implementation_digest():
                raise ValueError("training implementation changed during batch")
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            row["error_type"] = error
            if state is not None:
                state = store.load(state["case_id"])
                models = [r.get("agent", {}).get("model", r.get("model_evidence", {}))
                          for r in state["runs"]]
                row.update(case_id=state["case_id"], status=state["status"],
                    calls=sum(m.get("calls_reserved", 0) for m in models),
                    known_estimated_cost_usd=sum(m.get("known_estimated_cost_usd", 0) for m in models),
                    measurement_complete=bool(models) and all(
                        m.get("cost_complete") is True and m.get("measurement_valid") is True
                        for m in models), feedback=diagnose(state), state_digest=digest(state))
            persist()
        progress(json.dumps({k: row[k] for k in ("task_id", "status", "calls", "error_type")}))
    summary["complete"] = True
    persist()
    return summary
