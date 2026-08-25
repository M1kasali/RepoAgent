import pytest

from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext
from repoagent.context_window import (
    ContextWindowBudget,
    ContextWindowConfigurationError,
    ContextWindowExceededError,
)
from repoagent.tokenization import CallableTokenCounter


def test_context_window_budget_reserves_output_and_admits_exact_boundary():
    budget = ContextWindowBudget(
        context_window_tokens=100,
        configured_input_tokens=80,
        reserved_output_tokens=30,
    )

    assert budget.available_input_tokens == 70
    assert budget.effective_input_tokens == 70
    admission = budget.admit(70)
    assert admission.total_reserved_tokens == 100
    assert admission.headroom_tokens == 0
    assert admission.admitted is True
    assert admission.window_source == "runtime-default"


def test_context_window_budget_rejects_invalid_reservation_and_overflow():
    with pytest.raises(ContextWindowConfigurationError, match="smaller"):
        ContextWindowBudget(
            context_window_tokens=100,
            configured_input_tokens=50,
            reserved_output_tokens=100,
        )

    budget = ContextWindowBudget(
        context_window_tokens=100,
        configured_input_tokens=80,
        reserved_output_tokens=30,
    )
    with pytest.raises(ContextWindowExceededError) as raised:
        budget.admit(71)

    assert raised.value.admission.total_reserved_tokens == 101
    assert raised.value.admission.admitted is False


def test_runtime_records_output_reservation_and_effective_input_budget(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    client = FakeModelClient([])
    agent = RepoAgent(
        model_client=client,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
        context_window_tokens=5000,
        context_token_budget=3000,
        max_new_tokens=4096,
    )

    metadata = agent.prompt_metadata("inspect", "")

    assert metadata["configured_input_token_budget"] == 3000
    assert metadata["effective_input_token_budget"] == 904
    assert metadata["prompt_token_budget"] == 904
    assert metadata["reserved_output_tokens"] == 4096
    assert metadata["request_admission_tokens"] <= 5000
    assert metadata["context_window_headroom_tokens"] >= 0
    assert metadata["context_window_admitted"] is True
    assert metadata["context_window_source"] == "runtime-argument"
    assert metadata["context_window"]["admitted"] is True
    assert metadata["context_window"]["window_source"] == "runtime-argument"

    configured = agent.configure_context_budget(700)
    refreshed = agent.prompt_metadata("inspect again", "")
    assert configured["configured_input_tokens"] == 700
    assert refreshed["configured_input_token_budget"] == 700
    assert refreshed["effective_input_token_budget"] == 700
    assert refreshed["prompt_token_budget"] == 700


def test_runtime_does_not_call_provider_when_mandatory_input_cannot_fit(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    client = FakeModelClient(["<final>must not run</final>"])
    client.token_counter = CallableTokenCounter(
        lambda text: len(str(text).split()),
        counter_identity="test-provider-tokenizer",
    )
    agent = RepoAgent(
        model_client=client,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy="auto",
        context_window_tokens=10,
        context_token_budget=8,
        max_new_tokens=8,
    )

    with pytest.raises(RuntimeError, match="ContextBudgetExceededError"):
        agent.ask("mandatory request cannot fit")

    assert client.prompts == []
