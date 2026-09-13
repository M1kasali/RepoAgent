"""Standalone trusted driver; imports RepoAgent only from the supplied snapshot."""

import contextlib
import hashlib
import json
from pathlib import Path
import sys


def main():
    source = Path(sys.argv[1]).resolve()
    task_root = Path(sys.argv[2]).resolve()
    config = json.loads(Path(sys.argv[3]).read_text())
    sys.path.insert(0, str(source))
    sys.path.insert(1, str(Path(sys.argv[3]).resolve().parent / "dependencies"))
    channel_writer = sys.stdout.buffer
    channel_reader = sys.stdin.buffer
    with contextlib.redirect_stdout(sys.stderr):
        import repoagent
        from repoagent.providers.base import ModelResult, ModelUsage, ProviderError
        from repoagent.runtime import RepoAgent
        from repoagent.session_store import SessionStore
        from repoagent.workspace import WorkspaceContext

        class ScriptedProvider:
            model = "scripted-snapshot-fixture"

            def __init__(self):
                self.calls = []

            def generate(self, request):
                if len(self.calls) >= config["max_calls"]:
                    raise ProviderError(
                        "scripted call limit", category="evaluation_budget"
                    )
                if request.max_output_tokens > config["max_output_tokens"]:
                    raise ProviderError(
                        "scripted output limit", category="evaluation_budget"
                    )
                index = len(self.calls)
                self.calls.append(
                    {
                        "prompt_digest": hashlib.sha256(
                            request.prompt.encode()
                        ).hexdigest(),
                        "call_kind": request.call_kind,
                    }
                )
                if index >= len(config["responses"]):
                    raise ProviderError(
                        "script exhausted", category="evaluation_fixture"
                    )
                return ModelResult(
                    text=config["responses"][index],
                    model=self.model,
                    provider="scripted",
                    usage=ModelUsage(),
                )

        if "channel_source" in config:
            namespace = {}
            exec(config["channel_source"], namespace)
            client = namespace["StdioModelClient"](
                config["model"], reader=channel_reader, writer=channel_writer
            )
        else:
            client = ScriptedProvider()
        agent = RepoAgent(
            model_client=client,
            workspace=WorkspaceContext.build(task_root, repo_root_override=task_root),
            session_store=SessionStore(task_root / ".repoagent/sessions"),
            approval_policy="auto",
            checkpoint_policy="never",
            max_steps=config["max_calls"],
            max_provider_calls=config["max_calls"],
            max_new_tokens=config["max_output_tokens"],
            allowed_tools=["read_file", "write_file", "patch_file", "list_files"]
            + (["run_tests"] if config.get("enable_tests", False) else []),
            feature_flags={"skills": config.get("enable_skills", False)},
            skill_roots={"workspace": source / "skills"},
        )
        answer = agent.ask(config["prompt"])
        modules = {}
        for name in ("repoagent", "repoagent.runtime", "repoagent.prompt_prefix"):
            path = Path(sys.modules[name].__file__).resolve()
            modules[name] = {
                "path": str(path.relative_to(source)),
                "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        result = {
            "schema": "repoagent.agent-snapshot-worker/v1",
            "modules": modules,
            "calls": client.calls
            if hasattr(client, "calls")
            else [{"index": index} for index in range(client.sequence)],
            "status": agent.current_task_state.status,
            "stop_reason": agent.current_task_state.stop_reason,
            "answer": answer[:2000],
            "prefix_excerpt": agent.prefix[:2048],
            "active_skills": [skill.qualified_id for skill in agent.active_skills],
        }
        if config.get("enable_tests", False):
            from repoagent.test_verification import refreshed_verifications

            names = [
                row["name"]
                for row in agent.session["history"]
                if row.get("role") == "tool"
            ]
            result["tool_names"] = names[:100]
            result["tool_names_truncated"] = len(names) > 100
            result["test_verifications"] = refreshed_verifications(
                agent.current_task_state.test_verifications, task_root
            )
        assert Path(repoagent.__file__).resolve().is_relative_to(source)
    if "channel_source" in config:
        result = {"worker_result": result}
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
