import asyncio
import json
from pathlib import Path
import subprocess
import sys

from repoagent.channel_receipts import ChannelReceipts, DurableDirectoryDelivery
from repoagent.channels import ChannelMessage, DirectoryChannel
from repoagent.runtime_host import RuntimeHost
from test_product_transports import _agent


def message(text="work", **kwargs):
    return ChannelMessage(
        "directory", "room", "local", text, kwargs.pop("message_id", "one"), **kwargs
    )


def setup(tmp_path, outputs=()):
    host = RuntimeHost(_agent(tmp_path / "workspace", list(outputs)))
    channel = DirectoryChannel(tmp_path / "queue")
    receipts = ChannelReceipts(tmp_path / "receipts.sqlite3")
    worker = DurableDirectoryDelivery(
        host, channel, receipts, retry_base=0, max_attempts=2
    )
    return host, channel, receipts, worker


def test_reserved_but_not_submitted_receipt_is_safe_to_submit(tmp_path):
    async def scenario():
        host, channel, receipts, worker = setup(tmp_path, ["<final>reply</final>"])
        row, _, _ = receipts.reserve(message(), worker.scope)
        await worker.reconcile(startup=True)
        try:
            accepted = await worker.submit(message())
            assert accepted["turn_id"] == row["turn_id"]
            await host.wait(accepted["turn_id"])
            await worker.reconcile()
            assert receipts.summary()["counts"] == {"delivered": 1}
        finally:
            await host.stop()

    asyncio.run(scenario())


def test_restart_recovers_completed_reply_without_model_execution(tmp_path):
    async def first():
        host, channel, receipts, worker = setup(tmp_path, ["<final>reply</final>"])
        try:
            accepted = await worker.submit(message())
            await host.wait(accepted["turn_id"])
        finally:
            await host.stop()
        assert receipts.summary()["counts"] == {"running": 1}
        return accepted["turn_id"]

    turn_id = asyncio.run(first())

    async def second():
        host, channel, receipts, worker = setup(tmp_path)
        await worker.reconcile(startup=True)
        duplicate = await worker.submit(message())
        assert duplicate == {"accepted": True, "duplicate": True, "turn_id": turn_id}
        assert not host.agent.model_client.prompts
        assert receipts.summary()["counts"] == {"delivered": 1}
        reply = json.loads(next(channel.outbox.glob("*.json")).read_text())
        assert reply["content"] == "reply" and reply["delivery_id"] == turn_id

    asyncio.run(second())


def test_same_identity_different_payload_is_rejected_and_scope_isolated(tmp_path):
    async def scenario():
        host, channel, receipts, worker = setup(tmp_path)
        receipts.reserve(message(), worker.scope)
        assert await worker.submit(message("changed")) == {
            "accepted": False,
            "reason": "identity_conflict",
        }
        assert not host.agent.model_client.prompts
        first, _, _ = receipts.reserve(message(), worker.scope)
        second, _, _ = receipts.reserve(message(), worker.scope + "-other")
        assert first["identity"] != second["identity"]

    asyncio.run(scenario())


def test_reply_written_before_ack_republishes_same_file(tmp_path):
    async def scenario():
        host, channel, receipts, worker = setup(tmp_path)
        row, _, _ = receipts.reserve(message(), worker.scope)
        receipts.update(row["identity"], status="pending", content="reply")
        await channel.send_once(row["turn_id"], "room", "reply")
        await worker.reconcile(startup=True)
        assert len(list(channel.outbox.glob("*.json"))) == 1
        assert receipts.summary()["counts"] == {"delivered": 1}

    asyncio.run(scenario())


def test_failed_delivery_budget_and_explicit_retry_never_run_model(tmp_path):
    async def scenario():
        host, channel, receipts, worker = setup(tmp_path)
        row, _, _ = receipts.reserve(message(), worker.scope)
        receipts.update(row["identity"], status="pending", content="reply")
        original = channel.send_once

        async def fail(*args):
            raise OSError("sensitive failure")

        channel.send_once = fail
        await worker.reconcile()
        assert receipts.summary()["counts"] == {"pending": 1}
        await worker.reconcile()
        review = receipts.summary()["review"][0]
        assert review["attempts"] == 2 and review["error"] == "OSError"
        assert receipts.retry_delivery(row["turn_id"])
        channel.send_once = original
        await worker.reconcile()
        assert receipts.summary()["counts"] == {"delivered": 1}
        assert not host.agent.model_client.prompts

    asyncio.run(scenario())


