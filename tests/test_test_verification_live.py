"""Opt-in real Docker verification-tool acceptance; no model calls."""

import os
import subprocess

import pytest

from repoagent.sandbox_session import PersistentDockerSandboxAdapter
from test_tool_gateway import build_agent
from test_test_verification import PASSING


pytestmark = pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"),
    reason="explicit local Docker acceptance required",
)


def test_real_docker_verification_and_revalidation(tmp_path):
    adapter = PersistentDockerSandboxAdapter(
        tmp_path,
        executable=os.environ["REPOAGENT_TEST_DOCKER"],
        image=os.environ.get("REPOAGENT_TEST_DOCKER_IMAGE", "python:3.12-slim"),
    )
    agent = build_agent(tmp_path, sandbox_adapter=adapter, require_isolation=True)
    (tmp_path / "value.py").write_text("VALUE = 1\n")
    (tmp_path / "test_value.py").write_text(PASSING)
    name = ""
    try:
        passed = agent.execute_tool("run_tests", {})
        name = adapter.container_name
        assert passed.metadata["test_verification"]["verdict"] == "passed"
        assert passed.metadata["test_verification"]["freshness"] == "current"
        assert passed.metadata["sandbox_isolated"] is True
        (tmp_path / "value.py").write_text("VALUE = 2\n")
        failed = agent.execute_tool("run_tests", {})
        assert failed.metadata["test_verification"]["verdict"] == "failed"
        assert (
            failed.metadata["test_verification"]["source_digest"]
            != passed.metadata["test_verification"]["source_digest"]
        )
    finally:
        adapter.stop()
    assert name
    result = subprocess.run(
        [
            adapter.executable,
            "container",
            "ls",
            "--all",
            "--filter",
            f"name=^/{name}$",
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert not result.stdout.strip()
