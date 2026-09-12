import json
import shlex
from types import SimpleNamespace

import pytest

from repoagent.task_state import TaskState
from repoagent.test_verification import (
    parse_report,
    refreshed_verifications,
    workspace_digest,
)
from test_tool_gateway import build_agent


PASSING = "import unittest\nfrom value import VALUE\nclass Tests(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(VALUE, 1)\n"


def fixture(tmp_path, source=PASSING):
    agent = build_agent(tmp_path, checkpoint_policy="never")
    (tmp_path / "value.py").write_text("VALUE = 1\n")
    (tmp_path / "test_value.py").write_text(source)
    return agent


@pytest.mark.parametrize(
    "source,verdict,count",
    [
        (PASSING, "passed", 1),
        (PASSING.replace("VALUE, 1", "VALUE, 2"), "failed", 1),
        ("", "no_tests_passed", 0),
        (
            PASSING.replace(
                "    def test_value", "    @unittest.skip('skip')\n    def test_value"
            ),
            "no_tests_passed",
            1,
        ),
        ("raise RuntimeError('broken import')", "failed", 1),
    ],
)
def test_real_framework_results_not_shell_output(tmp_path, source, verdict, count):
    agent = fixture(tmp_path, source)
    result = agent.execute_tool("run_tests", {})
    evidence = result.metadata["test_verification"]
    assert evidence["verdict"] == verdict
    assert evidence["report"]["tests"] == count
    assert evidence["freshness"] == "current"
    assert evidence["report"]["test_ids"] or count == 0


def test_ordinary_stdout_cannot_be_used_as_report(tmp_path):
    agent = fixture(tmp_path, "print('all tests passed')\n")
    result = agent.execute_tool("run_tests", {})
    assert result.metadata["test_verification"]["verdict"] == "no_tests_passed"
    assert (
        "test_verification"
        not in agent.execute_tool(
            "run_shell", {"command": "echo all tests passed"}
        ).metadata
    )


def test_changes_during_tests_invalidate_result(tmp_path):
    agent = fixture(
        tmp_path,
        PASSING.replace(
            "self.assertEqual(VALUE, 1)", "open('value.py', 'w').write('VALUE = 2\\n')"
        ),
    )
    result = agent.execute_tool("run_tests", {})
    assert result.metadata["test_verification"]["verdict"] == "passed"
    assert result.metadata["test_verification"]["freshness"] == "stale"


def test_content_freshness_is_sticky_and_covers_new_deleted_files(tmp_path):
    fixture(tmp_path)
    initial = workspace_digest(tmp_path)
    rows = [{"source_digest": initial, "freshness": "current"}]
    (tmp_path / "value.py").write_text("VALUE = 2\n")
    stale = refreshed_verifications(rows, tmp_path)
    assert stale[0]["freshness"] == "stale"
    assert rows[0]["freshness"] == "current"
    (tmp_path / "value.py").write_text("VALUE = 1\n")
    assert refreshed_verifications(stale, tmp_path)[0]["freshness"] == "stale"
    (tmp_path / "new.py").write_text("new")
    assert workspace_digest(tmp_path) != initial
    (tmp_path / "test_value.py").unlink()
    assert workspace_digest(tmp_path) != initial


def test_unknown_inventory_never_claims_fresh(tmp_path):
    (tmp_path / "link").symlink_to(tmp_path / "absent")
    assert workspace_digest(tmp_path) == ""
    assert (
        refreshed_verifications(
            [{"freshness": "current", "source_digest": "digest"}], tmp_path
        )[0]["freshness"]
        == "unknown"
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": "timeout"},
        {"status": "cancelled"},
        {"stdout": "OK"},
        {"stdout": "{}"},
        {"output_truncated": True},
        {"exit_code": True},
    ],
)
def test_missing_invalid_or_incomplete_reports_fail_closed(kwargs):
    outcome = SimpleNamespace(
        status="completed", exit_code=0, stdout="{}", output_truncated=False
    )
    for key, value in kwargs.items():
        setattr(outcome, key, value)
    assert parse_report(outcome) is None


def test_gateway_denial_and_path_escape_do_not_create_evidence(tmp_path):
    agent = fixture(tmp_path)
    # Explicit tool allowlist rejection is independent of approval UI settings.
    agent.allowed_tools = {"read_file"}
    assert "test_verification" not in agent.execute_tool("run_tests", {}).metadata
    agent.allowed_tools = None
    for args in ({"start": "../"}, {"pattern": "../test*.py"}):
        assert agent.execute_tool("run_tests", args).status == "rejected"


