import asyncio
import os
from pathlib import Path

import pytest

from repoagent.evidence import (
    EvidenceBundleBuilder,
    IncompleteEvidenceError,
    verify_evidence_bundle,
)
from repoagent.evaluation.tracing import measure_tracing_overhead
from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext
from repoagent.run_store import RunStore
from repoagent.spine import Text, TurnOutcome, TurnRequest, TurnRuntime, TurnState
from repoagent.trace_inspection import main as inspect_main
from repoagent.tracing import (
    SEMANTIC_EVENTS,
    current_trace_context,
    validate_semantic_event,
)


class _Runner:
    async def run(self, request, emit, drain):
        await emit(Text("done"))
        return TurnOutcome(
            turn_id=request.turn_id,
            request_id=request.request_id,
            session_id=request.session_id,
            state=TurnState.COMPLETED,
            final_answer="done",
            explicit_reply=True,
        )


def _completed_turn(store):
    request = TurnRequest.create(session_id="session", text="inspect")

    async def emit(_event):
        return None

    asyncio.run(TurnRuntime(_Runner(), store).execute(request, emit))
    return request


def test_semantic_event_contract_requires_namespaced_attributes():
    assert SEMANTIC_EVENTS["provider.call.completed"].stage == "provider"
    validate_semantic_event(
        "provider.call.completed",
        {"provider_call_id": "call-1", "status": "completed"},
    )
    with pytest.raises(ValueError, match="provider_call_id"):
        validate_semantic_event("provider.call.completed", {"status": "completed"})
    with pytest.raises(ValueError, match="unknown semantic event"):
        validate_semantic_event("unscoped", {})


def test_turn_runtime_propagates_one_trace_across_scheduler_runtime_and_delivery(tmp_path):
    store = RunStore(tmp_path / "runs")
    request = _completed_turn(store)
    rows = store.load_turn_events(request.turn_id)

    assert {row["trace_id"] for row in rows} == {request.trace_context.trace_id}
    assert [row["stage"] for row in rows] == [
        "scheduler",
        "runtime",
        "delivery",
        "delivery",
    ]
    assert all(row["span_id"] == request.trace_context.span_id for row in rows)
    assert current_trace_context() is None


def test_recovery_terminal_inherits_accepted_trace_context(tmp_path):
    store = RunStore(tmp_path / "runs")
    request = TurnRequest.create(session_id="session", text="queued")
    TurnRuntime(_Runner(), store).accept(request)

    assert RunStore(store.root).recover_incomplete_turns() == [str(request.turn_id)]
    rows = store.load_turn_events(request.turn_id)
    assert rows[-1]["kind"] == "turn.failed"
    assert rows[-1]["trace_id"] == request.trace_context.trace_id
    assert rows[-1]["stage"] == "delivery"


def test_run_store_redacts_at_write_seam_and_supports_query_export_retention(tmp_path):
    secret = "secret-value"

    def redact(value):
        if isinstance(value, dict):
            return {key: redact(item) for key, item in value.items()}
        if isinstance(value, str):
            return value.replace(secret, "<redacted>")
        return value

    store = RunStore(tmp_path / "runs", redactor=redact)
    request = _completed_turn(store)
    store.append_trace(
        request.turn_id,
        {
            "event": "model_call_accounted",
            "stage": "provider",
            "provider_call_id": "call-1",
            "content": secret,
        },
    )
    store.append_trace(
        request.turn_id,
        {"event": "tool_executed", "stage": "tool", "tool_call_id": "tool-1"},
    )

    provider_rows = store.query_trace(
        request.turn_id, stages=("provider",), provider_call_id="call-1"
    )
    assert provider_rows[0]["content"] == "<redacted>"
    assert store.export_trace(request.turn_id)["turn_events"][-1]["kind"] == "turn.completed"

    old = _completed_turn(store)
    old_path = store.run_dir(old.turn_id)
    os.utime(old_path, (1, 1))
    removed = store.apply_retention(keep_latest=1, older_than=2)
    assert str(old.turn_id) in removed
    assert store.run_dir(request.turn_id).exists()


def test_evidence_bundle_is_self_contained_checksummed_and_requires_terminal(tmp_path):
    store = RunStore(tmp_path / "runs")
    request = _completed_turn(store)
    store.append_trace(request.turn_id, {"event": "run_finished", "stage": "delivery"})
    store.write_report(request.turn_id, {"status": "completed"})

    destination = tmp_path / "bundle"
    EvidenceBundleBuilder(store).build(request.turn_id, destination)
    manifest = verify_evidence_bundle(destination)

    assert manifest["run_id"] == str(request.turn_id)
    assert manifest["terminal_kind"] == "turn.completed"
    assert all(not Path(row["path"]).is_absolute() for row in manifest["files"])
    assert {row["path"] for row in manifest["files"]} >= {
        "turn.json",
        "turn_events.jsonl",
        "trace.jsonl",
        "report.json",
    }

    (destination / "report.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch|size mismatch"):
        verify_evidence_bundle(destination)

    incomplete = TurnRequest.create(session_id="session", text="queued")
    TurnRuntime(_Runner(), store).accept(incomplete)
    with pytest.raises(IncompleteEvidenceError, match="terminal"):
        EvidenceBundleBuilder(store).build(incomplete.turn_id, tmp_path / "incomplete")


def test_tracing_overhead_experiment_and_inspection_cli(tmp_path, capsys):
    result = measure_tracing_overhead(event_count=20, payload_chars=16)
    assert result["schema"] == "repoagent.tracing-overhead/v1"
    assert result["tracing"]["storage_bytes"] > 0
    assert result["tracing"]["bytes_per_event"] > 16

    store = RunStore(tmp_path / "runs")
    request = _completed_turn(store)
    store.append_trace(request.turn_id, {"event": "run_finished", "stage": "delivery"})
    assert inspect_main([str(request.turn_id), "--root", str(store.root)]) == 0
    output = capsys.readouterr().out
    assert f"run: {request.turn_id}" in output
    assert "runtime events: 1" in output


def test_agent_trace_correlates_memory_provider_delivery_ledger_and_report(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    agent = RepoAgent(
        model_client=FakeModelClient(["<final>done</final>"]),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="never",
    )

    assert agent.ask("inspect") == "done"
    run_id = agent.current_task_state.run_id
    runtime_rows = agent.run_store.query_trace(run_id)
    turn_rows = agent.run_store.load_turn_events(run_id)
    calls = agent.run_store.load_model_calls(run_id)
    report = agent.run_store.load_report(run_id)

    trace_id = turn_rows[0]["trace_id"]
    assert {row["trace_id"] for row in runtime_rows} == {trace_id}
    assert {row["stage"] for row in runtime_rows} >= {
        "memory",
        "provider",
        "delivery",
    }
    assert calls[0]["trace_id"] == trace_id
    assert calls[0]["stage"] == "provider"
    assert calls[0]["parent_span_id"] == turn_rows[0]["span_id"]
    assert report["provider_call_ids"] == [calls[0]["provider_call_id"]]
    assert report["usage"]["model_call_count"] == 1
