"""Worker-side client; load alongside the pinned package, never host credentials."""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
import json
import sys


def _encode(value):
    if is_dataclass(value):
        return {f.name: getattr(value, f.name) for f in fields(value)}
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("non-JSON model contract")


class StdioModelClient:
    supports_structured_messages = True

    def __init__(self, model, *, reader=None, writer=None):
        self.model = model
        self.reader = reader or sys.stdin.buffer
        self.writer = writer or sys.stdout.buffer
        self.sequence = 0
        self.closed = False

    def generate(self, request):
        from repoagent.providers.base import (
            ModelResult,
            ModelUsage,
            ProviderError,
            ToolCall,
        )

        if self.closed:
            raise ProviderError("model channel closed", category="evaluation_channel")
        self.closed = True
        values = {
            f.name: getattr(request, f.name)
            for f in fields(request)
            if f.name != "cancellation_token"
        }
        payload = (
            json.dumps(
                {"sequence": self.sequence, "request": values},
                default=_encode,
                allow_nan=False,
            ).encode()
            + b"\n"
        )
        if len(payload) > 1_000_000:
            raise ProviderError(
                "model request too large", category="evaluation_channel"
            )
        self.writer.write(payload)
        self.writer.flush()
        response = self.reader.readline(2_000_002)
        if len(response) > 2_000_001 or not response.endswith(b"\n"):
            raise ProviderError("model response frame", category="evaluation_channel")
        data = json.loads(response)
        if data["sequence"] != self.sequence or data["result"]["model"] != self.model:
            raise ProviderError(
                "model response identity", category="evaluation_channel"
            )
        result = dict(data["result"])
        result["usage"] = ModelUsage.from_metadata(result["usage"])
        result["tool_calls"] = tuple(ToolCall(**row) for row in result["tool_calls"])
        result = ModelResult(**result)
        self.sequence += 1
        self.closed = False
        return result
