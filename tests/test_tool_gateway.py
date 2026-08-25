import json
from pathlib import Path
import sys
import threading
import time

import pytest

from repoagent import (
    FakeModelClient,
    CancellationToken,
    RepoAgent,
    SessionStore,
    ToolEffect,
    ToolGateway,
    ToolRequest,
    ToolResult,
    ToolRunnerOutput,
    WorkspaceContext,
)
from repoagent.tool_gateway import compatibility_metadata


def build_agent(tmp_path, outputs=(), **kwargs):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    return RepoAgent(
        model_client=FakeModelClient(outputs),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".repoagent" / "sessions"),
        approval_policy=kwargs.pop("approval_policy", "auto"),
        **kwargs,
    )


def test_gateway_requires_typed_request_and_returns_typed_result(tmp_path):
    agent = build_agent(tmp_path)
    request = ToolRequest(
        call_id="call-read-1",
        name="read_file",
        arguments={"path": "README.md"},
        session_id=agent.session["id"],
        origin="internal",
        capability_token=agent.capability_token,
    )

    with pytest.raises(TypeError, match="ToolRequest"):
        agent.tool_gateway.execute({"name": "read_file"})

    result = agent.tool_gateway.execute(request)

    assert isinstance(agent.tool_gateway, ToolGateway)
    assert isinstance(result, ToolResult)
    assert result.call_id == request.call_id
    assert result.name == "read_file"
    assert result.status == "ok"
    assert result.effect is ToolEffect.READ
    assert result.duration_ms >= 0
    assert compatibility_metadata(result)["read_only"] is True


def test_gateway_rejects_unknown_tools_and_invalid_arguments(tmp_path):
    agent = build_agent(tmp_path)

    unknown = agent.execute_tool("vendor/tool-v2", {}, call_id="call-unknown")
    invalid = agent.execute_tool(
        "read_file",
        {"path": "README.md", "start": "one"},
        call_id="call-invalid",
    )

    assert unknown.status == "rejected"
    assert unknown.effect is ToolEffect.UNKNOWN
    assert unknown.error_code == "unknown_tool"
    assert invalid.status == "rejected"
    assert invalid.effect is ToolEffect.READ
    assert invalid.error_code == "invalid_arguments"


def test_gateway_fails_closed_for_missing_tampered_and_cross_session_tokens(
    tmp_path,
):
    agent = build_agent(tmp_path)
    valid = agent.build_tool_request(
        "read_file", {"path": "README.md"}, call_id="call-valid"
    )
    body, signature = agent.capability_token.split(".", 1)
    replacement = "A" if signature[-1] != "A" else "B"
    tampered_token = f"{body}.{signature[:-1]}{replacement}"
    requests = (
        ToolRequest(
            call_id="call-missing",
            name="read_file",
            arguments={"path": "README.md"},
            session_id=agent.session["id"],
        ),
        ToolRequest(
            call_id="call-tampered",
            name="read_file",
            arguments={"path": "README.md"},
            session_id=agent.session["id"],
            capability_token=tampered_token,
        ),
        ToolRequest(
            call_id="call-replayed",
            name="read_file",
            arguments={"path": "README.md"},
            session_id="another-session",
            capability_token=agent.capability_token,
        ),
    )

    results = [agent.tool_gateway.execute(request) for request in requests]

    assert [result.error_code for result in results] == [
        "capability_denied",
        "capability_denied",
        "capability_denied",
    ]
    assert [result.metadata["capability"]["reason"] for result in results] == [
        "missing_token",
        "invalid_or_expired_token",
        "request_session_mismatch",
    ]
    assert agent.tool_gateway.execute(valid).status == "ok"


def test_gateway_accounts_for_workspace_changes(tmp_path):
    agent = build_agent(tmp_path)

    result = agent.execute_tool(
        "write_file",
        {"path": "notes.txt", "content": "gateway\n"},
        call_id="call-write-1",
    )

    assert result.status == "ok"
    assert result.effect is ToolEffect.WRITE
    assert result.workspace_changed is True
    assert result.affected_paths == ("notes.txt",)
    assert result.metadata["diff_summary"] == ("created:notes.txt",)
    assert result.metadata["capability"]["allowed"] is True
    assert result.metadata["approval"]["reason"] == "policy_auto"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "gateway\n"


