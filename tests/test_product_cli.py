import json

import pytest

from repoagent import FakeModelClient
from repoagent.cli import build_arg_parser, main
from repoagent.runtime_assembly import RuntimeAssembly


def test_runtime_assembly_is_separate_from_argument_parser(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    args = build_arg_parser().parse_args(["--cwd", str(tmp_path), "--approval", "auto"])

    def client_factory(_args):
        client = FakeModelClient([])
        client.profile = type(
            "Profile",
            (),
            {
                "max_output_tokens": 64,
                "context_window_tokens": 4096,
                "context_window_source": "test",
            },
        )()
        return client

    assembly = RuntimeAssembly.from_arguments(
        args,
        model_client_factory=client_factory,
        secret_names_factory=lambda _args: (),
    )
    agent = assembly.build()

    assert agent.workspace.repo_root == str(tmp_path)
    assert agent.recovered_turn_ids == ()
    assert agent.checkpoint_policy == "interactive"
    assert agent.workspace_checkpoint_service is not None

    one_shot_args = build_arg_parser().parse_args(
        ["--cwd", str(tmp_path), "--approval", "auto", "inspect"]
    )
    one_shot = RuntimeAssembly.from_arguments(
        one_shot_args,
        model_client_factory=client_factory,
        secret_names_factory=lambda _args: (),
    ).build()
    assert one_shot.interactive is False
    assert one_shot.workspace_checkpoint_service is None


def test_doctor_provider_and_sandbox_commands_are_structured(tmp_path, capsys):
    assert main(["doctor", "--cwd", str(tmp_path)]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["schema"] == "repoagent.doctor/v1"
    assert {row["id"] for row in doctor["checks"]} == {
        "workspace",
        "state_root",
        "python",
        "git",
    }

    assert main(["provider", "show", "deepseek"]) == 0
    provider = json.loads(capsys.readouterr().out)["providers"][0]
    assert provider["name"] == "deepseek"
    assert "credential_configured" in provider
    assert not any("api_key" in key for key in provider)

    assert main(["sandbox", "status", "--require-isolation"]) == 0
    sandbox = json.loads(capsys.readouterr().out)
    assert sandbox["status"] == "fail"
    assert sandbox["is_isolated"] is False
    assert sandbox["available"] is True


def test_persistent_sandbox_product_selection(tmp_path, capsys, monkeypatch):
    from repoagent.sandbox_session import PersistentDockerSandboxAdapter

    probes = []
    monkeypatch.setattr(PersistentDockerSandboxAdapter, "verify_available", lambda self: probes.append(self.identity))
    args = build_arg_parser().parse_args([
        "--cwd", str(tmp_path), "--sandbox-backend", "docker-persistent"
    ])
    def client_factory(_):
        client = FakeModelClient([])
        client.profile = type("Profile", (), {
            "max_output_tokens": 64, "context_window_tokens": 4096,
            "context_window_source": "test",
        })()
        return client

    assembly = RuntimeAssembly.from_arguments(
        args, model_client_factory=client_factory,
        secret_names_factory=lambda _: (),
    )
    agent = assembly.build()
    assert isinstance(agent.sandbox_adapter, PersistentDockerSandboxAdapter)
    assert agent.sandbox_adapter.container_name is None
    assert probes
    assert main(["sandbox", "status", "--cwd", str(tmp_path), "--backend", "docker-persistent"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["identity"].startswith("docker-persistent:")
    assert report["is_isolated"] is True


def test_sandbox_reconcile_reports_uncertain_ownership(tmp_path, capsys, monkeypatch):
    from repoagent.sandbox_ownership import SandboxOwnership

    monkeypatch.setattr(SandboxOwnership, "reconcile", lambda self, adapter: [{"token": "test", "status": "watching"}])
    assert main(["sandbox", "reconcile", "--cwd", str(tmp_path)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == "repoagent.sandbox-reconcile/v1"
    assert report["status"] == "pending"


def test_mcp_check_rejects_version_only_docker_before_server_start(tmp_path, capsys, monkeypatch):
    import subprocess
    from repoagent.sandbox import DockerSandboxAdapter

    config = tmp_path / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"docs": {"command": "must-not-run"}}}))
    def run(argv, **kwargs):
        if argv[1] == "info":
            return subprocess.CompletedProcess(argv, 0, "29.7.2", "")
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(DockerSandboxAdapter, "start_process", lambda *a, **k: pytest.fail("must not start MCP"))
    assert main(["mcp", "check", "--backend", "docker", "--config", str(config), "--cwd", str(tmp_path)]) == 2
    assert "container control" in capsys.readouterr().err


def test_sandbox_status_reports_unavailable_docker_without_claiming_success(
    tmp_path, capsys, monkeypatch
):
    from repoagent import product_commands

    def unavailable(self):
        raise ValueError("daemon unavailable")

    monkeypatch.setattr(
        product_commands.DockerSandboxAdapter,
        "verify_available",
        unavailable,
    )

    assert (
        main(
            [
                "sandbox",
                "status",
                "--backend",
                "docker",
                "--cwd",
                str(tmp_path),
                "--require-isolation",
            ]
        )
        == 0
    )
    sandbox = json.loads(capsys.readouterr().out)
    assert sandbox["is_isolated"] is True
    assert sandbox["available"] is False
    assert sandbox["status"] == "fail"
    assert sandbox["error"] == "daemon unavailable"


def test_session_and_skill_commands_are_read_only_summaries(tmp_path, capsys):
    sessions = tmp_path / ".repoagent" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "session-1.json").write_text(
        json.dumps(
            {
                "id": "session-1",
                "created_at": "now",
                "workspace_root": str(tmp_path),
                "history": [{"role": "user", "content": "secret prompt"}],
                "_schema_version": 1,
                "_revision": 2,
            }
        ),
        encoding="utf-8",
    )
    skill = tmp_path / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nid: review\nname: Review\ndescription: Review changes\nversion: 1\n---\nBody secret\n",
        encoding="utf-8",
    )

    assert main(["session", "show", "session-1", "--cwd", str(tmp_path)]) == 0
    session = json.loads(capsys.readouterr().out)["sessions"][0]
    assert session["history_count"] == 1
    assert "history" not in session

    assert main(["skill", "show", "review", "--cwd", str(tmp_path)]) == 0
    skill_row = json.loads(capsys.readouterr().out)["skills"][0]
    assert skill_row["id"] == "workspace/review"
    assert "content" not in skill_row


