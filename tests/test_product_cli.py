import json

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
