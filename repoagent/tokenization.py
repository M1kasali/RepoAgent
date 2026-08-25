"""Pre-request token counting contracts for context budgeting."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
import inspect
import math


class TokenCountSource(str, Enum):
    PROVIDER = "provider"
    ESTIMATED = "estimated"


class TokenCounter(ABC):
    @property
    @abstractmethod
    def identity(self) -> str: ...

    @property
    @abstractmethod
    def source(self) -> TokenCountSource: ...

    @abstractmethod
    def count(self, text: str) -> int: ...

    def metadata(self) -> dict:
        return {
            "identity": self.identity,
            "source": self.source.value,
        }


@dataclass(frozen=True)
class CallableTokenCounter(TokenCounter):
    counter: object
    counter_identity: str
    counter_source: TokenCountSource = TokenCountSource.PROVIDER

    def __post_init__(self):
        if not callable(self.counter):
            raise TypeError("token counter must be callable")
        if not str(self.counter_identity).strip():
            raise ValueError("token counter identity must be non-empty")
        object.__setattr__(self, "counter_identity", str(self.counter_identity).strip())
        object.__setattr__(self, "counter_source", TokenCountSource(self.counter_source))

    @property
    def identity(self):
        return self.counter_identity

    @property
    def source(self):
        return self.counter_source

    def count(self, text):
        value = self.counter(str(text))
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("token counter must return a non-negative integer")
        return value


@dataclass(frozen=True)
class Utf8TokenEstimator(TokenCounter):
    provider: str = "unknown"
    model: str = "unknown"
    bytes_per_token: float = 4.0

    def __post_init__(self):
        if not math.isfinite(float(self.bytes_per_token)) or self.bytes_per_token <= 0:
            raise ValueError("bytes_per_token must be positive")

    @property
    def identity(self):
        return f"utf8_estimate:{self.provider}:{self.model}"

    @property
    def source(self):
        return TokenCountSource.ESTIMATED

    def count(self, text):
        byte_count = len(str(text).encode("utf-8"))
        if byte_count == 0:
            return 0
        return int(math.ceil(byte_count / float(self.bytes_per_token)))

    def metadata(self):
        return {
            **super().metadata(),
            "provider": self.provider,
            "model": self.model,
            "bytes_per_token": float(self.bytes_per_token),
        }


def resolve_token_counter(model_client) -> TokenCounter:
    declared_counter = inspect.getattr_static(model_client, "token_counter", None)
    if declared_counter is not None:
        explicit = getattr(model_client, "token_counter")
        if not isinstance(explicit, TokenCounter):
            raise TypeError("model_client.token_counter must implement TokenCounter")
        return explicit

    profile = getattr(model_client, "profile", None)
    provider = str(
        getattr(profile, "provider", "")
        or getattr(profile, "protocol", "")
        or type(model_client).__name__
    )
    model = str(
        getattr(profile, "model", "")
        or getattr(model_client, "model", "")
        or "unknown"
    )
    declared_count_tokens = inspect.getattr_static(model_client, "count_tokens", None)
    if declared_count_tokens is not None:
        count_tokens = getattr(model_client, "count_tokens")
        if not callable(count_tokens):
            raise TypeError("model_client.count_tokens must be callable")
        return CallableTokenCounter(
            count_tokens,
            counter_identity=f"provider:{provider}:{model}",
            counter_source=TokenCountSource.PROVIDER,
        )
    return Utf8TokenEstimator(provider=provider, model=model)


__all__ = [
    "CallableTokenCounter",
    "TokenCounter",
    "TokenCountSource",
    "Utf8TokenEstimator",
    "resolve_token_counter",
]