def test_trace_and_eval_are_routed_through_unified_command_tree(tmp_path, capsys):
    assert main(["eval", "validate", str(tmp_path / "missing.json")]) == 2
    assert "missing.json" in capsys.readouterr().err

    assert main(["trace", "missing", "--root", str(tmp_path)]) == 0
    trace = capsys.readouterr().out
    assert "run: missing" in trace


def test_gateway_channel_and_cron_status_commands(tmp_path, capsys):
    assert main(["gateway", "status", "--cwd", str(tmp_path)]) == 0
    gateway = json.loads(capsys.readouterr().out)
    assert gateway["status"] == "stopped"

    channel_root = tmp_path / "channel"
    (channel_root / "inbox").mkdir(parents=True)
    (channel_root / "inbox" / "one.json").write_text("{}", encoding="utf-8")
    assert main(["channel", "directory-status", "--root", str(channel_root)]) == 0
    channel = json.loads(capsys.readouterr().out)
    assert channel["inbox_pending"] == 1

    assert main(["cron", "list", "--cwd", str(tmp_path)]) == 0
    cron = json.loads(capsys.readouterr().out)
    assert cron["jobs"] == []

    assert main(["evolver", "status", "--cwd", str(tmp_path)]) == 0
    evolver = json.loads(capsys.readouterr().out)
    assert evolver["ledger_valid"] is True
    assert evolver["event_count"] == 0
    assert all(active is None for active in evolver["routes"].values())
