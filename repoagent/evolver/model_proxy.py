"""Host-owned model RPC dispatcher; transport and credentials stay outside workers."""

from dataclasses import fields
import json
import threading

from ..providers.base import ModelMessage, ModelRequest, ModelTool, ToolCall
from .model_budget import BudgetedEvaluationClient, _json_default


class ModelProxyError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ModelProxyError("duplicate_key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ModelProxyError("nonfinite_value")


def decode_request(payload):
    """Accept only the model contract, never transport, pricing or credentials."""
    if not isinstance(payload, bytes) or len(payload) > 1_000_000:
        raise ModelProxyError("request_size")
    try:
        data = json.loads(
            payload, object_pairs_hook=_object, parse_constant=_reject_constant
        )
        if not isinstance(data, dict) or set(data) != {"sequence", "request"}:
            raise ModelProxyError("envelope")
        sequence, values = data["sequence"], data["request"]
        allowed = {f.name for f in fields(ModelRequest)} - {"cancellation_token"}
        if type(sequence) is not int or sequence < 0:
            raise ModelProxyError("sequence")
        if not isinstance(values, dict) or set(values) - allowed:
            raise ModelProxyError("request_fields")
        for key in ("max_output_tokens", "attempt"):
            if key in values and (type(values[key]) is not int or values[key] < 1):
                raise ModelProxyError("integer_field")
        for key in ("prompt", "turn_id", "session_id", "request_id", "call_kind"):
            if key in values and not isinstance(values[key], str):
                raise ModelProxyError("text_field")
        values["tools"] = tuple(ModelTool(**row) for row in values.get("tools", []))
        messages = []
        for row in values.get("messages", []):
            row = dict(row)
            row["tool_calls"] = tuple(ToolCall(**t) for t in row.get("tool_calls", []))
            messages.append(ModelMessage(**row))
        values["messages"] = tuple(messages)
        return sequence, ModelRequest(**values)
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise ModelProxyError("invalid_request") from exc


class HostModelProxy:
    """One trial, one serialized channel. Replays never invoke the leaf client.

    The trusted host supplies an evidence sink outside all worker mounts. The
    sink must durably persist each record before returning. A failed sink poisons
    the dispatcher. Reconstruction/resume is intentionally not supported here;
    the outer paired trial fence must reject uncertain restarts.
    """

    def __init__(self, client, *, evidence_sink):
        if not isinstance(client, BudgetedEvaluationClient):
            raise TypeError("host proxy requires a budgeted model client")
        if not callable(evidence_sink):
            raise TypeError("host proxy requires a trusted evidence sink")
        self._client = client
        self._sink = evidence_sink
        self._lock = threading.Lock()
        self._sequence = 0
        self._closed = False

    def dispatch(self, payload):
        sequence, request = decode_request(payload)
        with self._lock:
            if self._closed:
                raise ModelProxyError("proxy_closed")
            if sequence != self._sequence:
                raise ModelProxyError("unexpected_sequence")
            # Fence before any persistence or potentially billable work.
            self._closed = True
            self._sequence += 1
            self._sink({"sequence": sequence, "status": "started"})
            try:
                result = self._client.generate(request)
                response = json.dumps(
                    {
                        "sequence": sequence,
                        "result": {
                            "text": result.text,
                            "tool_calls": result.tool_calls,
                            "reasoning_content": result.reasoning_content,
                            "thinking_blocks": result.thinking_blocks,
                            "finish_reason": result.finish_reason,
                            "usage": result.usage.to_metadata(),
                            "model": result.model,
                        },
                    },
                    default=_json_default,
                    allow_nan=False,
                ).encode()
                if len(response) > 2_000_000:
                    raise ModelProxyError("response_size")
            except BaseException as exc:
                self._sink(
                    {
                        "sequence": sequence,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "model_evidence": self._client.evidence(),
                    }
                )
                if not isinstance(exc, Exception):
                    raise
                raise ModelProxyError("model_call_failed") from exc
            self._sink(
                {
                    "sequence": sequence,
                    "status": "completed",
                    "model_evidence": self._client.evidence(),
                }
            )
            self._closed = False
            return response

    def close(self):
        with self._lock:
            self._closed = True
