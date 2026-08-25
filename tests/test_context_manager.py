from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext
from dataclasses import FrozenInstanceError

import pytest

from repoagent.context_manager import (
    CONTEXT_SEGMENT_DEFINITIONS,
    ContextBudgetExceededError,
    ContextManager,
)
from repoagent.tokenization import CallableTokenCounter


def build_workspace(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return WorkspaceContext.build(tmp_path)


def build_agent(tmp_path, outputs, **kwargs):
    workspace = build_workspace(tmp_path)
    store = SessionStore(tmp_path / ".repoagent" / "sessions")
    approval_policy = kwargs.pop("approval_policy", "auto")
    return RepoAgent(
        model_client=FakeModelClient(outputs),
        workspace=workspace,
        session_store=store,
        approval_policy=approval_policy,
        **kwargs,
    )


def test_context_manager_assembles_sections_in_expected_order(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.memory.append_note("deploy key is red", tags=("deploy",), created_at="2026-04-07T10:00:00+00:00")
    agent.record({"role": "user", "content": "old request", "created_at": "2026-04-07T09:59:00+00:00"})
    agent.record({"role": "assistant", "content": "old answer", "created_at": "2026-04-07T10:00:30+00:00"})

    prompt, metadata = ContextManager(agent).build("Where is the deploy key?")

    assert prompt.index("You are repoagent") < prompt.index("Memory:")
    assert prompt.index("Memory:") < prompt.index("Relevant memory:")
    assert prompt.index("Relevant memory:") < prompt.index("Transcript:")
    assert prompt.index("Transcript:") < prompt.index("Current user request:")
    assert prompt.rstrip().endswith("Current user request:\nWhere is the deploy key?")
    assert metadata["section_order"] == [
        "prefix",
        "checkpoint",
        "memory",
        "relevant_memory",
        "skills",
        "history",
        "current_request",
    ]


def test_context_segment_manifest_has_stable_sources_and_policy(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.render_checkpoint_text = lambda: "Task checkpoint:\n- continue safely"

    prompt, metadata = ContextManager(agent).build("Inspect the current state")

    assert prompt.index("You are repoagent") < prompt.index("Task checkpoint:")
    assert prompt.index("Task checkpoint:") < prompt.index("Memory:")
    assert metadata["segment_manifest"] == [
        {
            "name": "prefix",
            "source": "runtime.prefix",
            "order": 0,
            "reducible": True,
            "mandatory": True,
            "present": True,
        },
        {
            "name": "checkpoint",
            "source": "runtime.checkpoint",
            "order": 1,
            "reducible": False,
            "mandatory": False,
            "present": True,
        },
        {
            "name": "memory",
            "source": "memory.working",
            "order": 2,
            "reducible": True,
            "mandatory": False,
            "present": True,
        },
        {
            "name": "relevant_memory",
            "source": "memory.retrieval",
            "order": 3,
            "reducible": True,
            "mandatory": False,
            "present": True,
        },
        {
            "name": "skills",
            "source": "skill.catalog",
            "order": 4,
            "reducible": True,
            "mandatory": False,
            "present": False,
        },
        {
            "name": "history",
            "source": "session.history",
            "order": 5,
            "reducible": True,
            "mandatory": False,
            "present": True,
        },
        {
            "name": "current_request",
            "source": "request.user",
            "order": 6,
            "reducible": False,
            "mandatory": True,
            "present": True,
        },
    ]
    assert metadata["sections"]["checkpoint"]["budget_chars"] is None
    assert metadata["sections"]["current_request"]["mandatory"] is True


def test_context_segment_definitions_and_instances_are_immutable(tmp_path):
    with pytest.raises(FrozenInstanceError):
        CONTEXT_SEGMENT_DEFINITIONS[-1].order = 0

    agent = build_agent(tmp_path, [])
    rendered = ContextManager(agent)._render_sections(
        {
            "prefix": "prefix",
            "checkpoint": "",
            "memory": "memory",
            "history": "",
            "current_request": "Current user request:\nrequest",
        },
        {
            "prefix": 100,
            "memory": 100,
            "relevant_memory": 100,
            "history": 100,
        },
    )

    with pytest.raises(FrozenInstanceError):
        rendered["prefix"].rendered = "changed"
    with pytest.raises(TypeError):
        rendered["history"].details["changed"] = True


def test_context_manager_reduces_relevant_memory_before_history_and_preserves_newer_context(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.prefix = "PREFIX " + ("A" * 600)
    agent.memory.render_memory_text = lambda: "MEMORY " + ("B" * 600)
    agent.memory.append_note("keep episodic note one " + ("C" * 220), tags=("keep",), created_at="2026-04-07T10:00:00+00:00")
    agent.memory.append_note("keep episodic note two " + ("D" * 220), tags=("keep",), created_at="2026-04-07T10:01:00+00:00")
    agent.memory.append_note("keep episodic note three " + ("E" * 220), tags=("keep",), created_at="2026-04-07T10:02:00+00:00")
    agent.record({"role": "user", "content": "OLD-CONTEXT " + ("D" * 260), "created_at": "2026-04-07T09:59:00+00:00"})
    for minute in range(1, 8):
        role = "assistant" if minute % 2 == 1 else "user"
        content = "RECENT-CONTEXT " + ("E" * 260) if minute == 7 else f"recent-{minute} " + ("E" * 180)
        agent.record({"role": role, "content": content, "created_at": f"2026-04-07T10:0{minute}:00+00:00"})

    manager = ContextManager(
        agent,
        total_token_budget=175,
        segment_token_budgets={
            "prefix": 30,
            "memory": 30,
            "relevant_memory": 30,
            "history": 100,
        },
        segment_token_floors={
            "prefix": 10,
            "memory": 10,
            "relevant_memory": 10,
            "history": 30,
        },
    )

    prompt, metadata = manager.build("keep this request verbatim")

    for section in ("prefix", "memory", "relevant_memory", "history"):
        assert (
            metadata["sections"][section]["rendered_tokens"]
            <= metadata["sections"][section]["budget_tokens"]
        )

    reduction_sections = [entry["section"] for entry in metadata["budget_reductions"]]
    assert reduction_sections[0] == "relevant_memory"
    assert reduction_sections
    assert "RECENT-CONTEXT" in prompt
    assert "OLD-CONTEXT" not in prompt
    assert "keep this request verbatim" in prompt


def test_context_manager_uses_provider_token_counter_for_budgeting(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.prefix = "one two three four five six seven eight"
    agent.memory.render_memory_text = lambda: "Memory:\nnine ten eleven twelve"
    counter = CallableTokenCounter(
        lambda text: len(str(text).split()),
        counter_identity="test-provider-tokenizer",
    )

    prompt, metadata = ContextManager(
        agent,
        total_token_budget=12,
        segment_token_budgets={
            "prefix": 4,
            "memory": 2,
            "relevant_memory": 2,
            "history": 2,
        },
        segment_token_floors={
            "prefix": 1,
            "memory": 1,
            "relevant_memory": 1,
            "history": 1,
        },
        token_counter=counter,
    ).build("keep request")

    assert metadata["token_counter"] == {
        "identity": "test-provider-tokenizer",
        "source": "provider",
    }
    assert metadata["prompt_tokens"] == counter.count(prompt)
    assert metadata["sections"]["prefix"]["rendered_tokens"] <= 4
    assert metadata["sections"]["prefix"]["budget_chars"] is None
    assert prompt.endswith("Current user request:\nkeep request")


def test_context_manager_fails_closed_when_mandatory_segments_exceed_budget(
    tmp_path,
):
    agent = build_agent(tmp_path, [])
    counter = CallableTokenCounter(
        lambda text: len(str(text).split()),
        counter_identity="test-provider-tokenizer",
    )
    manager = ContextManager(
        agent,
        total_token_budget=4,
        segment_token_budgets={
            "prefix": 0,
            "memory": 0,
            "relevant_memory": 0,
            "history": 0,
        },
        segment_token_floors={
            "prefix": 0,
            "memory": 0,
            "relevant_memory": 0,
            "history": 0,
        },
        token_counter=counter,
    )

    with pytest.raises(ContextBudgetExceededError) as raised:
        manager.build("this mandatory request cannot fit")

    assert raised.value.observed_tokens > raised.value.budget_tokens
    assert raised.value.token_counter["source"] == "provider"


def test_context_manager_renders_top_three_episodic_notes_per_note_under_budget(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.memory.append_note("alpha episodic note " + ("A" * 120), tags=("recall",), created_at="2026-04-07T10:00:00+00:00")
    agent.memory.append_note("beta episodic recall note " + ("B" * 120), created_at="2026-04-07T10:01:00+00:00")
    agent.memory.append_note("gamma episodic note " + ("C" * 120), tags=("recall",), created_at="2026-04-07T10:02:00+00:00")
    agent.memory.append_note("older unmatched note", created_at="2026-04-07T09:59:00+00:00")
    agent.memory.append_note("Unrelated note", created_at="2026-04-07T11:00:00+00:00")

    prompt, metadata = ContextManager(
        agent,
        total_token_budget=250,
        segment_token_budgets={
            "prefix": 60,
            "memory": 60,
            "relevant_memory": 80,
            "history": 60,
        },
    ).build("recall")

    assert metadata["relevant_memory"]["selected_count"] == 3
    assert metadata["relevant_memory"]["limit"] == 3
    assert metadata["relevant_memory"]["selected_notes"] == [
        "gamma episodic note " + ("C" * 120),
        "alpha episodic note " + ("A" * 120),
        "beta episodic recall note " + ("B" * 120),
    ]
    assert len(metadata["relevant_memory"]["rendered_notes"]) == 3
    assert metadata["relevant_memory"]["rendered_count"] == 3
    assert metadata["relevant_memory"]["rendered_notes"][0].startswith("gamma episodi")
    assert metadata["relevant_memory"]["rendered_notes"][1].startswith("alpha episodi")
    assert metadata["relevant_memory"]["rendered_notes"][2].startswith("beta episodi")
    relevant_section = prompt.split("Relevant memory:\n", 1)[1].split("\n\nTranscript:", 1)[0]
    assert len([line for line in relevant_section.splitlines() if line.startswith("- ")]) == 3
    assert "alpha episodi" in relevant_section
    assert "beta episodic" in relevant_section
    assert "gamma episodi" in relevant_section
    assert "older unmatched note" not in relevant_section


def test_context_manager_preserves_current_request_when_over_budget(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.prefix = "PREFIX " + ("A" * 600)
    agent.memory.render_memory_text = lambda: "MEMORY " + ("B" * 600)
    agent.memory.retrieval_view = lambda query, limit=3: "Relevant memory:\n" + "\n".join(f"- {i} " + ("C" * 220) for i in range(5))
    agent.history_text = lambda: "Transcript:\n" + "\n".join(f"[user] {i} " + ("D" * 220) for i in range(5))

    request = "please preserve this request exactly"
    prompt, metadata = ContextManager(
        agent,
        total_token_budget=250,
        segment_token_budgets={
            "prefix": 80,
            "memory": 80,
            "relevant_memory": 80,
            "history": 80,
        },
    ).build(request)

    assert prompt.split("Current user request:\n", 1)[1] == request
    assert metadata["current_request"]["text"] == request
    assert metadata["current_request"]["rendered_chars"] == len(request)


def test_context_manager_collapses_older_duplicate_reads_into_one_summary_line(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\nbeta\n", encoding="utf-8")
    agent = build_agent(tmp_path, [])
    agent.memory.set_file_summary("sample.txt", "alpha | beta")
    agent.memory.remember_file("sample.txt")

    for created_at in ("2026-04-07T09:00:00+00:00", "2026-04-07T09:01:00+00:00"):
        agent.record(
            {
                "role": "tool",
                "name": "read_file",
                "args": {"path": "sample.txt", "start": 1, "end": 2},
                "content": "# sample.txt\nalpha\nbeta\n",
                "created_at": created_at,
            }
        )

    for minute in range(2, 8):
        role = "user" if minute % 2 == 0 else "assistant"
        agent.record(
            {
                "role": role,
                "content": f"recent-{minute}",
                "created_at": f"2026-04-07T09:0{minute}:00+00:00",
            }
        )

    prompt, metadata = ContextManager(agent).build("check the file")
    transcript = prompt.split("\n\nTranscript:\n", 1)[1].split("\n\nCurrent user request:", 1)[0]

    assert transcript.count("[tool:read_file]") == 0
    assert "sample.txt -> alpha | beta" in transcript
    assert metadata["history"]["older_entries_count"] == 1
    assert metadata["history"]["collapsed_duplicate_reads"] == 1
    assert metadata["history"]["reused_file_summary_count"] == 1
    assert metadata["history"]["compaction_strategy"] == "deterministic-history-v1"
    assert metadata["history"]["compaction_applied"] is True
    assert metadata["history"]["source_entry_count"] == 8
    assert len(metadata["history"]["compaction_records"]) == 8
    assert metadata["history"]["compaction_provenance_digest"].startswith("sha256:")
    assert [
        record["operation"] for record in metadata["history"]["compaction_records"][:2]
    ] == ["reuse_file_summary", "collapse_duplicate_read"]


def test_context_manager_summarizes_older_tool_output_into_one_line(tmp_path):
    agent = build_agent(tmp_path, [])
    agent.record(
        {
            "role": "tool",
            "name": "run_shell",
            "args": {"command": "pytest -q"},
            "content": "FAIL test_one\nFAIL test_two\nFAIL test_three\nFAIL test_four\n",
            "created_at": "2026-04-07T09:00:00+00:00",
        }
    )

    for minute in range(1, 7):
        role = "user" if minute % 2 == 1 else "assistant"
        agent.record(
            {
                "role": role,
                "content": f"recent-{minute}",
                "created_at": f"2026-04-07T09:0{minute}:00+00:00",
            }
        )

    prompt, metadata = ContextManager(agent).build("check failures")
    transcript = prompt.split("\n\nTranscript:\n", 1)[1].split("\n\nCurrent user request:", 1)[0]

    assert 'pytest -q -> FAIL test_one | FAIL test_two | FAIL test_three' in transcript
    assert "FAIL test_four" not in transcript
    assert metadata["history"]["summarized_tool_count"] == 1
    assert metadata["history"]["reused_file_summary_count"] == 0


def test_context_manager_relevant_memory_can_mix_durable_notes(tmp_path):
    memory_root = tmp_path / ".repoagent" / "memory"
    topics_dir = memory_root / "topics"
    topics_dir.mkdir(parents=True)
    (memory_root / "MEMORY.md").write_text(
        "# Durable Memory Index\n\n"
        "- [project-conventions](topics/project-conventions.md): Project Conventions\n"
        "  - summary: Stable repository conventions.\n"
        "  - tags: convention\n",
        encoding="utf-8",
    )
    (topics_dir / "project-conventions.md").write_text(
        "# Project Conventions\n\n"
        "- topic: project-conventions\n"
        "- summary: Stable repository conventions.\n"
        "- tags: convention\n"
        "- updated_at: 2026-04-12T08:14:49+00:00\n\n"
        "## Notes\n"
        "- Use constrained tools instead of guessing.\n",
        encoding="utf-8",
    )

    agent = build_agent(tmp_path, [])

    prompt, metadata = ContextManager(agent).build("What conventions should I follow?")
    relevant_section = prompt.split("Relevant memory:\n", 1)[1].split("\n\nTranscript:", 1)[0]

    assert "Use constrained tools instead of guessing." in relevant_section
    assert any("Use constrained tools instead of guessing." in item for item in metadata["relevant_memory"]["selected_notes"])
    assert metadata["relevant_memory"]["selected_durable_count"] == 1
    assert metadata["relevant_memory"]["selected_sources"] == ["project-conventions"]
    assert metadata["relevant_memory"]["selected_kinds"] == ["durable"]
