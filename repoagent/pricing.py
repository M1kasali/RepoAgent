"""Explicit model pricing snapshots used by runtime accounting."""

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any


@dataclass(frozen=True)
class ModelPricing:
    """A user-supplied USD pricing snapshot for one model profile."""

    input_per_1m_usd: float
    output_per_1m_usd: float
    source: str
    currency: str = "USD"
    cache_read_per_1m_usd: float | None = None
    cache_write_per_1m_usd: float | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("input_per_1m_usd", self.input_per_1m_usd),
            ("output_per_1m_usd", self.output_per_1m_usd),
            ("cache_read_per_1m_usd", self.cache_read_per_1m_usd),
            ("cache_write_per_1m_usd", self.cache_write_per_1m_usd),
        ):
            if value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise ValueError(f"{name} must be a finite non-negative number")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("pricing source must not be empty")
        object.__setattr__(self, "source", self.source.strip())
        if self.currency != "USD":
            raise ValueError("only USD pricing is currently supported")

    @property
    def pricing_id(self) -> str:
        payload = json.dumps(
            {
                "currency": self.currency,
                "input_per_1m_usd": self.input_per_1m_usd,
                "output_per_1m_usd": self.output_per_1m_usd,
                "cache_read_per_1m_usd": self.cache_read_per_1m_usd,
                "cache_write_per_1m_usd": self.cache_write_per_1m_usd,
                "source": self.source,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "pricing_id": self.pricing_id,
            "currency": self.currency,
            "input_per_1m_usd": self.input_per_1m_usd,
            "output_per_1m_usd": self.output_per_1m_usd,
            "cache_read_per_1m_usd": self.cache_read_per_1m_usd,
            "cache_write_per_1m_usd": self.cache_write_per_1m_usd,
            "source": self.source,
        }


__all__ = ["ModelPricing"]
