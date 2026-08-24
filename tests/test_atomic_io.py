import json

import pytest

from repoagent.atomic_io import (
    StorageCorruptionError,
    append_jsonl,
    atomic_replace,
    read_jsonl_unlocked,
)


def test_atomic_replace_publishes_complete_file_and_cleans_temp(tmp_path):
    path = tmp_path / "state.json"

    atomic_replace(path, '{"value": 1}\n')
    atomic_replace(path, '{"value": 2}\n')

    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 2}
    assert list(tmp_path.glob("state.json.*.tmp")) == []


def test_append_jsonl_repairs_incomplete_tail_before_append(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"sequence": 1}\n{"sequence":')

    append_jsonl(path, {"sequence": 2})

    assert [json.loads(line) for line in path.read_text().splitlines()] == [
        {"sequence": 1},
        {"sequence": 2},
    ]


def test_append_jsonl_rejects_complete_corrupt_record(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"sequence": 1}\nnot-json\n', encoding="utf-8")

    with pytest.raises(StorageCorruptionError, match="invalid JSONL record"):
        append_jsonl(path, {"sequence": 2})


def test_jsonl_reader_does_not_hide_incomplete_tail_without_recovery(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"sequence": 1}', encoding="utf-8")

    with pytest.raises(StorageCorruptionError, match="incomplete JSONL tail"):
        read_jsonl_unlocked(path)
