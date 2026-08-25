"""Signed, attenuable capabilities enforced by the Tool Gateway."""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterable
from dataclasses import dataclass
import hashlib
import hmac
import json
import secrets
import time

from .tool_contracts import ToolEffect


CAPABILITY_FORMAT_VERSION = 1
DEFAULT_CAPABILITY_TTL_SECONDS = 24 * 60 * 60


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def capability_token_digest(token: str) -> str:
    if not token:
        return ""
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _non_empty_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"capability {name} must be a non-empty trimmed string")
    return value


@dataclass(frozen=True)
class CapabilityClaims:
    issuer_id: str
    token_id: str
    subject_id: str
    session_id: str
    effects: tuple[ToolEffect, ...]
    tools: tuple[str, ...]
    issued_at: int
    expires_at: int
    parent_token_id: str = ""

    def __post_init__(self) -> None:
        for name in ("issuer_id", "token_id", "subject_id", "session_id"):
            _non_empty_text(name, getattr(self, name))
        if self.parent_token_id:
            _non_empty_text("parent_token_id", self.parent_token_id)
            if self.parent_token_id == self.token_id:
                raise ValueError("capability token cannot be its own parent")
        effects = tuple(self.effects)
        if any(
            not isinstance(effect, ToolEffect) or effect is ToolEffect.UNKNOWN
            for effect in effects
        ):
            raise ValueError("capability effects must be known ToolEffect values")
        if len(set(effects)) != len(effects):
            raise ValueError("capability effects must not contain duplicates")
        tools = tuple(self.tools)
        if any(
            not isinstance(tool, str) or not tool or tool != tool.strip()
            for tool in tools
        ):
            raise ValueError("capability tools must be non-empty trimmed strings")
        if len(set(tools)) != len(tools):
            raise ValueError("capability tools must not contain duplicates")
        if isinstance(self.issued_at, bool) or not isinstance(self.issued_at, int):
            raise ValueError("capability issued_at must be an integer")
        if isinstance(self.expires_at, bool) or not isinstance(self.expires_at, int):
            raise ValueError("capability expires_at must be an integer")
        if self.expires_at <= self.issued_at:
            raise ValueError("capability expiry must be after issuance")
        object.__setattr__(self, "effects", effects)
        object.__setattr__(self, "tools", tools)

    def to_payload(self) -> dict:
        return {
            "format_version": CAPABILITY_FORMAT_VERSION,
            "issuer_id": self.issuer_id,
            "token_id": self.token_id,
            "subject_id": self.subject_id,
            "session_id": self.session_id,
            "effects": [effect.value for effect in self.effects],
            "tools": list(self.tools),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "parent_token_id": self.parent_token_id,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "CapabilityClaims":
        if not isinstance(payload, dict):
            raise ValueError("capability payload must be an object")
        if payload.get("format_version") != CAPABILITY_FORMAT_VERSION:
            raise ValueError("unsupported capability format version")
        return cls(
            issuer_id=payload["issuer_id"],
            token_id=payload["token_id"],
            subject_id=payload["subject_id"],
            session_id=payload["session_id"],
            effects=tuple(ToolEffect(value) for value in payload["effects"]),
            tools=tuple(payload["tools"]),
            issued_at=payload["issued_at"],
            expires_at=payload["expires_at"],
            parent_token_id=payload.get("parent_token_id", ""),
        )

    def scope(self) -> dict:
        return {
            "subject_id": self.subject_id,
            "session_id": self.session_id,
            "effects": [effect.value for effect in self.effects],
            "tools": list(self.tools),
            "parent_token_id": self.parent_token_id,
        }


@dataclass(frozen=True)
class CapabilityDecision:
    allowed: bool
    reason: str
    token_id: str = ""
    token_digest: str = ""

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "token_id": self.token_id,
            "token_digest": self.token_digest,
        }


