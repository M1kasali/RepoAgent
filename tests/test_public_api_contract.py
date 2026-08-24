from pathlib import Path

import repoagent
from repoagent import RepoAgent, SessionStore, WorkspaceContext, build_agent, build_arg_parser, build_welcome, main


def test_public_api_exports_current_names_only():
    assert RepoAgent is not None
    assert SessionStore is not None
    assert WorkspaceContext is not None
    assert callable(build_agent)
    assert callable(build_arg_parser)
    assert callable(build_welcome)
    assert callable(main)
    assert not hasattr(repoagent, "MiniAgent")
    assert "MiniAgent" not in repoagent.__all__


def test_build_agent_returns_repoagent(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    args = build_arg_parser().parse_args(["--cwd", str(tmp_path), "--approval", "auto"])

    agent = build_agent(args)

    assert isinstance(agent, RepoAgent)


def test_lightweight_package_split_uses_package_paths_without_legacy_shims():
    from repoagent.evaluation.evaluator import BenchmarkEvaluator
    from repoagent.evaluation.metrics import run_context_ablation_v2
    from repoagent.features.memory import LayeredMemory
    from repoagent.providers.clients import FakeModelClient as ProviderFakeModelClient

    assert BenchmarkEvaluator is not None
    assert LayeredMemory is not None
    assert ProviderFakeModelClient is not None
    assert callable(run_context_ablation_v2)
    for legacy_module in ("evaluator.py", "metrics.py", "models.py", "memory.py"):
        assert not (Path("repoagent") / legacy_module).exists()


def test_packaging_discovers_repoagent_subpackages():
    pyproject_text = Path("pyproject.toml").read_text(encoding="utf-8")

    assert "[tool.setuptools.packages.find]" in pyproject_text
    assert 'include = ["repoagent*"]' in pyproject_text
