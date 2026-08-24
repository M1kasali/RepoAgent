"""Explicitly opt-in acceptance checks against configured real providers."""

import os

import pytest

from repoagent.cli import _build_model_client, build_arg_parser
from repoagent.providers import ModelRequest, UsageSource, generate_model


def test_configured_live_provider_returns_typed_metered_result():
    if os.environ.get("REPOAGENT_RUN_LIVE_PROVIDER_TESTS") != "1":
        pytest.fail(
            "set REPOAGENT_RUN_LIVE_PROVIDER_TESTS=1 to acknowledge real calls"
        )
    profiles = [
        value.strip()
        for value in os.environ.get("REPOAGENT_LIVE_PROFILES", "").split(",")
        if value.strip()
    ]
    if not profiles:
        pytest.fail("REPOAGENT_LIVE_PROFILES must name at least one profile")

    for profile_name in profiles:
        args = build_arg_parser().parse_args(
            [
                "--profile",
                profile_name,
                "--max-new-tokens",
                "32",
                "--openai-timeout",
                "60",
                "--ollama-timeout",
                "60",
            ]
        )
        client = _build_model_client(args)
        profile = client.profile
        if profile.credential_envs and not any(
            os.environ.get(name) for name in profile.credential_envs
        ):
            pytest.fail(
                f"live profile {profile_name!r} has no configured credential"
            )
        result = generate_model(
            client,
            ModelRequest(
                prompt="Reply with exactly OK.",
                max_output_tokens=32,
                timeout_seconds=60,
            ),
        )
        assert result.provider
        assert result.model
        assert result.text.strip()
        assert result.usage.source is UsageSource.ACTUAL
