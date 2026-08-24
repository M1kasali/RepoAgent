from pathlib import Path

import repoagent
from repoagent import RepoAgent, SessionStore, WorkspaceContext, build_agent, build_arg_parser, build_welcome, main
from repoagent.run_store import RunStore
from repoagent.spine import RuntimeEvent, TurnRequest


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


def test_build_agent_recovers_incomplete_turns_on_host_start(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    store = RunStore(tmp_path / ".repoagent" / "runs")
    request = TurnRequest.create(session_id="abandoned", text="queued")
    accepted = RuntimeEvent(
        kind="turn.accepted",
        turn_id=request.turn_id,
        session_id=request.session_id,
        request_id=request.request_id,
        sequence=1,
        payload={
            "request": {"text": request.text, "work_class": "foreground"}
        },
    )
    store.commit_turn_event(
        request.turn_id,
        accepted.to_dict(),
        {
            "format_version": 1,
            "turn_id": str(request.turn_id),
            "session_id": str(request.session_id),
            "request_id": str(request.request_id),
            "state": "accepted",
            "request": {"text": request.text, "work_class": "foreground"},
            "outcome": None,
        },
    )
    args = build_arg_parser().parse_args(
        ["--cwd", str(tmp_path), "--approval", "auto"]
    )

    agent = build_agent(args)

    assert agent.recovered_turn_ids == (str(request.turn_id),)
    assert store.load_turn_events(request.turn_id)[-1]["kind"] == "turn.failed"


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
