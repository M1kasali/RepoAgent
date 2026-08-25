"""Security and redaction helpers for runtime artifacts."""

import os
import ipaddress
from urllib.parse import urlsplit

SENSITIVE_ENV_NAME_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
REDACTED_VALUE = "<redacted>"


class NetworkPolicyError(ValueError):
    pass


class NetworkPolicy:
    def __init__(self, *, allowed_hosts=(), allow_private=False, enabled=True):
        self.allowed_hosts = frozenset(
            str(host).lower().rstrip(".") for host in allowed_hosts
        )
        self.allow_private = bool(allow_private)
        self.enabled = bool(enabled)

    def validate_url(self, url):
        if not self.enabled:
            raise NetworkPolicyError("network access is disabled")
        parsed = urlsplit(str(url))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise NetworkPolicyError("network URL must be absolute HTTP(S)")
        if parsed.username or parsed.password:
            raise NetworkPolicyError("network URL must not contain credentials")
        host = parsed.hostname.lower().rstrip(".")
        if self.allowed_hosts and host not in self.allowed_hosts:
            raise NetworkPolicyError(f"network host is not allowlisted: {host}")
        if host == "localhost" or host.endswith(".localhost"):
            raise NetworkPolicyError("loopback network targets are denied")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not self.allow_private:
            if (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
                or address.is_unspecified
            ):
                raise NetworkPolicyError("private or special network targets are denied")
        return url


def _normalized_secret_names(secret_env_names):
    return {str(name).upper() for name in (secret_env_names or ())}


def looks_sensitive_env_name(name):
    upper = str(name).upper()
    return any(upper == marker or upper.endswith(marker) or upper.endswith(f"_{marker}") for marker in SENSITIVE_ENV_NAME_MARKERS)


def is_secret_env_name(name, secret_env_names=None):
    upper = str(name).upper()
    return upper in _normalized_secret_names(secret_env_names) or looks_sensitive_env_name(upper)


def configured_secret_env_items(env=None, secret_env_names=None):
    env = os.environ if env is None else env
    configured_names = _normalized_secret_names(secret_env_names)
    items = [
        (name, value)
        for name, value in env.items()
        if str(name).upper() in configured_names and value
    ]
    items.sort(key=lambda item: item[0])
    return items


def detected_secret_env_items(env=None, secret_env_names=None):
    env = os.environ if env is None else env
    items = [
        (name, value)
        for name, value in env.items()
        if is_secret_env_name(name, secret_env_names=secret_env_names) and value
    ]
    items.sort(key=lambda item: item[0])
    return items


def secret_env_summary(env=None, secret_env_names=None):
    names = [name for name, _ in configured_secret_env_items(env=env, secret_env_names=secret_env_names)]
    return {
        "secret_env_count": len(names),
        "secret_env_names": names,
    }


def detected_secret_env_summary(env=None, secret_env_names=None):
    names = [name for name, _ in detected_secret_env_items(env=env, secret_env_names=secret_env_names)]
    return {
        "secret_env_count": len(names),
        "secret_env_names": names,
    }


def redact_text(text, env=None, secret_env_names=None):
    text = str(text)
    for _, value in sorted(
        detected_secret_env_items(env=env, secret_env_names=secret_env_names),
        key=lambda item: len(item[1]),
        reverse=True,
    ):
        text = text.replace(value, REDACTED_VALUE)
    return text


def redact_artifact(value, key=None, env=None, secret_env_names=None):
    if key and is_secret_env_name(key, secret_env_names=secret_env_names):
        return REDACTED_VALUE
    if isinstance(value, dict):
        return {
            str(item_key): redact_artifact(item_value, key=item_key, env=env, secret_env_names=secret_env_names)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact_artifact(item, key=key, env=env, secret_env_names=secret_env_names) for item in value]
    if isinstance(value, tuple):
        return [redact_artifact(item, key=key, env=env, secret_env_names=secret_env_names) for item in value]
    if isinstance(value, str):
        return redact_text(value, env=env, secret_env_names=secret_env_names)
    return value


def shell_env(env=None, allowlist=(), root="."):
    env = os.environ if env is None else env
    filtered = {
        name: env[name]
        for name in allowlist
        if name in env
    }
    filtered["PWD"] = str(root)
    if "PATH" not in filtered and env.get("PATH"):
        filtered["PATH"] = env["PATH"]
    return filtered
