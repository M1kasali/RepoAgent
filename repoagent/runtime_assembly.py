"""Build the runtime object graph from already-resolved launch arguments."""

from __future__ import annotations

from dataclasses import dataclass

from .config import load_project_env, load_user_env
from .paths import workspace_state_root
from .run_store import RunStore
from .runtime import RepoAgent
from .sandbox import build_sandbox_adapter
from .session_store import SessionStore
from .workspace import WorkspaceContext


@dataclass(frozen=True)
class RuntimeAssembly:
    args: object
    workspace: WorkspaceContext
    model_client: object
    secret_env_names: tuple[str, ...]
    session_store: SessionStore
    run_store: RunStore
    recovered_turn_ids: tuple[str, ...]

    @classmethod
    def from_arguments(cls, args, *, model_client_factory, secret_names_factory):
        workspace = WorkspaceContext.build(
            args.cwd,
            repo_root_override=getattr(args, "repo_root_override", None),
        )
        load_user_env()
        load_project_env(workspace.repo_root)
        state_root = workspace_state_root(workspace.repo_root)
        run_store = RunStore(state_root / "runs")
        return cls(
            args=args,
            workspace=workspace,
            model_client=model_client_factory(args),
            secret_env_names=tuple(secret_names_factory(args)),
            session_store=SessionStore(state_root / "sessions"),
            run_store=run_store,
            recovered_turn_ids=tuple(run_store.recover_incomplete_turns()),
        )

    def build(self):
        args = self.args
        profile = self.model_client.profile
        session_id = args.resume
        if session_id == "latest":
            session_id = self.session_store.latest()
        options = {
            "model_client": self.model_client,
            "workspace": self.workspace,
            "session_store": self.session_store,
            "run_store": self.run_store,
            "approval_policy": args.approval,
            "max_steps": args.max_steps,
            "max_parallel_tools": getattr(args, "max_parallel_tools", 4),
            "mutation_conflict_policy": getattr(
                args, "mutation_conflict_policy", "serial"
            ),
            "require_isolation": getattr(args, "require_isolation", False),
            "sandbox_adapter": build_sandbox_adapter(
                getattr(args, "sandbox_backend", "direct"),
                self.workspace.repo_root,
                docker_executable=getattr(args, "sandbox_docker_executable", "docker"),
                docker_image=getattr(args, "sandbox_image", "python:3.12-slim"),
                docker_memory=getattr(args, "sandbox_memory", "2g"),
                docker_cpus=getattr(args, "sandbox_cpus", 2.0),
                docker_pids_limit=getattr(args, "sandbox_pids_limit", 256),
                docker_workspace_path_converter=getattr(
                    args, "sandbox_workspace_path_converter", None
                ),
                verify=getattr(args, "sandbox_backend", "direct") == "docker",
            ),
            "max_new_tokens": profile.max_output_tokens,
            "context_token_budget": getattr(args, "context_token_budget", 3000),
            "context_window_tokens": profile.context_window_tokens,
            "context_window_source": profile.context_window_source,
            "secret_env_names": self.secret_env_names,
            "checkpoint_policy": getattr(args, "checkpoint_policy", "interactive"),
            "interactive": not bool(getattr(args, "prompt", [])),
        }
        agent = (
            RepoAgent.from_session(session_id=session_id, **options)
            if session_id
            else RepoAgent(**options)
        )
        agent.recovered_turn_ids = self.recovered_turn_ids
        return agent


def assemble_runtime(args, *, model_client_factory, secret_names_factory):
    return RuntimeAssembly.from_arguments(
        args,
        model_client_factory=model_client_factory,
        secret_names_factory=secret_names_factory,
    ).build()


__all__ = ["RuntimeAssembly", "assemble_runtime"]
