"""Trusted container entry point for native Agent execution over host model RPC."""

import asyncio
import contextlib
import json
from pathlib import Path
import sys


def main():
    bundle = Path(sys.argv[1]).resolve()
    config = json.loads((bundle / "input.json").read_text())
    sys.path.insert(0, str(bundle / "harness"))
    sys.path.insert(1, str(bundle / "dependencies"))
    writer, reader = sys.stdout.buffer, sys.stdin.buffer
    with contextlib.redirect_stdout(sys.stderr):
        from repoagent.evolver.model_channel_guest import StdioModelClient
        from repoagent.runtime import RepoAgent
        from repoagent.session_store import SessionStore
        from repoagent.workspace import WorkspaceContext

        root = bundle / "target"
        client = StdioModelClient(config["model"], reader=reader, writer=writer)
        # This demo's host uses the native-tool DeepSeek provider path.
        client.supports_native_tools = True
        agent = RepoAgent(
            model_client=client,
            workspace=WorkspaceContext.build(root, repo_root_override=root),
            session_store=SessionStore(root / ".repoagent/sessions"),
            approval_policy="auto",
            checkpoint_policy="never",
            max_steps=config["max_calls"] * 4,
            max_provider_calls=config["max_calls"],
            max_new_tokens=config["max_output_tokens"],
            context_token_budget=16000,
            context_window_tokens=1000000,
            allowed_tools=[
                "read_file",
                "list_files",
                "write_file",
                "patch_file",
                "run_shell",
                "run_tests",
            ],
            feature_flags={"skills": False, "memory": False},
            skill_roots={"workspace": bundle / "empty-skills"},
        )
        try:
            answer = agent.ask(config["prompt"])
            result = {
                "answer": answer[:16000],
                "agent_status": agent.current_task_state.status,
                "stop_reason": agent.current_task_state.stop_reason,
                "calls": [{"index": i} for i in range(client.sequence)],
            }
        finally:
            asyncio.run(agent.aclose())
    print(json.dumps({"worker_result": result}, allow_nan=False))


if __name__ == "__main__":
    main()
