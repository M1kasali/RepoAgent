import json
from pathlib import Path

import pytest

from repoagent.issue_agent.training_batch import prepare_batch, run_batch
from test_issue_workflow import case as _case
from test_issue_strategy_evaluation import Client

case = _case


def row(case, tmp_path, number=1):
    state = case[0].load(case[1])
    issue = tmp_path / f"issue-{number}.json"
    config = tmp_path / f"config-{number}.json"
    url = f"https://github.com/example/project/issues/{number}"
    issue.write_text(json.dumps({"html_url": url, "title": "bug", "body": "expected behavior"}))
    config.write_text(json.dumps(case[2]))
    return {"task_id": f"task{number}", "family": f"family{number}", "issue_url": url,
            "issue_file": str(issue), "config_file": str(config),
            "repo_path": state["repository"]["path"],
            "base_revision": state["repository"]["base_revision"], "previously_seen": False}


def test_batch_rejects_heldout_and_duplicate_sources_before_creating_output(case, tmp_path):
    item = row(case, tmp_path)
    output = tmp_path / "out"
    with pytest.raises(ValueError, match="held-out"):
        prepare_batch([item], output, excluded_sources=[item["issue_url"]], client_factory=Client)
    with pytest.raises(ValueError, match="duplicate"):
        prepare_batch([item, {**item, "task_id": "another"}], output, client_factory=Client)
    assert not output.exists()


def test_batch_keeps_failures_cost_and_frozen_inputs_without_retry(case, tmp_path):
    tasks = [row(case, tmp_path, i) for i in (1, 2)]
    output = tmp_path / "out"
    plan = prepare_batch(tasks, output, client_factory=Client)
    assert plan["max_cost_usd"] == 4
    Path(tasks[0]["config_file"]).write_text("tampered source file after freeze")
    calls = []

    def execute(store, cid, phase, **options):
        state = store.load(cid)
        calls.append((state["issue"]["number"], phase))
        state["runs"].append({"phase": phase, "status": "failed",
            "model_evidence": {"cost_complete": True, "measurement_valid": True,
                               "calls_reserved": 2, "known_estimated_cost_usd": 0.1}})
        if state["issue"]["number"] == 1:
            assert options["config"]["probe"] == case[2]["probe"]
            state["status"] = "execution_failed"
            store.save(state)
            raise RuntimeError("fixture infrastructure failure")
        state["status"] = "budget_exhausted"
        store.save(state)

    result = run_batch(output, client_factory=Client, execute=execute, progress=lambda _: None)
    assert result["complete"] and len(calls) == 2
    assert result["eligible_for_review"] == []
    assert [r["status"] for r in result["results"]] == ["execution_failed", "budget_exhausted"]
    assert sum(r["calls"] for r in result["results"]) == 4
    assert sum(r["known_estimated_cost_usd"] for r in result["results"]) == 0.2
    with pytest.raises(FileExistsError):
        run_batch(output, client_factory=Client, execute=execute)


def test_changed_plan_refuses_execution(case, tmp_path):
    output = tmp_path / "out"
    prepare_batch([row(case, tmp_path)], output, client_factory=Client)
    path = output / "plan.json"
    envelope = json.loads(path.read_text())
    envelope["plan"]["tasks"][0]["config"]["probe"] = "pass"
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="plan changed"):
        run_batch(output, client_factory=Client)
    assert not (output / "started.json").exists()


def test_heldout_snapshot_is_never_opened(case, tmp_path, monkeypatch):
    item = row(case, tmp_path)

    def forbidden_read(*args):
        pytest.fail("sealed snapshot was opened")

    monkeypatch.setattr("repoagent.issue_agent.training_batch.read_snapshot", forbidden_read)
    with pytest.raises(ValueError, match="held-out"):
        prepare_batch([item], tmp_path / "out", excluded_sources=[item["issue_url"]],
                      client_factory=Client)


@pytest.mark.parametrize("failure", ["factory", "gateway", "implementation", "progress"])
def test_setup_abort_retains_paid_prefix_and_all_planned_tasks(case, tmp_path, monkeypatch, failure):
    from repoagent.issue_agent import training_batch

    output = tmp_path / "out"
    monkeypatch.setattr(training_batch, "implementation_digest", lambda: "frozen")
    prepare_batch([row(case, tmp_path, i) for i in (1, 2)], output, client_factory=Client)
    executions = []
    writes = []
    atomic_write = training_batch.atomic_replace_unlocked

    def record_write(path, value):
        writes.append(json.loads(value))
        atomic_write(path, value)

    monkeypatch.setattr(training_batch, "atomic_replace_unlocked", record_write)

    def execute(store, cid, phase, **options):
        executions.append(phase)
        state = store.load(cid)
        state["status"] = "reproduced"
        state["runs"].append({"phase": phase, "status": "completed",
            "model_evidence": {"measurement_valid": True, "cost_complete": True,
                               "calls_reserved": 3, "known_estimated_cost_usd": 0.12}})
        store.save(state)
        if failure == "implementation":
            monkeypatch.setattr(training_batch, "implementation_digest", lambda: "changed")

    def factory():
        if executions and failure == "factory":
            raise RuntimeError("client unavailable")
        client = Client()
        if executions and failure == "gateway":
            client.descriptor = lambda: {"model": "changed"}
        return client

    def progress(message):
        if executions and failure == "progress":
            raise RuntimeError("progress disconnected")

    with pytest.raises((ValueError, RuntimeError)):
        run_batch(output, client_factory=factory, execute=execute, progress=progress)
    summary = json.loads((output / "summary.json").read_text())
    assert not summary["complete"]
    assert len(summary["results"]) == 2
    paid, unstarted = summary["results"]
    assert paid["calls"] == 3 and paid["known_estimated_cost_usd"] == 0.12
    assert paid["error_type"] in {"ValueError", "RuntimeError"}
    assert unstarted["status"] == "not_started" and unstarted["case_id"] is None
    assert executions == ["investigate"]
    assert summary["eligible_for_review"] == []
    assert writes[0]["results"][0]["status"] == "not_started"
    assert writes[-1] == summary
    monkeypatch.setattr(training_batch, "implementation_digest", lambda: "frozen")
    with pytest.raises(FileExistsError):
        run_batch(output, client_factory=Client)