class CapabilityAuthority:
    """Issue, attenuate, and verify workspace-local HMAC capabilities."""

    def __init__(
        self,
        *,
        secret: bytes | None = None,
        issuer_id: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._secret = secret or secrets.token_bytes(32)
        if not isinstance(self._secret, bytes) or len(self._secret) < 16:
            raise ValueError("capability authority secret must be at least 16 bytes")
        self.issuer_id = issuer_id or "authority-" + secrets.token_hex(8)
        _non_empty_text("issuer_id", self.issuer_id)
        self._clock = clock

    @staticmethod
    def _normalize_effects(effects: Iterable[ToolEffect]) -> tuple[ToolEffect, ...]:
        values = tuple(dict.fromkeys(effects))
        if any(
            not isinstance(effect, ToolEffect) or effect is ToolEffect.UNKNOWN
            for effect in values
        ):
            raise ValueError("capability effects must be known ToolEffect values")
        return values

    @staticmethod
    def _normalize_tools(tools: Iterable[str]) -> tuple[str, ...]:
        values = tuple(dict.fromkeys(str(tool) for tool in tools))
        if any(not tool or tool != tool.strip() for tool in values):
            raise ValueError("capability tools must be non-empty trimmed strings")
        return values

    def issue(
        self,
        *,
        subject_id: str,
        session_id: str,
        effects: Iterable[ToolEffect],
        tools: Iterable[str],
        parent_token: str = "",
        ttl_seconds: int = DEFAULT_CAPABILITY_TTL_SECONDS,
    ) -> str:
        subject_id = _non_empty_text("subject_id", subject_id)
        session_id = _non_empty_text("session_id", session_id)
        normalized_effects = self._normalize_effects(effects)
        normalized_tools = self._normalize_tools(tools)
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise ValueError("capability ttl_seconds must be an integer")
        if ttl_seconds <= 0:
            raise ValueError("capability ttl_seconds must be positive")
        parent_token_id = ""
        parent_expires_at = None
        if parent_token:
            parent = self.verify(parent_token)
            if parent is None:
                raise ValueError("parent capability token is invalid or expired")
            if not set(normalized_effects).issubset(parent.effects):
                raise ValueError("child capability cannot expand effects")
            if not set(normalized_tools).issubset(parent.tools):
                raise ValueError("child capability cannot expand tools")
            parent_token_id = parent.token_id
            parent_expires_at = parent.expires_at
        issued_at = int(self._clock())
        expires_at = issued_at + ttl_seconds
        if parent_expires_at is not None:
            expires_at = min(expires_at, parent_expires_at)
        if expires_at <= issued_at:
            raise ValueError("parent capability expires too soon for delegation")
        claims = CapabilityClaims(
            issuer_id=self.issuer_id,
            token_id="cap-" + secrets.token_hex(12),
            subject_id=subject_id,
            session_id=session_id,
            effects=normalized_effects,
            tools=normalized_tools,
            issued_at=issued_at,
            expires_at=expires_at,
            parent_token_id=parent_token_id,
        )
        payload = json.dumps(
            claims.to_payload(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        encoded = _encode(payload)
        signature = hmac.new(
            self._secret, encoded.encode("ascii"), hashlib.sha256
        ).digest()
        return f"{encoded}.{_encode(signature)}"

    def verify(self, token: str) -> CapabilityClaims | None:
        if not isinstance(token, str) or token.count(".") != 1:
            return None
        encoded, supplied_signature = token.split(".", 1)
        try:
            expected_signature = _encode(
                hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).digest()
            )
        except UnicodeEncodeError:
            return None
        if not hmac.compare_digest(expected_signature, supplied_signature):
            return None
        try:
            payload = json.loads(_decode(encoded).decode("utf-8"))
            claims = CapabilityClaims.from_payload(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        now = int(self._clock())
        if claims.issuer_id != self.issuer_id:
            return None
        if claims.issued_at > now + 60 or now >= claims.expires_at:
            return None
        return claims

    def authorize(
        self,
        token: str,
        *,
        subject_id: str,
        session_id: str,
        tool_name: str,
        effect: ToolEffect,
    ) -> CapabilityDecision:
        digest = capability_token_digest(token)
        if not token:
            return CapabilityDecision(False, "missing_token")
        claims = self.verify(token)
        if claims is None:
            return CapabilityDecision(
                False, "invalid_or_expired_token", token_digest=digest
            )
        common = {"token_id": claims.token_id, "token_digest": digest}
        if claims.subject_id != subject_id:
            return CapabilityDecision(False, "subject_mismatch", **common)
        if claims.session_id != session_id:
            return CapabilityDecision(False, "session_mismatch", **common)
        if tool_name not in claims.tools:
            return CapabilityDecision(False, "tool_not_granted", **common)
        if effect not in claims.effects:
            return CapabilityDecision(False, "effect_not_granted", **common)
        return CapabilityDecision(True, "granted", **common)


__all__ = [
    "CAPABILITY_FORMAT_VERSION",
    "CapabilityAuthority",
    "CapabilityClaims",
    "CapabilityDecision",
    "capability_token_digest",
]