def test_gateway_maps_timeout_and_output_limit_to_structured_results(tmp_path):
    agent = build_agent(tmp_path)
    timeout = agent.execute_tool(
        "run_shell",
        {
            "command": f'{sys.executable} -c "import time; time.sleep(2)"',
            "timeout": 2,
        },
        call_id="call-timeout",
        timeout_seconds=0.1,
    )
    truncated = agent.execute_tool(
        "run_shell",
        {
            "command": f"{sys.executable} -c \"print('x' * 5000)\"",
            "timeout": 2,
        },
        call_id="call-output",
        max_output_chars=180,
    )

    assert timeout.status == "timeout"
    assert timeout.error_code == "tool_timeout"
    assert timeout.metadata["execution_status"] == "timeout"
    assert timeout.metadata["termination_signal"] in {
        "sigterm",
        "sigkill",
        "taskkill",
        "taskkill_force",
    }
    assert truncated.status == "partial_success"
    assert truncated.error_code == "tool_output_truncated"
    assert truncated.metadata["output_truncated"] is True
    assert truncated.metadata["output_limit_chars"] == 180
    assert len(truncated.content) <= 180


def test_gateway_cancellation_converges_to_a_structured_result(tmp_path):
    agent = build_agent(tmp_path)
    token = CancellationToken()
    request = agent.build_tool_request(
        "run_shell",
        {
            "command": f'{sys.executable} -c "import time; time.sleep(5)"',
            "timeout": 10,
        },
        call_id="call-cancel",
    )
    values = []
    worker = threading.Thread(
        target=lambda: values.append(
            agent.execute_tool_request(request, cancellation_token=token)
        )
    )
    worker.start()
    time.sleep(0.1)
    token.cancel()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert values[0].status == "cancelled"
    assert values[0].error_code == "tool_cancelled"
    assert values[0].metadata["execution_status"] == "cancelled"


def test_gateway_parallel_read_batches_obey_limit_and_keep_result_order(tmp_path):
    for index in range(8):
        (tmp_path / f"file-{index}.txt").write_text(
            f"value-{index}\n", encoding="utf-8"
        )
    agent = build_agent(tmp_path, max_parallel_tools=3)
    lock = threading.Lock()
    state = {"active": 0, "peak": 0, "completed": []}

    def delayed_read(args, control):
        index = int(Path(args["path"]).stem.split("-")[-1])
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        try:
            time.sleep((8 - index) * 0.005)
            with lock:
                state["completed"].append(index)
            return args["path"]
        finally:
            with lock:
                state["active"] -= 1

    agent.tools["read_file"]["run"] = delayed_read
    requests = tuple(
        agent.build_tool_request(
            "read_file",
            {"path": f"file-{index}.txt"},
            call_id=f"read-{index}",
        )
        for index in range(8)
    )
    batches = agent.tool_gateway.plan_batches(requests)
    results = tuple(
        result
        for batch in batches
        for result in agent.execute_tool_batch(batch)
    )

    assert [len(batch) for batch in batches] == [3, 3, 2]
    assert state["peak"] == 3
    assert state["completed"] != list(range(8))
    assert [result.call_id for result in results] == [
        f"read-{index}" for index in range(8)
    ]
    assert [result.content for result in results] == [
        f"file-{index}.txt" for index in range(8)
    ]


def test_gateway_treats_non_safe_calls_as_serial_batch_barriers(tmp_path):
    agent = build_agent(tmp_path, max_parallel_tools=4)
    requests = (
        agent.build_tool_request(
            "list_files", {"path": "."}, call_id="read-1"
        ),
        agent.build_tool_request(
            "read_file", {"path": "README.md"}, call_id="read-2"
        ),
        agent.build_tool_request(
            "write_file",
            {"path": "written.txt", "content": "written\n"},
            call_id="write-1",
        ),
        agent.build_tool_request(
            "search", {"pattern": "demo"}, call_id="read-3"
        ),
        agent.build_tool_request(
            "delegate", {"task": "inspect"}, call_id="delegate-1"
        ),
    )

    batches = agent.tool_gateway.plan_batches(requests)

    assert [[request.call_id for request in batch] for batch in batches] == [
        ["read-1", "read-2"],
        ["write-1"],
        ["read-3"],
        ["delegate-1"],
    ]
    assert agent.tool_gateway.is_concurrency_safe(requests[0]) is True
    assert agent.tool_gateway.is_concurrency_safe(requests[2]) is False
    assert agent.tool_gateway.is_concurrency_safe(requests[4]) is False
    assert [batch.reason for batch in batches] == [
        "concurrency_safe_read",
        "mutation_conflict_policy",
        "concurrency_safe_read_singleton",
        "read_not_concurrency_safe",
    ]
    assert {
        batch.mutation_conflict_policy.value for batch in batches
    } == {"serial"}


def test_gateway_rejects_oversized_parallel_batch(tmp_path):
    agent = build_agent(tmp_path, max_parallel_tools=2)
    requests = tuple(
        agent.build_tool_request(
            "read_file", {"path": "README.md"}, call_id=f"read-{index}"
        )
        for index in range(3)
    )

    with pytest.raises(ValueError, match="max_parallel"):
        agent.execute_tool_batch(requests)


