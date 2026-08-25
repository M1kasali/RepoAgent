import json
from dataclasses import FrozenInstanceError

import pytest

from repoagent.compaction import (
    HISTORY_COMPACTION_STRATEGY,
    DeterministicHistoryCompactor,
)
from repoagent.tokenization import Utf8TokenEstimator


def build_compactor(recent_window=2):
    return DeterministicHistoryCompactor(
        Utf8TokenEstimator(provider="test", model="test", bytes_per_token=1),
        recent_window=recent_window,
    )


def test_compaction_records_are_deterministic_and_cover_every_source_entry():
    history = [
        {
            "role": "tool",
            "name": "read_file",
            "args": {"path": "src/app.py"},
            "content": "API_TOKEN=do-not-persist-this",
            "created_at": "2026-08-25T01:00:00+00:00",
        },
        {
            "role": "tool",
            "name": "read_file",
            "args": {"path": "src/app.py"},
            "content": "API_TOKEN=do-not-persist-this",
            "created_at": "2026-08-25T01:01:00+00:00",
        },
        {"role": "user", "content": "inspect it", "created_at": "2026-08-25T01:02:00+00:00"},
        {"role": "assistant", "content": "working", "created_at": "2026-08-25T01:03:00+00:00"},
    ]
    summaries = {
        "src/app.py": {
            "summary": "application entrypoint",
            "created_at": "2026-08-25T00:00:00+00:00",
            "freshness": "sha256:file-state",
        }
    }
    compactor = build_compactor()

    first = compactor.compact(
        history, 500, file_summary_lookup=summaries.get
    )
    second = compactor.compact(
        history, 500, file_summary_lookup=summaries.get
    )

    assert first.provenance_digest == second.provenance_digest
    assert [record.record_id for record in first.records] == [
        record.record_id for record in second.records
    ]
    assert [record.source_index for record in first.records] == [0, 1, 2, 3]
    assert [record.operation for record in first.records] == [
        "reuse_file_summary",
        "collapse_duplicate_read",
        "retain_recent",
        "retain_recent",
    ]
    assert first.records[1].provenance["duplicate_of_index"] == 0
    assert first.records[0].provenance["summary_source"] == "memory.file_summary"
    assert first.records[0].provenance["summary_freshness"] == "sha256:file-state"
    assert "src/app.py -> application entrypoint" in first.rendered

    serialized_records = json.dumps(first.details()["records"], sort_keys=True)
    assert "do-not-persist-this" not in serialized_records
    assert "application entrypoint" not in serialized_records


def test_compaction_records_budget_clipping_and_drops_without_exceeding_budget():
    history = [
        {
            "role": "tool",
            "name": "run_shell",
            "args": {"command": "pytest -q"},
            "content": "failure-output " * 30,
            "created_at": "2026-08-25T01:00:00+00:00",
        },
        {"role": "user", "content": "A" * 200, "created_at": "2026-08-25T01:01:00+00:00"},
        {"role": "assistant", "content": "B" * 200, "created_at": "2026-08-25T01:02:00+00:00"},
    ]
    compactor = build_compactor()

    result = compactor.compact(history, 80)
    details = result.details()

    assert compactor.token_counter.count(result.rendered) <= 80
    assert details["strategy"] == HISTORY_COMPACTION_STRATEGY
    assert details["compaction_applied"] is True
    assert details["source_entry_count"] == 3
    assert details["clipped_entry_count"] + details["dropped_entry_count"] > 0
    assert details["raw_digest"].startswith("sha256:")
    assert details["rendered_digest"].startswith("sha256:")


def test_compaction_records_are_immutable():
    result = build_compactor().compact(
        [{"role": "user", "content": "hello", "created_at": "now"}],
        100,
    )

    with pytest.raises(FrozenInstanceError):
        result.records[0].operation = "changed"
    with pytest.raises(TypeError):
        result.records[0].provenance["changed"] = True


def test_raw_history_bypasses_compaction_for_ablation():
    compactor = build_compactor()
    history = [
        {
            "role": "tool",
            "name": "run_shell",
            "args": {"command": "pytest -q"},
            "content": "full first line\nfull second line",
        }
    ]

    assert compactor.raw_history(history) == (
        'Transcript:\n[tool:run_shell] {"command": "pytest -q"}\n'
        "full first line\nfull second line"
    )


def test_empty_history_preserves_explicit_empty_marker():
    result = build_compactor().compact([], 100)

    assert result.raw == "Transcript:\n- empty"
    assert result.rendered == result.raw
    assert result.records == ()
    assert result.details()["compaction_applied"] is False
