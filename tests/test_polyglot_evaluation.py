import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from repoagent import FakeModelClient, RepoAgent, SessionStore
from repoagent.pricing import ModelPricing
from repoagent.providers import (
    ModelEvent,
    ModelProfile,
    ModelResult,
    ModelUsage,
    ProviderError,
    ToolCall,
    UsageSource,
)
from repoagent.evaluation.cli import main
from repoagent.evaluation.polyglot import (
    PolyglotAdapter,
    PolyglotContainerGrader,
    polyglot_plan_payload,
    polyglot_workspace_context,
    prepare_polyglot_runner_workspace,
)
from repoagent.tool_execution import ProcessOutcome
from repoagent.evaluation.polyglot_campaign import PolyglotSingleTaskCampaign
from repoagent.evaluation.polyglot_suite import CampaignBudget, PolyglotCampaign
from repoagent.evaluation.provider_probe import (
    ProviderProbeError,
    ProviderProbeResult,
)
from repoagent.atomic_io import file_lock
from scripts.run_polyglot_campaign import build_arg_parser as campaign_arg_parser


def _exercise(root: Path, language: str, name: str, *, bad_solution=None):
    exercise = root / language / "exercises" / "practice" / name
    (exercise / ".docs").mkdir(parents=True)
    (exercise / ".meta").mkdir()
    (exercise / "src").mkdir()
    (exercise / "tests").mkdir()
    (exercise / ".docs" / "introduction.md").write_text("Intro", encoding="utf-8")
    (exercise / ".docs" / "instructions.md").write_text(
        f"Implement {name}", encoding="utf-8"
    )
    (exercise / "src" / "answer.txt").write_text("TODO\n", encoding="utf-8")
    (exercise / "tests" / "hidden.txt").write_text("SECRET TEST\n", encoding="utf-8")
    (exercise / ".meta" / "example.txt").write_text("SECRET ANSWER\n", encoding="utf-8")
    config = {
        "blurb": f"Exercise {name}",
        "files": {
            "solution": [bad_solution or "src/answer.txt"],
            "test": ["tests/hidden.txt"],
            "example": [".meta/example.txt"],
        },
    }
    (exercise / ".meta" / "config.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    return exercise


def _dataset(tmp_path):
    for language in ("python", "rust"):
        _exercise(tmp_path, language, "alpha")
        _exercise(tmp_path, language, "beta")
    return tmp_path


def test_live_campaign_parser_preserves_explicit_cache_pricing():
    args = campaign_arg_parser().parse_args(
        [
            "--dataset",
            "dataset",
            "--output",
            "output",
            "--max-input-tokens-per-call",
            "12000",
            "--hard-cost-cap-usd",
            "0.13",
            "--input-cost-per-1m-usd",
            "0.44",
            "--output-cost-per-1m-usd",
            "1.32",
            "--cache-read-cost-per-1m-usd",
            "0.014",
            "--cache-write-cost-per-1m-usd",
            "0",
            "--context-window-tokens",
            "1000000",
            "--pricing-source",
            "official",
            "--image",
            "polyglot:1",
        ]
    )

    assert args.cache_read_cost_per_1m_usd == 0.014
    assert args.cache_write_cost_per_1m_usd == 0
    assert args.context_window_tokens == 1_000_000


def test_polyglot_adapter_separates_runner_and_grader_inputs(tmp_path):
    loaded = PolyglotAdapter().load(
        _dataset(tmp_path), languages=("python", "rust"), limit=3
    )
    assert [item.runner.task_id for item in loaded["instances"]] == [
        "python/alpha",
        "rust/alpha",
        "python/beta",
    ]
    instance = loaded["instances"][0]
    assert instance.runner.solution_files == ("src/answer.txt",)
    assert "SECRET" not in repr(instance.runner)
    assert instance.grader_payload()["test_files"] == ["tests/hidden.txt"]
    assert loaded["benchmark"]["languages"] == {"python": 2, "rust": 1}
    assert loaded["benchmark"]["corpus_tasks"] == 4
    assert loaded["benchmark"]["corpus_languages"] == {"python": 2, "rust": 2}
    assert loaded["benchmark"]["definition_digest"].startswith("sha256:")


def test_polyglot_plan_is_shareable_and_marks_execution_not_run(tmp_path):
    loaded = PolyglotAdapter().load(
        _dataset(tmp_path), languages=("python", "rust"), limit=2
    )
    payload = polyglot_plan_payload(loaded)
    rendered = json.dumps(payload)
    assert "hidden.txt" not in rendered
    assert "SECRET" not in rendered
    assert payload["execution"] == {
        "status": "not_run",
        "requires_isolation": True,
        "runner_grader_separated": True,
    }


def test_polyglot_adapter_rejects_unsafe_or_unknown_inputs(tmp_path):
    root = tmp_path / "unsafe"
    _exercise(root, "python", "alpha", bad_solution="../escape.py")
    with pytest.raises(ValueError, match="safe and relative"):
        PolyglotAdapter().load(root, languages=("python",))
    with pytest.raises(ValueError, match="unsupported"):
        PolyglotAdapter().load(root, languages=("brainfuck",))
    good = _dataset(tmp_path / "good")
    with pytest.raises(ValueError, match="positive integer"):
        PolyglotAdapter().load(good, languages=("python",), limit=0)


def test_polyglot_adapter_rejects_symlinked_dataset_files(tmp_path):
    root = _dataset(tmp_path / "linked")
    solution = (
        root / "python" / "exercises" / "practice" / "alpha" / "src" / "answer.txt"
    )
    solution.unlink()
    solution.symlink_to(
        root / "rust" / "exercises" / "practice" / "alpha" / "src" / "answer.txt"
    )
    with pytest.raises(ValueError, match="symlinked"):
        PolyglotAdapter().load(root, languages=("python",))


def test_polyglot_plan_cli_writes_deterministic_canary(tmp_path, capsys):
    dataset = _dataset(tmp_path / "dataset")
    output = tmp_path / "plan.json"
    assert (
        main(
            [
                "polyglot-plan",
                "--dataset",
                str(dataset),
                "--output",
                str(output),
                "--languages",
                "python,rust",
                "--limit",
                "3",
            ]
        )
        == 0
    )
    printed = json.loads(capsys.readouterr().out)
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert printed == persisted
    assert [task["task_id"] for task in persisted["tasks"]] == [
        "python/alpha",
        "rust/alpha",
        "python/beta",
    ]


def test_polyglot_grader_physically_separates_runner_and_hidden_files(tmp_path):
    instance = PolyglotAdapter().load(
        _dataset(tmp_path / "dataset"), languages=("python",), limit=1
    )["instances"][0]
    runner_workspace = prepare_polyglot_runner_workspace(instance, tmp_path / "runner")
    assert (runner_workspace / "src" / "answer.txt").is_file()
    assert not (runner_workspace / "tests").exists()
    assert not (runner_workspace / ".meta").exists()

    class Container:
        is_isolated = True

        def execute(self, command, *, cwd, env, control):
            cwd = Path(cwd)
            assert cwd.name == instance.runner.exercise
            assert (cwd / "tests" / "hidden.txt").read_text() == "SECRET TEST\n"
            assert (cwd / ".meta" / "example.txt").read_text() == "SECRET ANSWER\n"
            assert (cwd / "src" / "answer.txt").read_text() == "TODO\n"
            assert tuple(command) == ("pytest",)
            assert env == {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
            return ProcessOutcome("completed", 0, "pass\n", "", 5, 0, False)

    result = PolyglotContainerGrader(Container()).grade(instance, runner_workspace)
    assert result["passed"] is True
    assert result["exit_code"] == 0


def test_polyglot_workspace_context_cannot_discover_parent_git_root(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / ".git").mkdir()
    instance = PolyglotAdapter().load(
        _dataset(tmp_path / "dataset"), languages=("python",), limit=1
    )["instances"][0]
    context = polyglot_workspace_context(instance, parent / "trial")
    assert Path(context.repo_root) == parent / "trial"
    assert Path(context.cwd) == parent / "trial"


def test_polyglot_grader_requires_isolation_and_solution_output(tmp_path):
    class Direct:
        is_isolated = False

    with pytest.raises(ValueError, match="explicitly isolated"):
        PolyglotContainerGrader(Direct())

    instance = PolyglotAdapter().load(
        _dataset(tmp_path / "dataset"), languages=("python",), limit=1
    )["instances"][0]
    runner_workspace = prepare_polyglot_runner_workspace(instance, tmp_path / "runner")
    (runner_workspace / "src" / "answer.txt").unlink()

    class Container:
        is_isolated = True

    with pytest.raises(FileNotFoundError, match="missing safely"):
        PolyglotContainerGrader(Container()).grade(instance, runner_workspace)


def test_polyglot_java_grader_enables_all_tests_only_in_grading_copy(tmp_path):
    test_path = tmp_path / "ExampleTest.java"
    original = '@Disabled("Remove to run test")\n@Test\nvoid example() {}\n'
    test_path.write_text(original, encoding="utf-8")
    instance = SimpleNamespace(
        runner=SimpleNamespace(language="java"),
        test_files=("ExampleTest.java",),
    )

    PolyglotContainerGrader._enable_all_tests(instance, tmp_path)

    assert test_path.read_text(encoding="utf-8") == "@Test\nvoid example() {}\n"


def test_polyglot_single_task_campaign_retains_patch_grade_and_turn_evidence(tmp_path):
    loaded = PolyglotAdapter().load(
        _dataset(tmp_path / "dataset"), languages=("python",), limit=1
    )
    instance = loaded["instances"][0]

    class Container:
        is_isolated = True

        def execute(self, command, *, cwd, env, control):
            assert (Path(cwd) / "src" / "answer.txt").read_text() == "DONE\n"
            return ProcessOutcome("completed", 0, "pass\n", "", 5, 0, False)

    def agent_factory(context):
        return RepoAgent(
            model_client=FakeModelClient(
                (
                    '<tool>{"name":"write_file","args":{"path":"src/answer.txt","content":"DONE\\n"}}</tool>',
                    "<final>Done.</final>",
                )
            ),
            workspace=context,
            session_store=SessionStore(
                Path(context.repo_root) / ".repoagent" / "sessions"
            ),
            approval_policy="auto",
        )

    output = tmp_path / "campaign"
    result = PolyglotSingleTaskCampaign(
        repo_root=".",
        output_root=output,
        instance=instance,
        benchmark=loaded["benchmark"],
        agent_factory=agent_factory,
        grader=PolyglotContainerGrader(Container()),
    ).run()
    row = result.rows[0]
    assert row.status == "pass"
    assert row.verifier["code_passed"] is True
    assert row.verifier["turn_converged"] is True
    assert result.model["run_kind"] == "scripted"
    assert result.aggregates["passes"] == 1
    assert result.aggregates["code_passes"] == 1
    assert result.aggregates["converged_turns"] == 1
    assert (output / "results.json").is_file()
    assert (output / "rows.jsonl").is_file()
    assert (output / "model.patch").read_text(encoding="utf-8")
    assert (output / "grade.json").is_file()
    assert (output / "agent-evidence" / "manifest.json").is_file()


def test_polyglot_single_task_campaign_can_stage_agent_workspace_separately(tmp_path):
    loaded = PolyglotAdapter().load(
        _dataset(tmp_path / "dataset"), languages=("python",), limit=1
    )
    instance = loaded["instances"][0]
    staged_workspace = tmp_path / "host-visible" / "attempt"
    observed = {}

    class Container:
        is_isolated = True

        def execute(self, command, *, cwd, env, control):
            return ProcessOutcome("completed", 0, "pass\n", "", 5, 0, False)

    def agent_factory(context):
        observed["root"] = Path(context.repo_root)
        return RepoAgent(
            model_client=FakeModelClient(("<final>Done.</final>",)),
            workspace=context,
            session_store=SessionStore(
                Path(context.repo_root) / ".repoagent" / "sessions"
            ),
            approval_policy="auto",
        )

    output = tmp_path / "campaign-with-external-workspace"
    PolyglotSingleTaskCampaign(
        repo_root=".",
        output_root=output,
        workspace_root=staged_workspace,
        instance=instance,
        benchmark=loaded["benchmark"],
        agent_factory=agent_factory,
        grader=PolyglotContainerGrader(Container()),
    ).run()

    assert observed["root"] == staged_workspace.resolve()
    assert staged_workspace.is_dir()
    assert not (output / "runner-workspace").exists()
    assert (output / "agent-evidence" / "manifest.json").is_file()


def test_polyglot_campaign_does_not_treat_code_only_pass_as_complete(tmp_path):
    dataset = _dataset(tmp_path / "dataset")
    loaded = PolyglotAdapter().load(dataset, languages=("python",), limit=1)
    instance = loaded["instances"][0]

    class Container:
        is_isolated = True

        def execute(self, command, *, cwd, env, control):
            assert (Path(cwd) / "src" / "answer.txt").read_text() == "DONE\n"
            return ProcessOutcome("completed", 0, "pass\n", "", 5, 0, False)

    def agent_factory(context):
        return RepoAgent(
            model_client=FakeModelClient(
                (
                    '<tool>{"name":"write_file","args":{"path":"src/answer.txt","content":"DONE\\n"}}</tool>',
                )
            ),
            workspace=context,
            session_store=SessionStore(
                Path(context.repo_root) / ".repoagent" / "sessions"
            ),
            approval_policy="auto",
            max_steps=1,
        )

    result = PolyglotSingleTaskCampaign(
        repo_root=".",
        output_root=tmp_path / "code-only-campaign",
        instance=instance,
        benchmark=loaded["benchmark"],
        agent_factory=agent_factory,
        grader=PolyglotContainerGrader(Container()),
    ).run()

    row = result.rows[0]
    assert row.status == "fail"
    assert row.verifier["code_passed"] is True
    assert row.verifier["turn_converged"] is False
    assert row.verifier["stop_reason"] == "step_limit_reached"
    assert result.aggregates["passes"] == 0
    assert result.aggregates["code_passes"] == 1
    assert result.gates == [
        {
            "id": "code_tests_passed",
            "status": "pass",
            "observed": "1/1",
            "threshold": "1/1",
        },
        {
            "id": "runtime_converged",
            "status": "fail",
            "observed": "0/1",
            "threshold": "1/1",
        },
    ]


def test_polyglot_campaign_preflight_failure_blocks_agent_and_grader(tmp_path):
    loaded = PolyglotAdapter().load(
        _dataset(tmp_path / "dataset"), languages=("python",), limit=1
    )

    class InvalidProbeProvider:
        profile = SimpleNamespace(
            provider="probe",
            model="probe-model",
            pricing=ModelPricing(1, 1, "test-pricing"),
        )

        def stream(self, request):
            yield ModelEvent(
                kind="completed",
                result=ModelResult(
                    tool_calls=(
                        ToolCall(
                            "probe-call",
                            "repoagent_preflight_echo",
                            {"value": "wrong"},
                        ),
                    ),
                    finish_reason="tool_use",
                    usage=ModelUsage(
                        input_tokens=5,
                        output_tokens=2,
                        total_tokens=7,
                        source=UsageSource.ACTUAL,
                    ),
                    provider="probe",
                    model="probe-model",
                ),
            )

    class Agent:
        model_client = InvalidProbeProvider()
        context_manager = SimpleNamespace(
            token_counter=SimpleNamespace(
                metadata=lambda: {
                    "identity": "test-tokenizer",
                    "source": "provider",
                }
            )
        )
        current_task_state = None

        def ask(self, _request):
            raise AssertionError("agent must not run after failed preflight")

    class Grader:
        def grade(self, _instance, _runner_root):
            raise AssertionError("grader must not run after failed preflight")

    output = tmp_path / "failed-preflight"
    result = PolyglotSingleTaskCampaign(
        repo_root=".",
        output_root=output,
        instance=loaded["instances"][0],
        benchmark=loaded["benchmark"],
        agent_factory=lambda _context: Agent(),
        grader=Grader(),
        require_provider_probe=True,
    ).run()

    row = result.rows[0]
    assert row.status == "error"
    assert row.verifier["failure_category"] == "provider_preflight_failed"
    assert row.verifier["provider_preflight_status"] == "fail"
    assert result.gates[0]["id"] == "provider_preflight"
    assert result.gates[0]["status"] == "fail"
    preflight = json.loads((output / "provider-preflight.json").read_text())
    assert preflight["status"] == "fail"
    assert not (output / "agent-evidence").exists()


def test_polyglot_formal_campaign_rejects_uncommitted_source_before_agent(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    loaded = PolyglotAdapter().load(
        _dataset(tmp_path / "dataset"), languages=("python",), limit=1
    )

    campaign = PolyglotSingleTaskCampaign(
        repo_root=source,
        output_root=tmp_path / "formal-output",
        instance=loaded["instances"][0],
        benchmark=loaded["benchmark"],
        agent_factory=lambda _context: (_ for _ in ()).throw(
            AssertionError("agent must not be constructed")
        ),
        grader=object(),
        require_provider_probe=True,
        require_clean_source=True,
    )

    with pytest.raises(ValueError, match="Git commit"):
        campaign.run()
    assert not (tmp_path / "formal-output").exists()


def test_polyglot_failed_turn_retains_call_efficiency_in_result_row(tmp_path):
    loaded = PolyglotAdapter().load(
        _dataset(tmp_path / "dataset"), languages=("python",), limit=1
    )

    class FailingProvider:
        supports_prompt_cache = False
        supports_structured_messages = True
        model = "failing-model"
        profile = ModelProfile(
            name="test",
            provider="test",
            protocol="openai",
            model=model,
            base_url="https://models.example/v1",
            credential_envs=("TEST_API_KEY",),
            temperature=0.2,
            pricing=ModelPricing(1, 1, "test-pricing"),
        )

        def stream(self, _request):
            raise ProviderError(
                "provider failed",
                category="server",
                provider="test",
                status_code=500,
            )
            yield

    def agent_factory(context):
        return RepoAgent(
            model_client=FailingProvider(),
            workspace=context,
            session_store=SessionStore(
                Path(context.repo_root) / ".repoagent" / "sessions"
            ),
            approval_policy="auto",
        )

    class Grader:
        def grade(self, _instance, _runner_root):
            raise AssertionError("grader must not run after Agent failure")

    output = tmp_path / "failed-turn"
    result = PolyglotSingleTaskCampaign(
        repo_root=tmp_path,
        output_root=output,
        instance=loaded["instances"][0],
        benchmark=loaded["benchmark"],
        agent_factory=agent_factory,
        grader=Grader(),
        require_provider_probe=False,
    ).run()

    row = result.rows[0]
    assert row.status == "error"
    assert row.metrics["call_efficiency"]["call_count"] == 1
    assert row.metrics["call_efficiency"]["cost_complete"] is False
    assert row.metrics["usage"]["model_call_count"] == 1
    assert (output / "agent-evidence" / "report.json").is_file()


def test_polyglot_campaign_revalidates_source_after_provider_probe(
    tmp_path, monkeypatch
):
    loaded = PolyglotAdapter().load(
        _dataset(tmp_path / "dataset"), languages=("python",), limit=1
    )
    source_before = {
        "commit_sha": "a" * 40,
        "branch": "main",
        "dirty": False,
        "tree_digest": "sha256:" + "b" * 64,
    }
    source_after = {**source_before, "tree_digest": "sha256:" + "c" * 64}
    states = iter((source_before, source_after))
    monkeypatch.setattr(
        "repoagent.evaluation.polyglot_campaign.collect_source_provenance",
        lambda _root: next(states),
    )

    class ValidProbeProvider:
        profile = SimpleNamespace(
            provider="probe",
            model="probe-model",
            pricing=ModelPricing(1, 1, "test-pricing"),
        )

        def stream(self, request):
            yield ModelEvent(
                kind="completed",
                result=ModelResult(
                    tool_calls=(
                        ToolCall(
                            "probe-call",
                            "repoagent_preflight_echo",
                            {"value": "ready"},
                        ),
                    ),
                    finish_reason="tool_use",
                    usage=ModelUsage(
                        input_tokens=5,
                        output_tokens=2,
                        total_tokens=7,
                        source=UsageSource.ACTUAL,
                    ),
                    provider="probe",
                    model="probe-model",
                ),
            )

    class Agent:
        model_client = ValidProbeProvider()
        context_manager = SimpleNamespace(
            total_token_budget=1000,
            token_counter=SimpleNamespace(
                metadata=lambda: {
                    "identity": "test-tokenizer",
                    "source": "provider",
                }
            ),
        )
        sandbox_adapter = SimpleNamespace(identity="docker:test", is_isolated=True)
        max_steps = 1
        max_new_tokens = 128
        current_task_state = None

        @staticmethod
        def tool_signature():
            return "tools-v1"

        def ask(self, _request):
            raise AssertionError("agent must not run after source drift")

    class Grader:
        def grade(self, _instance, _runner_root):
            raise AssertionError("grader must not run after source drift")

    output = tmp_path / "drift-output"
    result = PolyglotSingleTaskCampaign(
        repo_root=tmp_path,
        output_root=output,
        instance=loaded["instances"][0],
        benchmark=loaded["benchmark"],
        agent_factory=lambda _context: Agent(),
        grader=Grader(),
        require_provider_probe=True,
    ).run()

    assert result.rows[0].status == "error"
    preflight = json.loads((output / "provider-preflight.json").read_text())
    assert preflight["status"] == "fail"
    assert "source changed after provider preflight" in preflight["error"]


def test_paid_polyglot_campaign_rejects_concurrent_writer_before_agent(tmp_path):
    loaded = PolyglotAdapter().load(
        _dataset(tmp_path / "dataset"), languages=("python",), limit=1
    )
    campaign = PolyglotSingleTaskCampaign(
        repo_root=tmp_path,
        output_root=tmp_path / "output",
        instance=loaded["instances"][0],
        benchmark=loaded["benchmark"],
        agent_factory=lambda _context: (_ for _ in ()).throw(
            AssertionError("agent must not be constructed while lock is held")
        ),
        grader=object(),
        require_provider_probe=True,
    )

    with file_lock(campaign._campaign_lock_path(), blocking=False):
        with pytest.raises(RuntimeError, match="active paid campaign"):
            campaign.run()
    assert not (tmp_path / "output").exists()


def test_provider_probe_cache_requires_exact_record_integrity(tmp_path):
    loaded = PolyglotAdapter().load(
        _dataset(tmp_path / "dataset"), languages=("python",), limit=1
    )
    campaign = PolyglotSingleTaskCampaign(
        repo_root=tmp_path,
        output_root=tmp_path / "output",
        instance=loaded["instances"][0],
        benchmark=loaded["benchmark"],
        agent_factory=lambda _context: None,
        grader=object(),
    )
    probe = ProviderProbeResult(
        provider_name="probe",
        requested_model="model",
        resolved_model="model",
        tool_calling_supported=True,
        usage_fields=("input_tokens", "output_tokens"),
        usage_source="actual",
        tokenizer_identity="tokenizer",
        tokenizer_source="provider",
        tokenizer_digest="sha256:" + "a" * 64,
        pricing_source="snapshot",
        attempts=1,
        fallback_used=False,
        timeout_seconds=60.0,
        max_output_tokens=128,
        approval_identity={"source_commit": "b" * 40},
        approval_digest="sha256:" + "c" * 64,
    )
    campaign._write_probe_cache(probe)

    cached = campaign._read_probe_cache(probe.approval_digest)

    assert cached == probe
    cache_path = campaign._probe_cache_path(probe.approval_digest)
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["result"]["resolved_model"] = "tampered"
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProviderProbeError, match="record digest mismatch"):
        campaign._read_probe_cache(probe.approval_digest)


def test_polyglot_campaign_budget_rejects_before_agent_or_output(tmp_path):
    loaded = PolyglotAdapter().load(
        _dataset(tmp_path / "dataset"), languages=("python",), limit=2
    )
    budget = CampaignBudget(
        max_provider_calls_per_attempt=10,
        max_input_tokens_per_call=10_000,
        max_output_tokens_per_call=4_000,
        hard_cost_cap_usd=0.0001,
        pricing=ModelPricing(1, 2, "test-snapshot"),
    )
    output = tmp_path / "over-budget"
    campaign = PolyglotCampaign(
        repo_root=tmp_path,
        output_root=output,
        instances=loaded["instances"],
        benchmark=loaded["benchmark"],
        agent_factory=lambda _context: (_ for _ in ()).throw(
            AssertionError("agent must not be constructed over budget")
        ),
        grader=object(),
        repetitions=2,
        budget=budget,
        require_provider_probe=False,
        require_clean_source=False,
    )

    with pytest.raises(ValueError, match="exceeds hard cap"):
        campaign.run()
    assert not output.exists()


def test_campaign_budget_omits_probe_allowance_when_probe_is_disabled():
    budget = CampaignBudget(
        max_provider_calls_per_attempt=3,
        max_input_tokens_per_call=100,
        max_output_tokens_per_call=20,
        hard_cost_cap_usd=1.0,
        pricing=ModelPricing(1, 2, "test-snapshot"),
        provider_probe_calls=2,
    )

    estimate = budget.estimate(4, include_provider_probe=False)

    assert estimate["max_provider_calls"] == 12
    assert estimate["max_input_tokens"] == 1200
    assert estimate["max_output_tokens"] == 240


def test_polyglot_campaign_runs_full_task_repetition_matrix(tmp_path):
    loaded = PolyglotAdapter().load(
        _dataset(tmp_path / "dataset"), languages=("python",), limit=2
    )

    def agent_factory(context):
        return RepoAgent(
            model_client=FakeModelClient(
                (
                    '<tool>{"name":"write_file","args":{"path":"src/answer.txt","content":"DONE\\n"}}</tool>',
                    "<final>Done.</final>",
                )
            ),
            workspace=context,
            session_store=SessionStore(
                Path(context.repo_root) / ".repoagent" / "sessions"
            ),
            approval_policy="auto",
        )

    class Grader:
        def grade(self, instance, runner_root):
            assert (Path(runner_root) / "src" / "answer.txt").read_text() == "DONE\n"
            return {
                "task_id": instance.runner.task_id,
                "passed": True,
                "status": "completed",
                "exit_code": 0,
                "duration_seconds": 0.01,
                "stdout": "pass",
                "stderr": "",
                "output_truncated": False,
            }

    output = tmp_path / "suite-output"
    result = PolyglotCampaign(
        repo_root=tmp_path,
        output_root=output,
        instances=loaded["instances"],
        benchmark=loaded["benchmark"],
        agent_factory=agent_factory,
        grader=Grader(),
        repetitions=2,
        require_provider_probe=False,
        require_clean_source=False,
    ).run()

    assert len(result.rows) == 4
    assert {(row.task_id, row.repetition) for row in result.rows} == {
        ("python/alpha", 0),
        ("python/alpha", 1),
        ("python/beta", 0),
        ("python/beta", 1),
    }
    assert all(row.status == "pass" for row in result.rows)
    assert result.aggregates["planned_run_n"] == 4
    assert result.aggregates["executed_run_n"] == 4
    assert result.aggregates["skipped_run_n"] == 0
    assert result.gates[0]["status"] == "pass"
    assert any("not model coding quality" in item for item in result.limitations)
    assert len((output / "rows.jsonl").read_text().splitlines()) == 4
    for row in result.rows:
        assert (output / row.evidence["patch"]).is_file()


def test_polyglot_campaign_preserves_skipped_rows_after_preflight_failure(tmp_path):
    loaded = PolyglotAdapter().load(
        _dataset(tmp_path / "dataset"), languages=("python",), limit=2
    )

    class InvalidProbeProvider:
        profile = SimpleNamespace(
            provider="probe",
            model="probe-model",
            pricing=ModelPricing(1, 1, "test-pricing"),
        )

        def stream(self, _request):
            yield ModelEvent(
                kind="completed",
                result=ModelResult(
                    content="probe response without the required tool call",
                    finish_reason="stop",
                    usage=ModelUsage(
                        input_tokens=5,
                        output_tokens=2,
                        total_tokens=7,
                        source=UsageSource.ACTUAL,
                    ),
                    provider="probe",
                    model="probe-model",
                ),
            )

    class Agent:
        model_client = InvalidProbeProvider()
        context_manager = SimpleNamespace(
            total_token_budget=1000,
            token_counter=SimpleNamespace(
                metadata=lambda: {
                    "identity": "test-tokenizer",
                    "source": "provider",
                }
            ),
        )
        sandbox_adapter = SimpleNamespace(identity="docker:test", is_isolated=True)
        max_steps = 1
        max_new_tokens = 128
        current_task_state = None

        @staticmethod
        def tool_signature():
            return "tools-v1"

        def ask(self, _request):
            raise AssertionError("agent must not run after failed preflight")

    class Grader:
        def grade(self, _instance, _runner_root):
            raise AssertionError("grader must not run after failed preflight")

    output = tmp_path / "preflight-failure"
    result = PolyglotCampaign(
        repo_root=tmp_path,
        output_root=output,
        instances=loaded["instances"],
        benchmark=loaded["benchmark"],
        agent_factory=lambda _context: Agent(),
        grader=Grader(),
        repetitions=2,
        require_provider_probe=True,
        require_clean_source=False,
    ).run()

    assert [row.status for row in result.rows] == [
        "error",
        "skipped",
        "skipped",
        "skipped",
    ]
    assert result.aggregates["planned_run_n"] == 4
    assert result.aggregates["executed_run_n"] == 1
    assert result.aggregates["skipped_run_n"] == 3
    assert result.aggregates["pass_rate"] == 0.0
    assert all((output / row.evidence["skip"]).is_file() for row in result.rows[1:])