def test_parallel_read_batch_shares_cancellation_and_converges_in_order(tmp_path):
    for index in range(4):
        (tmp_path / f"file-{index}.txt").write_text("value\n", encoding="utf-8")
    agent = build_agent(tmp_path, max_parallel_tools=4)
    token = CancellationToken()
    lock = threading.Lock()
    active = 0
    peak = 0

    def cancellable_read(args, control):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            while control.status() == "running":
                time.sleep(0.005)
            return ToolRunnerOutput(
                args["path"], {"execution_status": control.status()}
            )
        finally:
            with lock:
                active -= 1

    agent.tools["read_file"]["run"] = cancellable_read
    requests = tuple(
        agent.build_tool_request(
            "read_file",
            {"path": f"file-{index}.txt"},
            call_id=f"read-{index}",
        )
        for index in range(4)
    )
    values = []
    worker = threading.Thread(
        target=lambda: values.extend(
            agent.execute_tool_batch(requests, cancellation_token=token)
        )
    )
    worker.start()
    deadline = time.monotonic() + 1
    while peak < 4 and time.monotonic() < deadline:
        time.sleep(0.005)
    token.cancel()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert peak == 4
    assert active == 0
    assert [result.call_id for result in values] == [
        f"read-{index}" for index in range(4)
    ]
    assert {result.status for result in values} == {"cancelled"}
    assert {result.error_code for result in values} == {"tool_cancelled"}


def test_conflicting_mutations_are_serial_and_observe_prior_workspace_state(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("original\n", encoding="utf-8")
    agent = build_agent(tmp_path, mutation_conflict_policy="serial")
    requests = (
        agent.build_tool_request(
            "patch_file",
            {
                "path": "target.txt",
                "old_text": "original\n",
                "new_text": "first\n",
            },
            call_id="patch-1",
        ),
        agent.build_tool_request(
            "patch_file",
            {
                "path": "target.txt",
                "old_text": "original\n",
                "new_text": "second\n",
            },
            call_id="patch-2",
        ),
    )

    batches = agent.tool_gateway.plan_batches(requests)
    results = tuple(
        result for batch in batches for result in agent.execute_tool_batch(batch)
    )

    assert [len(batch) for batch in batches] == [1, 1]
    assert [batch.reason for batch in batches] == [
        "mutation_conflict_policy",
        "mutation_conflict_policy",
    ]
    assert results[0].status == "ok"
    assert results[1].status == "rejected"
    assert results[1].error_code == "invalid_arguments"
    assert target.read_text(encoding="utf-8") == "first\n"


def test_gateway_rejects_unimplemented_mutation_policy(tmp_path):
    with pytest.raises(ValueError, match="unsupported mutation conflict policy"):
        build_agent(tmp_path, mutation_conflict_policy="disjoint_paths")


def test_delegate_tool_uses_the_same_gateway_and_normalizes_defaults(tmp_path):
    agent = build_agent(tmp_path)
    received = []
    agent.tools["delegate"]["run"] = lambda args, control: (
        received.append(args) or "delegated"
    )
    request = agent.build_tool_request(
        "delegate",
        {"task": "inspect the repository"},
        call_id="call-delegate-1",
        origin="delegate",
        parent_call_id="call-parent-1",
    )

    result = agent.execute_tool_request(request)

    assert result.status == "ok"
    assert result.content == "delegated"
    assert received == [
        {
            "task": "inspect the repository",
            "max_steps": 3,
            "role": "reviewer",
        }
    ]
    assert request.parent_call_id == "call-parent-1"


def test_agent_loop_persists_gateway_request_and_result_correlation(tmp_path):
    agent = build_agent(
        tmp_path,
        (
            '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
            "<final>Done.</final>",
        ),
    )

    assert agent.ask("Inspect README") == "Done."

    events = [
        json.loads(line)
        for line in agent.run_store.trace_path(agent.current_task_state)
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    event = next(item for item in events if item["event"] == "tool_executed")
    request = event["tool_request"]
    result = event["tool_result"]

    assert request["origin"] == "model"
    assert request["turn_id"] == agent.current_task_state.run_id
    assert request["request_id"] == agent.current_task_state.task_id
    assert request["session_id"] == agent.session["id"]
    assert request["call_id"]
    assert result["call_id"] == request["call_id"] == event["tool_call_id"]
    assert result["status"] == event["tool_status"] == "ok"
    assert event["capability"]["allowed"] is True
    assert event["approval"]["reason"] == "safe_read"
    assert agent.capability_token not in json.dumps(event, sort_keys=True)


def test_gateway_is_the_only_registry_runner_invocation():
    package_root = Path(__file__).parents[1] / "repoagent"
    invocation = 'entry["run"]('
    owners = [
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*.py")
        if invocation in path.read_text(encoding="utf-8")
    ]

    assert owners == ["tool_gateway.py"]
