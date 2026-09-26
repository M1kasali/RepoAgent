"""One real process in the BoxLite + JSONL runtime acceptance scenario."""

# Bootstrap the checkout for this directly invoked subprocess fixture.
# ruff: noqa: E402

import asyncio
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
import time

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from repoagent import FakeModelClient, RepoAgent, SessionStore, WorkspaceContext
from repoagent.boxlite_adapter import BoxliteSandboxAdapter
from repoagent.boxlite_sandbox import SandboxConfig
from repoagent.boxlite_sandbox._runtime import get_boxlite_runtime, _runtime_cache
from repoagent.mcp_transport import StdioMCPClient, StdioServerConfig


OBSERVED = []


class ObservedAdapter(BoxliteSandboxAdapter):
    async def _start(self):
        await super()._start()
        box_id = self._executor._box.id
        if getattr(self, "_observed_id", None) != box_id:
            self._observed_id = box_id
            info = next(
                b for b in await get_boxlite_runtime().list_info() if b.id == box_id
            )
            OBSERVED.append(
                {"id": box_id, "pid": info.state.pid, "workspace": str(self.workspace)}
            )


def tool(name, **args):
    return "<tool>" + json.dumps({"name": name, "args": args}) + "</tool>"


async def main(phase, root):
    workspace = root / "workspace"
    os.environ["REPOAGENT_DATA_DIR"] = str(root / "runtime")
    workspace.mkdir(exist_ok=True)
    sessions = SessionStore(root / "sessions")
    if phase == "create":
        (workspace / "calc.py").write_text("def add(a, b):\n    return a - b\n")
        (workspace / "test_calc.py").write_text(
            "import unittest\nfrom calc import add\n"
            "class Tests(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
        )
        spec = importlib.util.spec_from_file_location(
            "live_fixture", PROJECT / "tests/test_boxlite_live.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        (workspace / "mcp_fixture.py").write_text(module.MCP_SERVER)
        outputs = [
            tool("read_file", path="calc.py"),
            tool("run_shell", command="python3 -m unittest test_calc"),
            tool(
                "patch_file",
                path="calc.py",
                old_text="return a - b",
                new_text="return a + b",
            ),
            tool(
                "run_shell",
                command="python3 -m unittest test_calc && echo shell-shared > /tmp/from-shell",
            ),
            tool("mcp_shared_exchange"),
            tool(
                "delegate",
                task="Inspect the corrected addition and write a note in your isolated workspace.",
                role="implementer",
                max_steps=4,
            ),
            tool("read_file", path="calc.py"),
            tool(
                "write_file", path="child-only.txt", content="Reviewed return a + b.\n"
            ),
            "<final>Child inspected the fix in its isolated workspace.</final>",
            tool(
                "run_shell",
                command="test ! -e child-only.txt && cat /tmp/from-mcp && python3 -m unittest test_calc",
            ),
            "<final>Fixed addition, passed tests, and completed isolated child review.</final>",
        ]
    else:
        initial = json.loads((root / "create.json").read_text())
        saved = sessions.load(initial["session_id"])
        original_history = saved["history"][:]
        assert any(
            "Fixed addition" in str(m.get("content", "")) for m in original_history
        )
        outputs = [
            tool("read_file", path="calc.py"),
            tool(
                "run_shell",
                command="test ! -e /tmp/from-shell && python3 -m unittest test_calc && echo resumed > resumed.txt",
            ),
            "<final>Resumed the saved session and verified the persisted fix.</final>",
        ]
    adapter = ObservedAdapter(
        workspace,
        config=SandboxConfig(backend="boxlite", image="python:3.12-slim-bookworm"),
    )
    client = StdioMCPClient(
        StdioServerConfig(
            "python3", ("-u", "/workspace/mcp_fixture.py"), startup_timeout=300
        ),
        cwd=workspace,
        sandbox_adapter=adapter,
    )
    agent = None
    try:
        options = dict(
            model_client=FakeModelClient(outputs),
            workspace=WorkspaceContext.build(workspace, repo_root_override=workspace),
            session_store=sessions,
            approval_policy="auto",
            sandbox_adapter=adapter,
            require_isolation=True,
            max_steps=20,
            context_state_root=root / "context",
            mcp_servers={"shared": client} if phase == "create" else None,
        )
        if phase == "create":
            agent = RepoAgent(**options)
        else:
            agent = RepoAgent.from_session(session_id=initial["session_id"], **options)
            assert agent.session["history"] == original_history
        answer = await agent.ask_async(
            "Fix and verify addition."
            if phase == "create"
            else "Continue the saved task and verify the fix."
        )
        assert agent.current_task_state.status == "completed"
        report = agent.run_store.load_report(agent.current_task_state.run_id)
        history = agent.session["history"]
        tools = [m for m in history if m.get("role") == "tool"]
        assert "return a + b" in (workspace / "calc.py").read_text()
        assert not (workspace / "child-only.txt").exists()
        if phase == "create":
            assert "Fixed addition" in answer
            assert len(OBSERVED) == 2, OBSERVED
            assert report["subagent_summary"]["completed"] == 1, report[
                "subagent_summary"
            ]
            child_evidence = (
                agent.current_run_dir
                / report["subagents"][0]["outcome"]["evidence"]["path"]
            )
            child_trace = [
                json.loads(line)
                for line in (child_evidence / "trace.jsonl").read_text().splitlines()
            ]
            child_tools = [
                row for row in child_trace if row.get("event") == "tool_executed"
            ]
            assert [row["name"] for row in child_tools] == [
                "read_file",
                "write_file",
            ], child_tools
            assert all(row["status"] == "ok" for row in child_tools), child_tools
            assert "return a + b" in child_tools[0]["tool_result"]["content"], (
                child_tools
            )
            assert child_tools[1]["tool_result"]["workspace_changed"], child_tools
            shell = [m["content"] for m in tools if m.get("name") == "run_shell"]
            assert len(shell) == 3, tools
            assert "exit_code: 1" in shell[0] and "FAILED" in shell[0], shell
            assert all("exit_code: 0" in text for text in shell[1:]), shell
            assert "mcp-shared" in shell[-1]
            assert any(
                m.get("name") == "mcp_shared_exchange"
                and "shell-shared" in m["content"]
                for m in tools
            ), tools
            live = await get_boxlite_runtime().list_info()
            assert [b.id for b in live] == [OBSERVED[0]["id"]], live
        else:
            assert "Resumed" in answer
            assert history[: len(original_history)] == original_history
            assert len(history) > len(original_history)
            assert (workspace / "resumed.txt").read_text().strip() == "resumed"
            assert len(OBSERVED) == 1 and OBSERVED[0]["id"] not in initial["vm_ids"]
        payload = sessions.load(agent.session["id"])
        assert payload["history"] == history
        session_path = sessions.path(agent.session["id"])
        assert session_path.suffix == ".jsonl"
        assert all(
            isinstance(json.loads(line), dict)
            for line in session_path.read_text().splitlines()
        )
        result = {
            "phase": phase,
            "pid": os.getpid(),
            "session_id": agent.session["id"],
            "history_count": len(history),
            "vm_ids": [b["id"] for b in OBSERVED],
            "session_path": str(session_path.relative_to(root)),
            "answer": answer,
            "tool_results": [
                {"name": m.get("name"), "content": m["content"]} for m in tools
            ],
            "subagents": report.get("subagent_summary"),
            "child_tool_statuses": [row["status"] for row in child_tools]
            if phase == "create"
            else [],
        }
    finally:
        if agent is not None:
            await agent.aclose()
        client.close()
        adapter.close_processes()
        runtime = get_boxlite_runtime()
        try:
            assert await runtime.list_info() == []
            deadline = time.monotonic() + 5
            while (
                any(Path(f"/proc/{b['pid']}").exists() for b in OBSERVED)
                and time.monotonic() < deadline
            ):
                await asyncio.sleep(0.05)
            assert all(not Path(f"/proc/{b['pid']}").exists() for b in OBSERVED), (
                OBSERVED
            )
        finally:
            await runtime.shutdown()
            closed = runtime.close()
            if inspect.isawaitable(closed):
                await closed
            _runtime_cache.clear()
    result["vm_cleanup_confirmed"] = True
    (root / f"{phase}.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], Path(sys.argv[2])))
