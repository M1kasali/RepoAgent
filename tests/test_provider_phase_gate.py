import ast
from pathlib import Path


def test_agent_loop_has_no_provider_specific_adapter_dependency():
    path = Path("repoagent/agent_loop.py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "providers.clients" not in imported_modules
    assert "repoagent.providers.clients" not in imported_modules


def test_live_provider_acceptance_is_outside_default_test_suite():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    live_test = Path("live_tests/test_live_providers.py")

    assert 'testpaths = ["tests"]' in pyproject
    assert live_test.exists()
    assert "REPOAGENT_RUN_LIVE_PROVIDER_TESTS" in live_test.read_text(
        encoding="utf-8"
    )
