from repoagent import MemoryConsolidator
from repoagent.security import REDACTED_VALUE


def test_consolidator_requires_explicit_intent_and_structured_durable_lines():
    consolidator = MemoryConsolidator(lambda text: text)

    no_intent = consolidator.consolidate(
        "What did we decide?", "Decision: Use typed tools."
    )
    result = consolidator.consolidate(
        "Remember this decision",
        "Decision: Use typed tools.\nUnstructured observation.",
    )

    assert no_intent.to_dict()["explicit_intent"] is False
    assert result.to_dict() == {
        "explicit_intent": True,
        "accepted_count": 1,
        "rejected_count": 0,
        "candidates": [
            {
                "topic": "key-decisions",
                "text": "Use typed tools.",
                "source": "assistant.explicit_durable_line",
                "confidence": 1.0,
            }
        ],
        "rejections": [],
    }


def test_consolidator_rejects_secrets_transient_state_and_tool_noise():
    secret = "sk-private-value"
    consolidator = MemoryConsolidator(
        lambda text: str(text).replace(secret, REDACTED_VALUE)
    )

    result = consolidator.consolidate(
        "Please remember these facts",
        "\n".join(
            [
                f"Dependency: token is {secret}",
                "Decision: next step is rerun tests",
                "Project convention: inspect stderr before retry",
            ]
        ),
    )

    assert [item.reason for item in result.rejections] == [
        "secret_shaped",
        "transient_task_state",
        "noisy_output",
    ]
    assert result.candidates == ()


def test_consolidator_never_consumes_tool_messages_by_interface():
    consolidator = MemoryConsolidator(lambda text: text)

    assert not hasattr(consolidator, "consolidate_tool_output")