def test_runtime_pass_edit_revalidate_and_checkpoint(tmp_path):
    agent = fixture(tmp_path)

    def call(name, args):
        return "<tool>" + json.dumps({"name": name, "args": args}) + "</tool>"

    from repoagent import FakeModelClient

    agent.model_client = FakeModelClient(
        [
            call("run_tests", {}),
            call("write_file", {"path": "value.py", "content": "VALUE = 2\n"}),
            call("run_tests", {}),
            call("write_file", {"path": "value.py", "content": "VALUE = 1\n"}),
            call("run_tests", {}),
            "<final>Validated.</final>",
        ]
    )
    agent.ask("Validate, edit and revalidate")
    rows = agent.current_task_state.test_verifications
    assert [(r["verdict"], r["freshness"]) for r in rows] == [
        ("passed", "stale"),
        ("failed", "stale"),
        ("passed", "current"),
    ]
    assert '"freshness": "stale"' in agent.model_client.prompts[2]
    assert agent.current_checkpoint()["test_verifications"] == rows
    report = json.loads(
        agent.run_store.report_path(agent.current_task_state).read_text()
    )
    assert report["task_state"]["test_verifications"] == rows
    payload = agent.current_task_state.to_dict()
    assert TaskState.from_dict(payload).test_verifications == rows
    payload.pop("test_verifications")
    assert TaskState.from_dict(payload).test_verifications == []


def test_later_external_edit_is_stale_in_rendered_checkpoint(tmp_path):
    from repoagent import FakeModelClient

    agent = fixture(tmp_path)
    agent.model_client = FakeModelClient(
        ['<tool>{"name":"run_tests","args":{}}</tool>', "<final>Done.</final>"]
    )
    agent.ask("Validate")
    assert '"freshness": "current"' in agent.render_checkpoint_text()
    (tmp_path / "value.py").write_text("VALUE = 2\n")
    assert '"freshness": "stale"' in agent.render_checkpoint_text()


def test_real_timeout_does_not_become_pass(tmp_path):
    agent = fixture(tmp_path, "import time\ntime.sleep(5)\n" + PASSING)
    result = agent.execute_tool("run_tests", {}, timeout_seconds=0.1)
    assert result.status == "timeout"
    assert result.metadata["test_verification"]["verdict"] == "unknown"


def test_selected_sandbox_receives_quoted_framework_command(tmp_path):
    from repoagent.tool_execution import ProcessOutcome

    agent = fixture(tmp_path)
    commands = []
    # Exercise the registered runner's narrow context rather than replacing Gateway.
    context = agent.tools["run_tests"]["run"].args[0]
    adapter = context.sandbox_adapter

    class Sandbox:
        identity = "test-isolated"

        def execute(self, command, **kwargs):
            commands.append(command)
            return ProcessOutcome("completed", 0, "{}", "", 2, 0, False)

    context.sandbox_adapter = Sandbox()
    try:
        result = agent.execute_tool("run_tests", {"pattern": "test;echo injected.py"})
    finally:
        context.sandbox_adapter = adapter
    argv = shlex.split(commands[0])
    assert argv[:3] == ["python3", "-B", "-c"]
    assert argv[-1] == "test;echo injected.py"
    assert result.metadata["test_verification"]["sandbox_identity"] == "test-isolated"
    assert result.metadata["test_verification"]["verdict"] == "unknown"


def test_state_bounds_records_and_copies_nested_data():
    payload = TaskState.create("id", "request").to_dict()
    payload["test_verifications"] = [{"report": {"tests": index}} for index in range(8)]
    state = TaskState.from_dict(payload)
    assert len(state.test_verifications) == 4
    payload["test_verifications"][-1]["report"]["tests"] = -1
    assert state.to_dict()["test_verifications"][-1]["report"]["tests"] == 7


@pytest.mark.parametrize(
    "change",
    [
        {"tests": True},
        {"test_ids": []},
        {"test_ids": [""]},
        {"skipped": 2},
        {"errors": -1},
    ],
)
def test_report_rejects_inconsistent_counts_or_ids(change):
    report = {
        "schema": "repoagent.unittest/v1",
        "tests": 1,
        "test_ids": ["test.case"],
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "expected_failures": 0,
        "unexpected_successes": 0,
    }
    outcome = SimpleNamespace(
        status="completed",
        exit_code=0,
        output_truncated=False,
        stdout=json.dumps(report),
    )
    assert parse_report(outcome) is not None
    outcome.stdout = json.dumps(report | change)
    assert parse_report(outcome) is None
