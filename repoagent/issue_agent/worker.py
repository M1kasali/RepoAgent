"""Trusted container entry point for native Agent execution over host model RPC."""

import asyncio
import contextlib
import json
from pathlib import Path
import sys


def context_options():
    # Let native assembly derive the prompt allowance from the context window.
    # Host model admission independently enforces its full-request byte budget.
    return {"context_token_budget": None, "context_window_tokens": 1000000}


def main():
    bundle = Path(sys.argv[1]).resolve()
    config = json.loads((bundle / "input.json").read_text())
    sys.path.insert(0, str(bundle / "harness"))
    sys.path.insert(1, str(bundle / "dependencies"))
    writer, reader = sys.stdout.buffer, sys.stdin.buffer
    with contextlib.redirect_stdout(sys.stderr):
        from repoagent.evolver.model_channel_guest import StdioModelClient
        from repoagent.issue_agent.admission import fit_issue_request
        from repoagent.issue_agent.execution_policy import budgeted_request
        from repoagent.runtime import RepoAgent
        from repoagent.session_store import SessionStore
        from repoagent.workspace import WorkspaceContext

        root = bundle / "target"
        class IssueModelClient(StdioModelClient):
            def generate(self, request):
                remaining = config["max_calls"] - self.sequence
                prepared = budgeted_request(request, remaining=remaining)
                if prepared is not request:
                    closeout_calls.append({"index": self.sequence, "remaining": remaining,
                                           "tools_enabled": bool(prepared.tools)})
                fitted, reduction = fit_issue_request(
                    prepared, model=self.model, input_limit=config["input_limit"],
                    temperature=config.get("counter_temperature"),
                )
                if reduction is not None:
                    reductions.append(reduction)
                return super().generate(fitted)

        reductions = []
        closeout_calls = []
        client = IssueModelClient(config["model"], reader=reader, writer=writer)
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
            **context_options(),
            allowed_tools=[
                "read_file",
                "list_files",
                "write_file",
                "patch_file",
                "run_shell",
                "run_tests",
            ],
            feature_flags={"skills": config.get("enable_strategy_skill", False), "memory": False},
            skill_roots={"workspace": bundle / (
                "skills" if config.get("enable_strategy_skill", False) else "empty-skills"
            )},
        )
        try:
            answer = agent.ask(config["prompt"])
            result = {
                "answer": answer[:16000],
                "agent_status": agent.current_task_state.status,
                "stop_reason": agent.current_task_state.stop_reason,
                "calls": [{"index": i} for i in range(client.sequence)],
                "active_skills": [skill.qualified_id for skill in agent.active_skills],
                "input_reductions": reductions,
                "closeout_calls": closeout_calls,
            }
        except Exception as exc:
            result = {
                "answer": "Worker execution failed; inspect retained runtime evidence.",
                "agent_status": "failed",
                "stop_reason": type(exc).__name__,
                "budget_reason": getattr(exc, "reason", None),
                "calls": [{"index": i} for i in range(client.sequence)],
                "active_skills": [skill.qualified_id for skill in agent.active_skills],
                "input_reductions": reductions,
                "closeout_calls": closeout_calls,
            }
        finally:
            asyncio.run(agent.aclose())
    print(json.dumps({"worker_result": result}, allow_nan=False))


if __name__ == "__main__":
    main()
