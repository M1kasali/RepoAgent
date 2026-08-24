import json

import pytest

from repoagent import (
    CallEfficiencyEntry,
    InputTokenSemantics,
    ModelPricing,
    ModelUsage,
    UsageSource,
)
from repoagent.call_replay import (
    CALL_REPLAY_SCHEMA,
    CallReplayError,
    file_digest,
    main,
    replay_call_ledger,
)


def _entry():
    return CallEfficiencyEntry(
        provider_call_id="request:1:0",
        turn_id="turn",
        request_id="request",
        session_id="session",
        agent_attempt=1,
        provider_attempt=0,
        provider="openai",
        model="model",
        status="completed",
        duration_ms=12,
        usage=ModelUsage(
            100,
            20,
            120,
            cache_read_tokens=40,
            source=UsageSource.ACTUAL,
            input_token_semantics=InputTokenSemantics.TOTAL,
        ),
        pricing=ModelPricing(
            2,
            10,
            "frozen test snapshot",
            cache_read_per_1m_usd=0.2,
        ),
        finish_reason="stop",
    )


def _write_ledger(path, rows):
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_replay_rebuilds_frozen_call_evidence_deterministically(tmp_path):
    path = tmp_path / "calls.jsonl"
    _write_ledger(path, [_entry().to_dict()])
    digest = file_digest(path)

    first = replay_call_ledger(path, expected_source_digest=digest)
    second = replay_call_ledger(path, expected_source_digest=digest)

    assert first == second
    assert first["schema"] == CALL_REPLAY_SCHEMA
    assert first["row_count"] == 1
    assert first["equivalent"] is True
    assert first["findings"] == []
    assert first["replay_digest"].startswith("sha256:")


def test_replay_detects_tampered_cost_even_with_rebound_source(tmp_path):
    path = tmp_path / "calls.jsonl"
    row = _entry().to_dict()
    row["estimated_cost_usd"] = 99
    _write_ledger(path, [row])

    replay = replay_call_ledger(
        path,
        expected_source_digest=file_digest(path),
    )

    assert replay["equivalent"] is False
    assert replay["findings"] == ["call_evidence_mismatch:0"]


def test_replay_requires_external_digest_binding(tmp_path):
    path = tmp_path / "calls.jsonl"
    _write_ledger(path, [_entry().to_dict()])

    with pytest.raises(CallReplayError, match="required"):
        replay_call_ledger(path, expected_source_digest="")

    replay = replay_call_ledger(
        path,
        expected_source_digest="sha256:" + "0" * 64,
    )
    assert replay["equivalent"] is False
    assert "source_digest_not_expected" in replay["findings"]


def test_replay_rejects_pricing_identity_tampering(tmp_path):
    path = tmp_path / "calls.jsonl"
    row = _entry().to_dict()
    row["pricing"]["source"] = "changed"
    _write_ledger(path, [row])

    with pytest.raises(CallReplayError, match="pricing_id mismatch"):
        replay_call_ledger(path, expected_source_digest=file_digest(path))


def test_replay_detects_duplicate_call_identity(tmp_path):
    path = tmp_path / "calls.jsonl"
    row = _entry().to_dict()
    _write_ledger(path, [row, row])

    replay = replay_call_ledger(
        path,
        expected_source_digest=file_digest(path),
    )
    assert replay["equivalent"] is False
    assert replay["findings"] == ["duplicate_provider_call_id:request:1:0"]


def test_replay_cli_writes_report_and_returns_equivalence_status(tmp_path):
    source = tmp_path / "calls.jsonl"
    output = tmp_path / "replay.json"
    _write_ledger(source, [_entry().to_dict()])

    result = main(
        [
            "--source",
            str(source),
            "--expected-source-digest",
            file_digest(source),
            "--output",
            str(output),
        ]
    )

    assert result == 0
    assert json.loads(output.read_text(encoding="utf-8"))["equivalent"] is True


def test_replay_cli_refuses_to_overwrite_source(tmp_path):
    source = tmp_path / "calls.jsonl"
    _write_ledger(source, [_entry().to_dict()])

    with pytest.raises(SystemExit):
        main(
            [
                "--source",
                str(source),
                "--expected-source-digest",
                file_digest(source),
                "--output",
                str(source),
            ]
        )

    assert source.read_text(encoding="utf-8").strip()


@pytest.mark.parametrize(
    "content,message",
    [
        ("", "at least one"),
        ("\n", "blank row"),
        ("not-json\n", "invalid JSON"),
        (json.dumps({"format_version": 999}) + "\n", "format_version"),
    ],
)
def test_replay_rejects_malformed_ledgers(tmp_path, content, message):
    path = tmp_path / "calls.jsonl"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(CallReplayError, match=message):
        replay_call_ledger(path, expected_source_digest=file_digest(path))
