import pytest

from repoagent.issue_agent.phases import investigation_handoff, phase_instruction


def test_handoff_only_exposes_bounded_completed_investigation_report():
    case = {"runs": [{"phase": "investigate", "status": "completed",
                     "agent": {"worker": {"agent_status": "completed", "answer": "finding"}},
                     "verification": {"private_probe": "DO_NOT_EXPOSE"}}]}
    assert investigation_handoff(case, "investigate") == ""
    result = investigation_handoff(case, "fix")
    assert "finding" in result and "untrusted evidence" in result
    assert "DO_NOT_EXPOSE" not in result
    assert "scripts are not present" in result
    case["runs"][0]["agent"]["worker"]["answer"] = "x" * 13000 + "TAIL_SECRET"
    result = investigation_handoff(case, "fix")
    assert '"truncated": true' in result and "TAIL_SECRET" not in result
    case["runs"].append({"phase": "investigate", "status": "failed"})
    assert investigation_handoff(case, "fix") == ""


def test_investigation_has_a_stop_condition_without_repair_requirement():
    instruction = phase_instruction("investigate", ["main.py"])
    assert "Finish once" in instruction
    assert "failing reproduction is an expected investigation result" in instruction
    assert "monkeypatch a corrected implementation" in instruction
    assert "build dependency stubs" in instruction
    assert "state that uncertainty" in instruction


def test_repair_keeps_independent_mutation_scope():
    assert "Only change these existing source files: main.py." in phase_instruction("fix", ["main.py"])
    assert "regression checks" in phase_instruction("fix", ["main.py"])
    assert "Do not build fake pytest" in phase_instruction("fix", ["main.py"])
    assert "remaining limitations" in phase_instruction("fix", ["main.py"])
    with pytest.raises(ValueError):
        phase_instruction("other", [])
