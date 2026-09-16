import json

import pytest

from repoagent.cli import main
from repoagent.issue_agent.cases import CaseStore


def saved_case(tmp_path, status="candidate_ready"):
    store = CaseStore(tmp_path / "cases")
    state = store.create(
        {"url": "https://github.com/example/project/issues/1", "title": "Bug\n\x1b[2J"},
        {"path": str(tmp_path / "target"), "base_revision": "a" * 40},
    )
    state["status"] = status
    state["runs"] = [
        {
            "phase": "fix",
            "status": "completed",
            "agent": {
                "worker": {"agent_status": "completed"},
                "changes": {"main.py": "PRIVATE_SOURCE"},
                "model": {"calls_reserved": 11},
            },
            "verification": {
                "baseline": {"reproduced": True, "passed": False, "exit_code": 1},
                "candidate": {"reproduced": False, "passed": True, "exit_code": 0},
            },
        }
    ]
    store.save(state)
    return store, state


def test_default_and_explicit_json_remain_machine_readable(tmp_path, capsys):
    store, state = saved_case(tmp_path)
    for flags in ([], ["--format", "json"]):
        assert (
            main(
                ["issue", "show", state["case_id"], "--store", str(store.root), *flags]
            )
            == 0
        )
        out = capsys.readouterr()
        assert json.loads(out.out) == state
        assert not out.err


def test_text_show_summarizes_evidence_without_source_or_terminal_controls(
    tmp_path, capsys
):
    store, state = saved_case(tmp_path)
    assert (
        main(
            [
                "issue",
                "show",
                state["case_id"],
                "--store",
                str(store.root),
                "--format",
                "text",
            ]
        )
        == 0
    )
    out = capsys.readouterr()
    assert "Status: candidate_ready" in out.out
    assert "Baseline: reproduced (exit 1)" in out.out
    assert "Candidate: passed (exit 0)" in out.out
    assert "Model calls: 11" in out.out
    assert "main.py" in out.out and "PRIVATE_SOURCE" not in out.out
    assert "\x1b" not in out.out and not out.err
    assert "Report:" not in out.out  # Never advertise a nonexistent report.


def test_incomplete_worker_is_visible_even_in_legacy_reproduced_case(tmp_path, capsys):
    store, state = saved_case(tmp_path, "reproduced")
    state["runs"][0]["agent"]["worker"]["agent_status"] = "stopped"
    store.save(state)
    main(
        [
            "issue",
            "show",
            state["case_id"],
            "--store",
            str(store.root),
            "--format",
            "text",
        ]
    )
    assert "Agent: stopped" in capsys.readouterr().out


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_progress_is_only_on_stderr_in_text_mode(
    tmp_path, capsys, monkeypatch, output_format
):
    import repoagent.issue_agent.workflow

    store, state = saved_case(tmp_path)

    def execute(store, case_id, phase, *, config, on_progress):
        if output_format == "text":
            on_progress("baseline")
            on_progress("agent")
            on_progress("candidate")
            on_progress("finished:candidate_ready")
        else:
            assert on_progress is None
        return state

    monkeypatch.setattr(repoagent.issue_agent.workflow, "execute_case", execute)
    assert (
        main(
            [
                "issue",
                "fix",
                state["case_id"],
                "--store",
                str(store.root),
                "--format",
                output_format,
            ]
        )
        == 0
    )
    out = capsys.readouterr()
    if output_format == "text":
        assert "[issue] Running the baseline" in out.err
        assert "[issue] Finished: candidate_ready" in out.err
        assert "[issue]" not in out.out
    else:
        assert json.loads(out.out) == state and not out.err
