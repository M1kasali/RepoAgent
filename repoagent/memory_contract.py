"""Reusable conformance tests for third-party memory backend adapters."""

from __future__ import annotations

import asyncio

from .memory_backend import MemoryBackend, MemoryHit


class MemoryBackendContractTests:
    """Subclass and implement ``make_backend`` to verify an adapter."""

    def make_backend(self) -> MemoryBackend:
        raise NotImplementedError(
            "MemoryBackendContractTests subclass must implement make_backend()"
        )

    def _run_with_backend(self, operation):
        async def run():
            backend = self.make_backend()
            await backend.start()
            try:
                return await operation(backend)
            finally:
                await backend.stop()

        return asyncio.run(run())

    def test_satisfies_memory_backend_protocol(self):
        async def verify(backend):
            assert isinstance(backend, MemoryBackend)

        self._run_with_backend(verify)

    def test_recall_returns_bounded_memory_hits(self):
        async def verify(backend):
            hits = await backend.recall(
                "anything", user_id="contract-test", top_k=3
            )
            assert isinstance(hits, list)
            assert len(hits) <= 3
            assert all(isinstance(hit, MemoryHit) for hit in hits)

        self._run_with_backend(verify)

    def test_store_then_recall_does_not_raise(self):
        async def verify(backend):
            await backend.store(
                "contract-test",
                [
                    {"role": "user", "content": "I use Python"},
                    {"role": "assistant", "content": "Noted."},
                ],
            )
            hits = await backend.recall(
                "Python", user_id="contract-test", top_k=5
            )
            assert isinstance(hits, list)

        self._run_with_backend(verify)

    def test_feedback_accepts_free_form_signals(self):
        async def verify(backend):
            await backend.feedback({})
            await backend.feedback({"kind": "skill_usage", "ids": ["x"]})

        self._run_with_backend(verify)

    def test_empty_or_ambiguous_owner_returns_no_hits(self):
        async def verify(backend):
            assert await backend.recall("q", top_k=3) == []
            assert (
                await backend.recall(
                    "q", user_id="u", agent_id="a", top_k=3
                )
                == []
            )

        self._run_with_backend(verify)


class MemoryBackendLifecycleContractTests:
    def make_backend(self) -> MemoryBackend:
        raise NotImplementedError

    def test_memory_backend_start_stop_is_idempotent(self):
        async def run():
            backend = self.make_backend()
            await backend.start()
            await backend.start()
            await backend.stop()
            await backend.stop()

        asyncio.run(run())

    def test_memory_backend_stop_before_start_is_safe(self):
        async def run():
            await self.make_backend().stop()

        asyncio.run(run())


__all__ = [
    "MemoryBackendContractTests",
    "MemoryBackendLifecycleContractTests",
]
