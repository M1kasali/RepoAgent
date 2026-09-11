import asyncio
import io
import json
import os
from pathlib import Path
import sys

import pytest

from repoagent.evolver.model_channel import ModelCallJournal, run_model_worker
from repoagent.evolver.model_channel_guest import StdioModelClient
from repoagent.evolver.model_proxy import HostModelProxy, ModelProxyError
from repoagent.providers.base import ModelRequest
from test_evolver_model_budget import LeafClient, _client


class LocalPipeAdapter:
    """Real subprocess pipes; substitute only the Docker ownership boundary."""

    executable = sys.executable

    def __init__(self, root):
        self.workspace = root
        self.finished = []

    def prepare_process(self, command, args, **kwargs):
        assert kwargs["env"] == {}
        return "owned", args

    def _docker_environment(self):
        return {"PYTHONPATH": str(Path.cwd()), "PATH": os.defpath}

    def finish_process(self, handle):
        self.finished.append(handle)


WORKER = """
import contextlib, json, sys
from pathlib import Path
from repoagent import RepoAgent, SessionStore, WorkspaceContext
from repoagent.evolver.model_channel_guest import StdioModelClient
client = StdioModelClient("fixture-model")
with contextlib.redirect_stdout(sys.stderr):
    root = Path.cwd()
    agent = RepoAgent(model_client=client,
        workspace=WorkspaceContext.build(root, repo_root_override=root),
        session_store=SessionStore(root / ".repoagent/sessions"),
        approval_policy="auto", checkpoint_policy="never",
        max_steps=2, max_provider_calls=2, max_new_tokens=20,
        allowed_tools=["write_file"], feature_flags={"skills": False})
    answer = agent.ask("Write done to result.txt")
print(json.dumps({"worker_result": {"answer": answer}}))
"""


def test_actual_agent_subprocess_calls_host_and_journals_outside_workspace(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    journal = ModelCallJournal(tmp_path / "journal", worker_root=work)
    leaf = LeafClient(
        outputs=[
            '<tool name="write_file" path="result.txt"><content>done</content></tool>',
            "<final>Done.</final>",
        ]
    )
    proxy = HostModelProxy(_client(leaf), evidence_sink=journal)
    adapter = LocalPipeAdapter(work)
    result = asyncio.run(
        run_model_worker(
            adapter, "python", ["-c", WORKER], cwd=work, proxy=proxy, timeout_seconds=10
        )
    )
    assert result["answer"]
    assert (work / "result.txt").read_text() == "done"
    assert len(leaf.requests) == 2
    rows = journal.ledger.events()
    assert [r["payload"].get("status") for r in rows] == [
        None,
        "started",
        "completed",
        "started",
        "completed",
    ]
    assert rows[-1]["payload"]["model_evidence"]["cost_complete"]
    assert adapter.finished == ["owned"]
    with pytest.raises(FileExistsError):
        ModelCallJournal(tmp_path / "journal", worker_root=work)


@pytest.mark.parametrize(
    "program",
    [
        'print("invalid")',
        'print("x" * 1000001)',
        'print(\'{"worker_result": {}}\'); print("trailing")',
        "import time; time.sleep(10)",
    ],
)
def test_bad_or_stalled_worker_is_reaped(tmp_path, program):
    adapter = LocalPipeAdapter(tmp_path)
    leaf = LeafClient()
    proxy = HostModelProxy(_client(leaf), evidence_sink=lambda row: None)
    with pytest.raises((ModelProxyError, ValueError, TimeoutError)):
        asyncio.run(
            run_model_worker(
                adapter,
                "python",
                ["-c", program],
                cwd=tmp_path,
                proxy=proxy,
                timeout_seconds=0.2,
            )
        )
    assert adapter.finished == ["owned"]
    assert not leaf.requests


def test_journal_rejects_worker_mount_and_symlink_alias(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(work)
    for path in (work / "journal", alias / "journal"):
        with pytest.raises(ValueError):
            ModelCallJournal(path, worker_root=work)


def test_guest_rejects_wrong_identity_and_blocks_reuse():
    client = StdioModelClient(
        "expected",
        reader=io.BytesIO(b'{"sequence":0,"result":{"model":"wrong"}}\n'),
        writer=io.BytesIO(),
    )
    with pytest.raises(Exception, match="identity"):
        client.generate(ModelRequest("hello", 10))
    assert client.closed


def test_guest_request_omits_cancellation_object():
    writer = io.BytesIO()
    result = {"model": "fixture-model", "text": "done", "tool_calls": [], "usage": {}}
    client = StdioModelClient(
        "fixture-model",
        reader=io.BytesIO(
            json.dumps({"sequence": 0, "result": result}).encode() + b"\n"
        ),
        writer=writer,
    )
    assert client.generate(ModelRequest("hello", 10)).text == "done"
    assert "cancellation_token" not in json.loads(writer.getvalue())["request"]


def test_cancellation_reaps_owned_worker(tmp_path):
    adapter = LocalPipeAdapter(tmp_path)
    proxy = HostModelProxy(_client(), evidence_sink=lambda row: None)

    async def cancel():
        task = asyncio.create_task(
            run_model_worker(
                adapter,
                "python",
                ["-c", "import time; time.sleep(30)"],
                cwd=tmp_path,
                proxy=proxy,
            )
        )
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel())
    assert adapter.finished == ["owned"]


@pytest.mark.skipif(
    not os.environ.get("REPOAGENT_TEST_DOCKER"), reason="opt-in Docker functional test"
)
def test_real_docker_pipe_calls_host_without_network(tmp_path):
    from repoagent.sandbox_session import PersistentDockerSandboxAdapter

    work = tmp_path / "work"
    work.mkdir()
    journal = ModelCallJournal(tmp_path / "private-journal", worker_root=work)
    proxy = HostModelProxy(_client(), evidence_sink=journal)
    adapter = PersistentDockerSandboxAdapter(
        work, executable=os.environ["REPOAGENT_TEST_DOCKER"], image="python:3.12-slim"
    )
    program = """
import json, sys
print(json.dumps({"sequence":0,"request":{"prompt":"hello","max_output_tokens":20}}), flush=True)
reply = json.loads(sys.stdin.readline())
print(json.dumps({"worker_result":{"model":reply["result"]["model"]}}), flush=True)
"""
    try:
        result = asyncio.run(
            run_model_worker(
                adapter,
                "python",
                ["-I", "-B", "-c", program],
                cwd=work,
                proxy=proxy,
                timeout_seconds=15,
            )
        )
        assert result["model"] == "fixture-model"
        assert journal.ledger.events()[-1]["payload"]["status"] == "completed"
        assert not (work / "private-journal").exists()
    finally:
        adapter.stop()
