"""Typed identifiers shared by the runtime spine."""

from typing import NewType
from uuid import uuid4


TurnId = NewType("TurnId", str)
SessionId = NewType("SessionId", str)
RequestId = NewType("RequestId", str)


def new_turn_id() -> TurnId:
    return TurnId("turn_" + uuid4().hex)


def new_request_id() -> RequestId:
    return RequestId("request_" + uuid4().hex)