def test_uncertain_execution_is_review_only(tmp_path):
    async def scenario():
        host, channel, receipts, worker = setup(tmp_path)
        row, request, _ = receipts.reserve(message(), worker.scope)
        from repoagent.spine import TurnRuntime
        from repoagent.agent_turn_runner import AgentTurnRunner

        TurnRuntime(AgentTurnRunner(host.agent), host.agent.run_store).accept(request)
        await worker.reconcile(startup=True)
        assert receipts.summary()["counts"] == {"review": 1}
        assert not receipts.retry_delivery(row["turn_id"])
        assert (await worker.submit(message()))["duplicate"]
        assert not host.agent.model_client.prompts

    asyncio.run(scenario())


def test_identical_replies_for_distinct_turns_have_distinct_files(tmp_path):
    async def scenario():
        channel = DirectoryChannel(tmp_path)
        await channel.send_once("turn_one", "room", "same")
        await channel.send_once("turn_two", "room", "same")
        assert len(list(channel.outbox.glob("*.json"))) == 2

    asyncio.run(scenario())


def test_forced_process_exit_after_turn_completion_recovers_from_disk(tmp_path):
    script = """
import asyncio, os, sys
from pathlib import Path
from repoagent import RepoAgent, FakeModelClient, WorkspaceContext, SessionStore
from repoagent.runtime_host import RuntimeHost
from repoagent.channels import DirectoryChannel, ChannelMessage
from repoagent.channel_receipts import ChannelReceipts, DurableDirectoryDelivery
async def main():
    root = Path(sys.argv[1])
    workspace = root / "workspace"
    agent = RepoAgent(model_client=FakeModelClient(["<final>disk-reply</final>"]),
        workspace=WorkspaceContext.build(workspace),
        session_store=SessionStore(workspace / ".repoagent" / "sessions"), approval_policy="never")
    host = RuntimeHost(agent)
    worker = DurableDirectoryDelivery(host, DirectoryChannel(root / "queue"), ChannelReceipts(root / "receipts.sqlite3"))
    accepted = await worker.submit(ChannelMessage("directory", "room", "local", "work", "one"))
    await host.wait(accepted["turn_id"])
    os._exit(0)
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr

    async def recover():
        host, channel, receipts, worker = setup(tmp_path)
        await worker.reconcile(startup=True)
        assert receipts.summary()["counts"] == {"delivered": 1}
        assert (await worker.submit(message()))["duplicate"]
        assert not host.agent.model_client.prompts
        assert (
            json.loads(next(channel.outbox.glob("*.json")).read_text())["content"]
            == "disk-reply"
        )

    asyncio.run(recover())


def test_retry_cli_refuses_uncertain_execution(tmp_path, capsys):
    from repoagent.cli import main
    from repoagent.paths import workspace_state_root

    receipts = ChannelReceipts(
        workspace_state_root(tmp_path) / "channel-receipts.sqlite3"
    )
    row, _, _ = receipts.reserve(message(), "scope")
    receipts.update(row["identity"], status="review", error="interrupted_execution")
    assert (
        main(["channel", "retry-delivery", row["turn_id"], "--cwd", str(tmp_path)]) == 2
    )
    assert "not retryable" in capsys.readouterr().err
    receipts.update(row["identity"], content="reply", error="OSError")
    assert (
        main(["channel", "retry-delivery", row["turn_id"], "--cwd", str(tmp_path)]) == 0
    )
    assert json.loads(capsys.readouterr().out)["counts"] == {"pending": 1}


def test_retry_deadline_survives_reopening_database(tmp_path):
    async def scenario():
        host, channel, receipts, worker = setup(tmp_path)
        row, _, _ = receipts.reserve(message(), worker.scope)
        receipts.update(
            row["identity"], status="pending", content="reply", next_attempt=10**12
        )
        reopened = DurableDirectoryDelivery(
            host, channel, ChannelReceipts(receipts.path)
        )
        await reopened.reconcile(startup=True)
        assert receipts.summary()["counts"] == {"pending": 1}
        assert not channel.outbox.exists()

    asyncio.run(scenario())


def test_run_store_recovered_failure_requires_review(tmp_path):
    async def scenario():
        host, channel, receipts, worker = setup(tmp_path)
        row, request, _ = receipts.reserve(message(), worker.scope)
        from repoagent.spine import TurnRuntime
        from repoagent.agent_turn_runner import AgentTurnRunner
        TurnRuntime(AgentTurnRunner(host.agent), host.agent.run_store).accept(request)
        host.agent.run_store.recover_incomplete_turns()
        await worker.reconcile(startup=True)
        assert receipts.summary()["review"][0]["error"] == "interrupted_execution"
        assert not receipts.retry_delivery(row["turn_id"])
    asyncio.run(scenario())
