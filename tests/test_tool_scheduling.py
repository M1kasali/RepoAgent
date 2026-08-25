import pytest

from repoagent import (
    MutationConflictPolicy,
    ToolBatch,
    ToolBatchMode,
    ToolEffect,
    ToolRequest,
)


def request(call_id):
    return ToolRequest(
        call_id=call_id,
        name="read_file",
        arguments={"path": "README.md"},
    )


def test_parallel_batch_contract_is_immutable_and_serializable():
    batch = ToolBatch(
        requests=(request("call-1"), request("call-2")),
        mode=ToolBatchMode.PARALLEL,
        reason="concurrency_safe_read",
        effects=(ToolEffect.READ, ToolEffect.READ),
        mutation_conflict_policy=MutationConflictPolicy.SERIAL,
    )

    assert len(batch) == 2
    assert [item.call_id for item in batch] == ["call-1", "call-2"]
    assert batch.to_dict() == {
        "mode": "parallel",
        "reason": "concurrency_safe_read",
        "effects": ["read", "read"],
        "mutation_conflict_policy": "serial",
        "tool_call_ids": ["call-1", "call-2"],
    }


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"requests": ()}, "at least one"),
        (
            {
                "requests": (request("call-1"),),
                "mode": ToolBatchMode.PARALLEL,
                "effects": (ToolEffect.READ,),
            },
            "at least two",
        ),
        (
            {
                "effects": (ToolEffect.WRITE, ToolEffect.READ),
            },
            "only read",
        ),
        (
            {
                "mode": ToolBatchMode.SERIAL,
                "effects": (ToolEffect.WRITE, ToolEffect.WRITE),
            },
            "singleton",
        ),
    ],
)
def test_batch_contract_rejects_unsafe_shapes(kwargs, message):
    values = {
        "requests": (request("call-1"), request("call-2")),
        "mode": ToolBatchMode.PARALLEL,
        "reason": "test",
        "effects": (ToolEffect.READ, ToolEffect.READ),
        "mutation_conflict_policy": MutationConflictPolicy.SERIAL,
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        ToolBatch(**values)
