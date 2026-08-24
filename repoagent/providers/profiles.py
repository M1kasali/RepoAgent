"""Validated, secret-free model configuration profiles."""

from dataclasses import dataclass, replace
import math
import re
from types import MappingProxyType
from urllib.parse import urlsplit

from ..pricing import ModelPricing


PROFILE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROVIDER_PROTOCOLS = {"ollama", "openai", "anthropic"}


@dataclass(frozen=True)
class ModelProfile:
    """One validated model endpoint without resolved credential values."""

    name: str
    provider: str
    protocol: str
    model: str
    base_url: str
    credential_envs: tuple[str, ...] = ()
    timeout_seconds: float = 300
    max_output_tokens: int = 4096
    temperature: float | None = 0.2
    top_p: float | None = None
    pricing: ModelPricing | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not PROFILE_NAME_PATTERN.fullmatch(
            self.name
        ):
            raise ValueError(f"invalid model profile name: {self.name!r}")
        if not isinstance(
            self.provider, str
        ) or not PROFILE_NAME_PATTERN.fullmatch(self.provider):
            raise ValueError(f"invalid model profile provider: {self.provider!r}")
        if self.protocol not in PROVIDER_PROTOCOLS:
            expected = ", ".join(sorted(PROVIDER_PROTOCOLS))
            raise ValueError(
                f"invalid model profile protocol: {self.protocol!r}; "
                f"expected one of: {expected}"
            )
        if (
            not isinstance(self.model, str)
            or not self.model
            or self.model != self.model.strip()
        ):
            raise ValueError("model profile model must be a non-empty trimmed string")
        self._validate_url()
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(float(self.timeout_seconds))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("model profile timeout_seconds must be positive")
        if (
            isinstance(self.max_output_tokens, bool)
            or not isinstance(self.max_output_tokens, int)
            or self.max_output_tokens < 1
        ):
            raise ValueError("model profile max_output_tokens must be positive")
        self._validate_probability("temperature", self.temperature, 0, 2)
        self._validate_probability("top_p", self.top_p, 0, 1, lower_open=True)
        if self.protocol != "ollama" and self.top_p is not None:
            raise ValueError("model profile top_p is currently supported only by ollama")
        if self.pricing is not None and not isinstance(
            self.pricing, ModelPricing
        ):
            raise ValueError("model profile pricing must be ModelPricing or None")
        credentials = tuple(self.credential_envs)
        if len(set(credentials)) != len(credentials):
            raise ValueError("model profile credential_envs must not contain duplicates")
        if any(
            not isinstance(name, str) or not ENV_NAME_PATTERN.fullmatch(name)
            for name in credentials
        ):
            raise ValueError("model profile credential_envs contains an invalid name")
        object.__setattr__(self, "credential_envs", credentials)

    def _validate_url(self) -> None:
        if not isinstance(self.base_url, str):
            raise ValueError("model profile base_url must be an absolute HTTP(S) URL")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("model profile base_url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("model profile base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("model profile base_url must not contain query or fragment")

    @staticmethod
    def _validate_probability(
        name: str,
        value: float | None,
        lower: float,
        upper: float,
        *,
        lower_open: bool = False,
    ) -> None:
        if value is None:
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"model profile {name} must be numeric or None")
        numeric = float(value)
        valid_lower = numeric > lower if lower_open else numeric >= lower
        if not math.isfinite(numeric) or not valid_lower or numeric > upper:
            bracket = "(" if lower_open else "["
            raise ValueError(
                f"model profile {name} must be in {bracket}{lower}, {upper}]"
            )

    def with_overrides(self, **changes) -> "ModelProfile":
        return replace(self, **changes)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "provider": self.provider,
            "protocol": self.protocol,
            "model": self.model,
            "base_url": self.base_url,
            "credential_envs": list(self.credential_envs),
            "timeout_seconds": self.timeout_seconds,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "pricing": self.pricing.to_dict() if self.pricing else None,
        }


BUILTIN_MODEL_PROFILES = MappingProxyType(
    {
        "ollama": ModelProfile(
            name="ollama",
            provider="ollama",
            protocol="ollama",
            model="qwen3.5:4b",
            base_url="http://127.0.0.1:11434",
            top_p=0.9,
        ),
        "openai": ModelProfile(
            name="openai",
            provider="openai",
            protocol="openai",
            model="gpt-5.4",
            base_url="https://www.right.codes/codex/v1",
            credential_envs=(
                "REPOAGENT_OPENAI_API_KEY",
                "OPENAI_API_KEY",
                "REPOAGENT_RIGHT_CODES_API_KEY",
                "RIGHT_CODES_API_KEY",
                "REPOAGENT_ANTHROPIC_API_KEY",
                "ANTHROPIC_API_KEY",
            ),
        ),
        "anthropic": ModelProfile(
            name="anthropic",
            provider="anthropic",
            protocol="anthropic",
            model="claude-sonnet-4-6",
            base_url="https://www.right.codes/claude/v1",
            credential_envs=(
                "REPOAGENT_ANTHROPIC_API_KEY",
                "ANTHROPIC_API_KEY",
                "REPOAGENT_RIGHT_CODES_API_KEY",
                "RIGHT_CODES_API_KEY",
                "REPOAGENT_OPENAI_API_KEY",
                "OPENAI_API_KEY",
            ),
        ),
        "deepseek": ModelProfile(
            name="deepseek",
            provider="deepseek",
            protocol="anthropic",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com/anthropic",
            credential_envs=(
                "REPOAGENT_DEEPSEEK_API_KEY",
                "DEEPSEEK_API_KEY",
            ),
        ),
    }
)


def get_model_profile(name: str) -> ModelProfile:
    try:
        return BUILTIN_MODEL_PROFILES[name]
    except KeyError as exc:
        expected = ", ".join(BUILTIN_MODEL_PROFILES)
        raise ValueError(
            f"unknown model profile: {name}. expected one of: {expected}"
        ) from exc


__all__ = ["BUILTIN_MODEL_PROFILES", "ModelProfile", "get_model_profile"]
