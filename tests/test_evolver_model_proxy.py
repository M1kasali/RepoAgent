import json

import pytest

from repoagent.evolver.model_proxy import (
    HostModelProxy,
    ModelProxyError,
    decode_request,
)
from test_evolver_model_budget import LeafClient, _client


def payload(sequence=0, **changes):
    return json.dumps(
        {
            "sequence": sequence,
            "request": {
                "prompt": "hello",
                "max_output_tokens": 20,
                **changes,
            },
        }
    ).encode()


def test_proxy_preserves_structured_requests_and_records_host_usage():
    leaf, records = LeafClient(), []
    proxy = HostModelProxy(_client(leaf), evidence_sink=records.append)
    result = json.loads(
        proxy.dispatch(payload(messages=[{"role": "user", "content": "hi"}]))
    )
    assert result["sequence"] == 0
    assert leaf.requests[0].messages[0].content == "hi"
    assert [r["status"] for r in records] == ["started", "completed"]
    assert records[-1]["model_evidence"]["cost_complete"]
    assert "metadata" not in result["result"]
    with pytest.raises(ModelProxyError):
        proxy.dispatch(payload())
    assert len(leaf.requests) == 1
    proxy.dispatch(payload(1))
    proxy.close()
    with pytest.raises(ModelProxyError):
        proxy.dispatch(payload(2))


@pytest.mark.parametrize(
    "bad",
    [
        b"{}",
        b"[]",
        b'{"sequence":0,"sequence":1,"request":{}}',
        payload(api_key="secret"),
        payload(cancellation_token={}),
        payload(max_output_tokens=True),
        payload(attempt=False),
        payload(timeout_seconds=float("nan")),
        payload(-1),
        payload(True),
        b"x" * 1_000_001,
    ],
)
def test_malformed_requests_never_reach_provider(bad):
    leaf = LeafClient()
    proxy = HostModelProxy(_client(leaf), evidence_sink=lambda row: None)
    with pytest.raises(ModelProxyError):
        proxy.dispatch(bad)
    assert not leaf.requests


@pytest.mark.parametrize("fail_at", [1, 2])
def test_persistence_failure_blocks_reuse(fail_at):
    leaf, rows = LeafClient(), []

    def sink(row):
        rows.append(row)
        if len(rows) == fail_at:
            raise OSError("disk full")

    proxy = HostModelProxy(_client(leaf), evidence_sink=sink)
    with pytest.raises(OSError):
        proxy.dispatch(payload())
    with pytest.raises(ModelProxyError, match="closed"):
        proxy.dispatch(payload(1))
    assert len(leaf.requests) == fail_at - 1


def test_provider_failure_is_redacted_and_blocks_retry():
    rows = []
    proxy = HostModelProxy(
        _client(LeafClient(error=RuntimeError("secret"))), evidence_sink=rows.append
    )
    with pytest.raises(ModelProxyError, match="model_call_failed"):
        proxy.dispatch(payload())
    assert "secret" not in json.dumps(rows)
    assert not rows[-1]["model_evidence"]["cost_complete"]
    with pytest.raises(ModelProxyError, match="closed"):
        proxy.dispatch(payload(1))


def test_tool_calls_round_trip_into_typed_contract():
    _, request = decode_request(
        payload(
            messages=[
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "1", "name": "read_file", "arguments": {"path": "a"}}
                    ],
                }
            ]
        )
    )
    assert request.messages[0].tool_calls[0].arguments["path"] == "a"


def test_interruption_preserves_control_flow_and_closes_proxy():
    rows = []
    proxy = HostModelProxy(
        _client(LeafClient(error=KeyboardInterrupt())), evidence_sink=rows.append
    )
    with pytest.raises(KeyboardInterrupt):
        proxy.dispatch(payload())
    assert rows[-1]["status"] == "failed"
    with pytest.raises(ModelProxyError, match="closed"):
        proxy.dispatch(payload(1))
