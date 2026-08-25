"""Pluggable long-term memory contract and deterministic in-memory adapter."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
import math
import re
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class MemoryHit:
    """One pre-rendered memory candidate returned by a backend."""

    text: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.text, str):
            raise TypeError("memory hit text must be a string")
        if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
            raise TypeError("memory hit score must be numeric")
        score = float(self.score)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("memory hit score must be between 0 and 1")
        if not isinstance(self.metadata, dict):
            raise TypeError("memory hit metadata must be a dict")
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "metadata", deepcopy(self.metadata))


@runtime_checkable
class MemoryBackend(Protocol):
    """Single async seam implemented by long-term memory adapters."""

    async def recall(
        self,
        query: str,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        top_k: int,
    ) -> list[MemoryHit]: ...

    async def store(
        self,
        session_id: str,
        messages: list[dict[str, Any]],
    ) -> None: ...

    async def feedback(self, signals: dict[str, Any]) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class MemoryBackendNotStartedError(RuntimeError):
    pass


def _tokens(value):
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_]+", str(value))
    }


class InMemoryMemoryBackend:
    """Deterministic test adapter with session-scoped lexical recall.

    The fake treats ``user_id`` or ``agent_id`` as the session identifier used
    by ``store``. It provides immediate read-after-write visibility for tests;
    production adapters may have weaker indexing guarantees.
    """

    def __init__(self):
        self._sessions: dict[str, list[dict[str, Any]]] = {}
        self._feedback: list[dict[str, Any]] = []
        self._started = False
        self._lock = asyncio.Lock()

    @property
    def started(self):
        return self._started

    async def start(self):
        self._started = True

    async def stop(self):
        self._started = False

    async def recall(
        self,
        query,
        *,
        user_id=None,
        agent_id=None,
        top_k,
    ):
        self._require_started()
        owner_ids = [
            str(owner).strip()
            for owner in (user_id, agent_id)
            if owner is not None and str(owner).strip()
        ]
        if len(owner_ids) != 1:
            return []
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise TypeError("memory recall top_k must be an integer")
        if top_k <= 0:
            return []

        session_id = owner_ids[0]
        query_tokens = _tokens(query)
        async with self._lock:
            messages = deepcopy(self._sessions.get(session_id, []))

        ranked = []
        for index, message in enumerate(messages):
            text = str(message.get("content", "")).strip()
            if not text:
                continue
            text_tokens = _tokens(text)
            overlap = len(query_tokens & text_tokens)
            if query_tokens and overlap == 0:
                continue
            score = overlap / len(query_tokens) if query_tokens else 0.0
            ranked.append(
                (
                    (score, index),
                    MemoryHit(
                        text=text,
                        score=score,
                        metadata={
                            "session_id": session_id,
                            "message_index": index,
                            "role": str(message.get("role", "")),
                            "owner_track": (
                                "user" if user_id is not None else "agent"
                            ),
                        },
                    ),
                )
            )
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [hit for _, hit in ranked[:top_k]]

    async def store(self, session_id, messages):
        self._require_started()
        session_id = str(session_id).strip()
        if not session_id:
            raise ValueError("memory session_id must be non-empty")
        if not isinstance(messages, list):
            raise TypeError("memory messages must be a list")
        normalized = []
        for message in messages:
            if not isinstance(message, dict):
                raise TypeError("each memory message must be a dict")
            normalized.append(deepcopy(message))
        async with self._lock:
            self._sessions.setdefault(session_id, []).extend(normalized)

    async def feedback(self, signals):
        self._require_started()
        if not isinstance(signals, dict):
            raise TypeError("memory feedback signals must be a dict")
        async with self._lock:
            self._feedback.append(deepcopy(signals))

    async def snapshot(self):
        """Return isolated fake state for test assertions."""
        async with self._lock:
            return {
                "sessions": deepcopy(self._sessions),
                "feedback": deepcopy(self._feedback),
                "started": self._started,
            }

    def _require_started(self):
        if not self._started:
            raise MemoryBackendNotStartedError("memory backend is not started")


__all__ = [
    "InMemoryMemoryBackend",
    "MemoryBackend",
    "MemoryBackendNotStartedError",
    "MemoryHit",
]
