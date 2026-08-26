"""Agent 运行时核心逻辑。

RepoAgent 就是包在模型外面的控制循环：负责组 prompt、解析模型输出、
校验并执行工具、写 trace、更新工作记忆，以及在合适的时候停下来。
"""

import asyncio
import json
import hashlib
import os
import re
import time
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from . import checkpoint as checkpointlib
from .features import memory as memorylib
from .mcp import MCPManager
from .memory_backend import MemoryBackend
from .memory_consolidation import MemoryConsolidator
from . import security as securitylib
from .approval import EffectApprovalPolicy
from .capabilities import CapabilityAuthority, capability_token_digest
from .context_manager import ContextManager, DEFAULT_TOTAL_TOKEN_BUDGET
from .context_window import ContextWindowBudget, DEFAULT_CONTEXT_WINDOW_TOKENS
from .empty_recovery import RecoveryLimits
from .checkpoint import CHECKPOINT_NONE_STATUS
from .prompt_prefix import build_prompt_prefix, tool_signature
from .providers.base import ProviderCancelledError
from .paths import workspace_state_root
from .run_store import RunStore
from .sandbox import DirectSandboxAdapter
from .session_store import SessionStore
from .subagents import (
    IsolatedSubagentWorkspace,
    ROLE_PROFILES,
    SubagentBudget,
    SubagentMessage,
    SubagentOutcome,
    SubagentRequest,
    persist_subagent_evidence,
)
from .skills import LocalSkillPool, SkillCatalog, SkillChangeWatcher
from .tool_context import ToolContext
from .tool_contracts import ToolEffect, ToolRequest, validate_tool_arguments
from .tool_executor import ToolExecutor
from .tool_gateway import ToolGateway, compatibility_metadata
from .tool_scheduling import MutationConflictPolicy
from .tracing import (
    current_trace_context,
    infer_trace_stage,
    trace_attributes,
    validate_semantic_event,
)
from . import tools as toolkit
from .workspace import IGNORED_PATH_NAMES, MAX_HISTORY, WorkspaceContext, clip, now
from .workspace_checkpoint import (
    WorkspaceCheckpointService,
    checkpoint_policy_active,
)

DEFAULT_SHELL_ENV_ALLOWLIST = (
    "ComSpec",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "PWD",
    "SHELL",
    "TERM",
    "TMPDIR",
    "TMP",
    "TEMP",
    "USER",
    "SystemRoot",
    "WINDIR",
)
DEFAULT_FEATURE_FLAGS = {
    "memory": True,
    "relevant_memory": True,
    "context_reduction": True,
    "prompt_cache": True,
    "skills": True,
}

__all__ = ["RepoAgent", "SessionStore"]


class RepoAgent:
    def __init__(
        self,
        model_client,
        workspace,
        session_store,
        session=None,
        run_store=None,
        approval_policy="ask",
        max_steps=20,
        max_parallel_tools=ToolGateway.DEFAULT_MAX_PARALLEL,
        mutation_conflict_policy=MutationConflictPolicy.SERIAL.value,
        max_new_tokens=4096,
        depth=0,
        max_depth=1,
        read_only=False,
        shell_env_allowlist=None,
        secret_env_names=None,
        feature_flags=None,
        allowed_tools=None,
        capability_authority=None,
        parent_capability_token="",
        mcp_servers=None,
        sandbox_adapter=None,
        require_isolation=False,
        network_policy=None,
        context_token_budget=None,
        segment_token_budgets=None,
        token_counter=None,
        context_window_tokens=None,
        context_window_source=None,
        memory_backend=None,
        skill_roots=None,
        plugin_manager=None,
        empty_recovery=None,
        checkpoint_policy="interactive",
        interactive=False,
    ):
        self.model_client = model_client
        self.workspace = workspace
        self.root = Path(workspace.repo_root)
        self.session_store = session_store
        self.approval_policy = approval_policy
        self.max_steps = max_steps
        if (
            isinstance(max_parallel_tools, bool)
            or not isinstance(max_parallel_tools, int)
            or max_parallel_tools < 1
        ):
            raise ValueError("max_parallel_tools must be a positive integer")
        self.max_parallel_tools = max_parallel_tools
        try:
            self.mutation_conflict_policy = MutationConflictPolicy(
                mutation_conflict_policy
            ).value
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported mutation conflict policy") from exc
        self.max_new_tokens = max_new_tokens
        self.empty_recovery_limits = empty_recovery or RecoveryLimits()
        if not isinstance(self.empty_recovery_limits, RecoveryLimits):
            raise TypeError("empty_recovery must be RecoveryLimits or None")
        self.checkpoint_policy = str(checkpoint_policy)
        self.interactive = bool(interactive)
        checkpoint_active = checkpoint_policy_active(
            self.checkpoint_policy, interactive=self.interactive
        )
        self.depth = depth
        self.max_depth = max_depth
        self.read_only = read_only
        self.sandbox_adapter = sandbox_adapter or DirectSandboxAdapter()
        self.require_isolation = bool(require_isolation)
        self.shell_env_allowlist = tuple(
            shell_env_allowlist or DEFAULT_SHELL_ENV_ALLOWLIST
        )
        self.secret_env_names = {str(name).upper() for name in (secret_env_names or ())}
        self.memory_consolidator = MemoryConsolidator(self.redact_text)
        self.feature_flags = dict(DEFAULT_FEATURE_FLAGS)
        if feature_flags:
            self.feature_flags.update(
                {str(key): bool(value) for key, value in feature_flags.items()}
            )
        self.allowed_tools = self._normalize_allowed_tools(allowed_tools)
        self.run_store = run_store or RunStore(
            workspace_state_root(workspace.repo_root) / "runs"
        )
        self.run_store.set_redactor(self.redact_artifact)
        self.workspace_checkpoint_service = (
            WorkspaceCheckpointService(
                self.root,
                state_root=workspace_state_root(self.root),
            )
            if checkpoint_active
            else None
        )
        if self.workspace_checkpoint_service is not None:
            self.workspace_checkpoint_service.commit_turn("session baseline")
        self.session = session or {
            "id": datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
            "created_at": now(),
            "workspace_root": workspace.repo_root,
            "history": [],
            "memory": memorylib.default_memory_state(),
        }
        self._ensure_session_shape()
        self.memory = memorylib.LayeredMemory(
            self.session.setdefault("memory", memorylib.default_memory_state()),
            workspace_root=self.root,
        )
        self.session["memory"] = self.memory.to_dict()
        self.memory_backend = memory_backend or self.memory
        if not isinstance(self.memory_backend, MemoryBackend):
            raise TypeError("memory_backend must implement MemoryBackend")
        self._memory_backend_started = False
        self.backend_memory_hits = []
        self.last_memory_backend_metadata = {
            "backend": type(self.memory_backend).__name__,
            "recall_status": "not_run",
            "store_status": "not_run",
            "recalled_count": 0,
            "stored_message_count": 0,
            "rejected_secret_hits": 0,
        }
        self.skill_roots = dict(
            skill_roots
            or {
                "workspace": self.root / "skills",
                "local": workspace_state_root(self.root) / "skills",
            }
        )
        self.skill_catalog = SkillCatalog(self.skill_roots)
        self.skill_pool = LocalSkillPool(self.skill_catalog)
        self.skill_watcher = SkillChangeWatcher(self.skill_catalog)
        self.active_skills = ()
        self.plugin_manager = plugin_manager
        self.network_policy = network_policy or securitylib.NetworkPolicy()
        self.mcp_manager = MCPManager(mcp_servers, network_policy=self.network_policy)
        self.tools = self.build_tools()
        discovered_mcp_tools = self.mcp_manager.discover(self.tools)
        self.tools.update(discovered_mcp_tools)
        if self.plugin_manager is not None:
            self.tools = self.plugin_manager.register_tools(self.tools)
            self.plugin_manager.start()
        self.tools = self._apply_tool_allowlist(self.tools)
        self.approval_engine = EffectApprovalPolicy(
            approval_policy,
            read_only=read_only,
            prompt=self._prompt_for_approval,
        )
        self.capability_authority = capability_authority or CapabilityAuthority()
        self.capability_subject_id = self.session["id"]
        granted_tools = {
            name: entry["definition"]
            for name, entry in self.tools.items()
            if not read_only or entry["definition"].effect is ToolEffect.READ
        }
        self.capability_token = self.capability_authority.issue(
            subject_id=self.capability_subject_id,
            session_id=self.session["id"],
            effects=tuple(
                dict.fromkeys(
                    definition.effect for definition in granted_tools.values()
                )
            ),
            tools=tuple(granted_tools),
            parent_token=parent_capability_token,
        )
        self.tool_gateway = ToolGateway(
            self,
            max_parallel=max_parallel_tools,
            mutation_conflict_policy=self.mutation_conflict_policy,
        )
        self.tool_executor = ToolExecutor(self)
        self.prefix_state = self.build_prefix()
        self.prefix = self.prefix_state.text
        profile = getattr(self.model_client, "profile", None)
        configured_window = (
            context_window_tokens
            if context_window_tokens is not None
            else getattr(profile, "context_window_tokens", None)
            or DEFAULT_CONTEXT_WINDOW_TOKENS
        )
        configured_input = (
            context_token_budget
            if context_token_budget is not None
            else DEFAULT_TOTAL_TOKEN_BUDGET
        )
        configured_window_source = context_window_source or (
            "runtime-argument"
            if context_window_tokens is not None
            else getattr(profile, "context_window_source", None) or "runtime-default"
        )
        self.context_window_budget = ContextWindowBudget(
            context_window_tokens=int(configured_window),
            configured_input_tokens=int(configured_input),
            reserved_output_tokens=int(self.max_new_tokens),
            window_source=str(configured_window_source),
        )
        self.context_manager = ContextManager(
            self,
            total_token_budget=self.context_window_budget.effective_input_tokens,
            segment_token_budgets=segment_token_budgets,
            token_counter=token_counter,
        )
        self.resume_state = self.evaluate_resume_state()
        self.session_path = self.session_store.save(self.session)
        self.current_task_state = None
        self.current_run_dir = None
        self.last_prompt_metadata = {}
        self.last_completion_metadata = {}
        self.last_model_result = None
        self.last_call_efficiency_summary = {}
        self.last_durable_promotions = []
        self.last_durable_rejections = []
        self.last_durable_superseded = []
        self.last_memory_consolidation = {}
        self.last_subagent_records = []
        self._last_tool_result_metadata = {}
        self._tool_call_sequence = 0
        self._last_prefix_refresh = {
            "workspace_changed": False,
            "prefix_changed": False,
        }
        self._turn_runtime = None
        self._scheduler = None
        self._scheduler_loop = None

    @classmethod
    def from_session(cls, model_client, workspace, session_store, session_id, **kwargs):
        return cls(
            model_client=model_client,
            workspace=workspace,
            session_store=session_store,
            session=session_store.load(session_id),
            **kwargs,
        )

    def _ensure_session_shape(self):
        self.session.setdefault("history", [])
        self.session.setdefault("memory", memorylib.default_memory_state())
        checkpoints = self.session.setdefault("checkpoints", {})
        if not isinstance(checkpoints, dict):
            checkpoints = {}
            self.session["checkpoints"] = checkpoints
        checkpoints.setdefault("current_id", "")
        checkpoints.setdefault("items", {})
        runtime_identity = self.session.setdefault("runtime_identity", {})
        if not isinstance(runtime_identity, dict):
            self.session["runtime_identity"] = {}
        resume_state = self.session.setdefault("resume_state", {})
        if not isinstance(resume_state, dict):
            self.session["resume_state"] = {}

    def current_runtime_identity(self):
        return checkpointlib.current_runtime_identity(self)

    def checkpoint_state(self):
        return checkpointlib.checkpoint_state(self)

    def current_checkpoint(self):
        return checkpointlib.current_checkpoint(self)

    def invalidate_stale_memory(self):
        invalidated = self.memory.invalidate_stale_file_summaries()
        self.session["memory"] = self.memory.to_dict()
        return invalidated

    def evaluate_resume_state(self):
        return checkpointlib.evaluate_resume_state(self)

    def render_checkpoint_text(self):
        return checkpointlib.render_checkpoint_text(self)

    @staticmethod
    def remember(bucket, item, limit):
        if not item:
            return
        if item in bucket:
            bucket.remove(item)
        bucket.append(item)
        del bucket[:-limit]

    def build_tools(self):
        return toolkit.build_tool_registry(self.tool_context())

    @staticmethod
    def _normalize_allowed_tools(allowed_tools):
        if allowed_tools is None:
            return None
        normalized = tuple(str(name).strip() for name in allowed_tools)
        if not normalized or any(not name for name in normalized):
            raise ValueError("allowed_tools must be a non-empty sequence of tool names")
        return normalized

    def _apply_tool_allowlist(self, tools):
        if self.allowed_tools is None:
            return tools
        unknown = [name for name in self.allowed_tools if name not in tools]
        if unknown:
            raise ValueError(f"unknown allowed tool: {', '.join(unknown)}")
        allowed = set(self.allowed_tools)
        return {name: tool for name, tool in tools.items() if name in allowed}

    def tool_signature(self):
        return tool_signature(self.tools)

    def build_prefix(self):
        return build_prompt_prefix(workspace=self.workspace, tools=self.tools)

    def _apply_prefix_state(self, prefix_state):
        self.prefix_state = prefix_state
        self.prefix = prefix_state.text

    def refresh_prefix(self, force=False):
        previous_hash = getattr(getattr(self, "prefix_state", None), "hash", None)
        previous_workspace_fingerprint = getattr(
            getattr(self, "prefix_state", None), "workspace_fingerprint", None
        )

        # 工作区事实相对稳定，所以这里按整体刷新；
        # 只有这些事实真的变化了，才重建完整 prefix。
        refreshed_workspace = WorkspaceContext.build(self.root)
        refreshed_workspace_fingerprint = refreshed_workspace.fingerprint()
        workspace_changed = (
            force or refreshed_workspace_fingerprint != previous_workspace_fingerprint
        )
        if workspace_changed:
            self.workspace = refreshed_workspace

        prefix_state = (
            self.build_prefix()
            if workspace_changed or force or previous_hash is None
            else self.prefix_state
        )
        prefix_changed = force or previous_hash != prefix_state.hash
        if prefix_changed:
            self._apply_prefix_state(prefix_state)

        self._last_prefix_refresh = {
            "workspace_changed": workspace_changed,
            "prefix_changed": prefix_changed,
        }
        return dict(self._last_prefix_refresh)

    def memory_text(self):
        return self.memory.render_memory_text()

    def skill_text(self):
        if not self.active_skills:
            return ""
        lines = ["Skills:"]
        for skill in self.active_skills:
            lines.extend(
                [
                    f"## {skill.manifest.name} [{skill.qualified_id}]",
                    skill.content,
                ]
            )
        return "\n".join(lines)

    def history_text(self):
        history = self.session["history"]
        if not history:
            return "- empty"

        lines = []
        seen_reads = set()
        recent_start = max(0, len(history) - 6)
        for index, item in enumerate(history):
            recent = index >= recent_start
            if item["role"] == "tool" and item["name"] == "read_file" and not recent:
                path = str(item["args"].get("path", ""))
                if path in seen_reads:
                    continue
                seen_reads.add(path)

            if item["role"] == "tool":
                limit = 900 if recent else 180
                lines.append(
                    f"[tool:{item['name']}] {json.dumps(item['args'], sort_keys=True)}"
                )
                lines.append(clip(item["content"], limit))
            else:
                limit = 900 if recent else 220
                lines.append(f"[{item['role']}] {clip(item['content'], limit)}")

        return clip("\n".join(lines), MAX_HISTORY)

    def feature_enabled(self, name):
        return bool(self.feature_flags.get(str(name), False))

    def prompt(self, user_message):
        prompt, _ = self._build_prompt_and_metadata(user_message)
        return prompt

    def record(self, item):
        self.session["history"].append(item)
        self.session_path = self.session_store.save(self.session)

    @staticmethod
    def looks_sensitive_env_name(name):
        return securitylib.looks_sensitive_env_name(name)

    def is_secret_env_name(self, name):
        return securitylib.is_secret_env_name(
            name, secret_env_names=self.secret_env_names
        )

    def configured_secret_env_items(self):
        return securitylib.configured_secret_env_items(
            secret_env_names=self.secret_env_names
        )

    def detected_secret_env_items(self):
        return securitylib.detected_secret_env_items(
            secret_env_names=self.secret_env_names
        )

    def secret_env_summary(self):
        return securitylib.secret_env_summary(secret_env_names=self.secret_env_names)

    def detected_secret_env_summary(self):
        return securitylib.detected_secret_env_summary(
            secret_env_names=self.secret_env_names
        )

    def redact_text(self, text):
        return securitylib.redact_text(text, secret_env_names=self.secret_env_names)

    def redact_artifact(self, value, key=None):
        return securitylib.redact_artifact(
            value, key=key, secret_env_names=self.secret_env_names
        )

    def shell_env(self):
        return securitylib.shell_env(allowlist=self.shell_env_allowlist, root=self.root)

    def prompt_metadata(self, user_message, prompt):
        _, metadata = self._build_prompt_and_metadata(user_message)
        return metadata

    def configure_context_budget(self, token_budget, segment_token_budgets=None):
        self.context_window_budget = ContextWindowBudget(
            context_window_tokens=self.context_window_budget.context_window_tokens,
            configured_input_tokens=int(token_budget),
            reserved_output_tokens=self.context_window_budget.reserved_output_tokens,
            window_source=self.context_window_budget.window_source,
        )
        self.context_manager.total_token_budget = (
            self.context_window_budget.effective_input_tokens
        )
        if segment_token_budgets is not None:
            self.context_manager.section_budgets = segment_token_budgets
        return self.context_window_budget.to_dict()

    def _build_prompt_and_metadata(
        self,
        user_message,
        *,
        include_history=True,
        history_override=None,
        segment_budget_overrides=None,
    ):
        refresh = self.refresh_prefix()
        self.skill_watcher.poll()
        self.active_skills = (
            self.skill_pool.search(user_message, top_k=3)
            if self.feature_enabled("skills")
            else ()
        )
        self.resume_state = self.evaluate_resume_state()
        prompt, metadata = self.context_manager.build(
            user_message,
            include_history=include_history,
            history_override=history_override,
            segment_budget_overrides=segment_budget_overrides,
        )
        admission = self.context_window_budget.admit(metadata["prompt_tokens"])
        # 这里把“这轮 prompt 是怎么拼出来的”连同缓存相关状态一起记下来，
        # 后面 trace/report 才能解释清楚：为什么这一轮 prefix 变了、缓存有没有命中。
        metadata.update(
            {
                "prefix_chars": len(self.prefix),
                "workspace_chars": len(self.workspace.text()),
                "memory_chars": len(self.memory_text()),
                "history_chars": len(self.history_text()),
                "request_chars": len(user_message),
                "context_window": admission.to_dict(),
                "context_window_tokens": admission.context_window_tokens,
                "context_window_source": self.context_window_budget.window_source,
                "configured_input_token_budget": admission.configured_input_tokens,
                "effective_input_token_budget": admission.effective_input_tokens,
                "reserved_output_tokens": admission.reserved_output_tokens,
                "request_admission_tokens": admission.total_reserved_tokens,
                "context_window_headroom_tokens": admission.headroom_tokens,
                "context_window_admitted": admission.admitted,
                "tool_count": len(self.tools),
                "workspace_docs": len(self.workspace.project_docs),
                "recent_commits": len(self.workspace.recent_commits),
                "prefix_hash": self.prefix_state.hash,
                "prompt_cache_key": self.prefix_state.hash,
                "workspace_fingerprint": self.prefix_state.workspace_fingerprint,
                "tool_signature": self.prefix_state.tool_signature,
                "workspace_changed": refresh["workspace_changed"],
                "prefix_changed": refresh["prefix_changed"],
                "prompt_cache_supported": bool(
                    getattr(self.model_client, "supports_prompt_cache", False)
                ),
                "resume_status": self.resume_state.get(
                    "status", CHECKPOINT_NONE_STATUS
                ),
                "stale_summary_invalidations": int(
                    self.resume_state.get("stale_summary_invalidations", 0)
                ),
                "stale_paths": list(self.resume_state.get("stale_paths", [])),
                "runtime_identity_mismatch_fields": list(
                    self.resume_state.get("runtime_identity_mismatch_fields", [])
                ),
            }
        )
        metadata.update(self.detected_secret_env_summary())
        return prompt, metadata

    def _apply_provider_message_metadata(
        self, metadata, *, projected_tokens, history_metadata
    ):
        metadata["prompt_text_tokens"] = int(metadata["prompt_tokens"])
        metadata["prompt_tokens"] = int(projected_tokens)
        metadata["provider_message_tokens"] = int(projected_tokens)
        metadata["provider_message_count"] = int(
            history_metadata.get("provider_message_count", 0)
        )
        metadata["history"].update(dict(history_metadata))
        metadata["sections"]["history"]["rendered_tokens"] = int(
            history_metadata.get("token_count", 0)
        )
        admission = self.context_window_budget.admit(int(projected_tokens))
        metadata.update(
            {
                "context_window": admission.to_dict(),
                "request_admission_tokens": admission.total_reserved_tokens,
                "context_window_headroom_tokens": admission.headroom_tokens,
                "context_window_admitted": admission.admitted,
            }
        )
        return metadata

    def emit_trace(self, task_state, event, payload=None):
        payload = self.redact_artifact(payload or {})
        if payload.get("semantic_event"):
            validate_semantic_event(payload["semantic_event"], payload)
        payload["event"] = event
        payload["created_at"] = now()
        context = current_trace_context()
        if context is not None:
            context = context.for_stage(infer_trace_stage(event))
        for key, value in trace_attributes(context).items():
            payload.setdefault(key, value)
        payload.setdefault("turn_id", task_state.run_id)
        payload.setdefault("request_id", task_state.task_id)
        payload.setdefault("session_id", self.session["id"])
        # trace 是运行中的逐事件时间线，适合回答“这一轮 agent 到底做了什么”。
        self.run_store.append_trace(task_state, payload)
        return payload

    def capture_workspace_snapshot(self):
        snapshot = {}
        for path in self.root.rglob("*"):
            try:
                relative_parts = path.relative_to(self.root).parts
            except ValueError:
                continue
            if any(part in IGNORED_PATH_NAMES for part in relative_parts):
                continue
            if not path.is_file():
                continue
            try:
                snapshot[path.relative_to(self.root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
            except Exception:
                continue
        return snapshot

    @staticmethod
    def diff_workspace_snapshots(before, after):
        changed_paths = []
        summaries = []
        all_paths = sorted(set(before) | set(after))
        for path in all_paths:
            if before.get(path) == after.get(path):
                continue
            changed_paths.append(path)
            if path not in before:
                summaries.append(f"created:{path}")
            elif path not in after:
                summaries.append(f"deleted:{path}")
            else:
                summaries.append(f"modified:{path}")
        return changed_paths, summaries

    def create_checkpoint(self, task_state, user_message, trigger):
        return checkpointlib.create_checkpoint(self, task_state, user_message, trigger)

    def snapshot_workspace(self, task_state, trigger):
        service = self.workspace_checkpoint_service
        if service is None:
            return None
        result = service.commit_turn(
            f"{task_state.run_id}: {str(trigger or 'turn finished')}"
        )
        task_state.workspace_checkpoint_status = result.status
        task_state.workspace_checkpoint_id = result.checkpoint_id
        task_state.edited_files = list(result.edited_files)
        return result

    def infer_next_step(self, task_state):
        return checkpointlib.infer_next_step(task_state)

    def update_memory_after_tool(self, name, args, result):
        """把少量高价值工具结果沉淀到 working memory。

        为什么存在：
        并不是每个工具结果都值得长期带进下一轮 prompt。完整结果已经进了
        `history`，这里只挑少量“下一轮大概率还会用到”的事实做提纯，
        例如最近读写过哪些文件、某个文件读出来的短摘要。

        输入 / 输出：
        - 输入：工具名 `name`、参数 `args`、执行结果 `result`
        - 输出：无显式返回值，副作用是更新 `self.memory`

        在 agent 链路里的位置：
        它发生在 `run_tool()` 真正执行完工具之后、下一轮 prompt 组装之前。
        也就是说：工具结果先进入完整历史，再由这个函数择优沉淀成轻量记忆。
        """
        if not self.feature_enabled("memory"):
            return
        path = args.get("path")
        if not path:
            return

        canonical_path = self.memory.canonical_path(path)
        # 不是所有工具结果都进入工作记忆。
        # 读文件会生成摘要；写文件/patch 会让旧摘要失效，因为它们可能过期了。
        if name in {"read_file", "write_file", "patch_file"}:
            self.memory.remember_file(canonical_path)
        if name == "read_file":
            summary = memorylib.summarize_read_result(result)
            self.memory.set_file_summary(canonical_path, summary)
            self.memory.append_note(
                summary, tags=(canonical_path,), source=canonical_path
            )
        elif name in {"write_file", "patch_file"}:
            self.memory.invalidate_file_summary(canonical_path)

    def note_tool(self, name, args, result):
        self.update_memory_after_tool(name, args, result)

    def record_process_note_for_tool(self, name, metadata):
        status = str(metadata.get("tool_status", "")).strip()
        if status not in {
            "partial_success",
            "error",
            "rejected",
            "cancelled",
            "timeout",
        }:
            return
        affected_paths = [
            str(path).strip()
            for path in metadata.get("affected_paths", [])
            if str(path).strip()
        ]
        path_text = ", ".join(affected_paths) or "workspace"
        if status == "partial_success":
            if metadata.get("tool_error_code") == "tool_output_truncated":
                text = f"{name} output truncated; narrow the request before retry"
            else:
                text = (
                    f"{name} partial_success on {path_text}; inspect diff before retry"
                )
        elif status == "error":
            text = f"{name} error on {path_text}; check the failure before retry"
        elif status == "cancelled":
            text = f"{name} cancelled on {path_text}; verify cleanup before retry"
        elif status == "timeout":
            text = (
                f"{name} timeout on {path_text}; inspect partial effects before retry"
            )
        else:
            text = f"{name} rejected; choose a different action before retry"
        tags = ["process", status, *affected_paths]
        self.memory.append_note(text, tags=tuple(tags), source=name, kind="process")
        self.session["memory"] = self.memory.to_dict()

    def reject_durable_reason(self, note_text):
        return self.memory_consolidator.reject_reason(note_text)

    def extract_durable_promotions(self, user_message, final_answer):
        result = self.memory_consolidator.consolidate(user_message, final_answer)
        self.last_memory_consolidation = result.to_dict()
        return (
            [(item.topic, item.text) for item in result.candidates],
            [f"{item.topic}:{item.reason}" for item in result.rejections],
        )

    def promote_durable_memory(self, user_message, final_answer):
        promotions, rejections = self.extract_durable_promotions(
            user_message, final_answer
        )
        promoted, superseded = self.memory.promote_durable(promotions)
        self.session["memory"] = self.memory.to_dict()
        self.last_durable_promotions = promoted
        self.last_durable_rejections = rejections
        self.last_durable_superseded = superseded
        return promoted, rejections, superseded

    def ask(self, user_message):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._ask_and_close(user_message))
        raise RuntimeError(
            "RepoAgent.ask() cannot run inside an active event loop; use await ask_async()"
        )

    async def _ask_and_close(self, user_message):
        try:
            return await self.ask_async(user_message)
        finally:
            await self.aclose()

    async def ask_async(self, user_message):
        from .agent_turn_runner import AgentTurnRunner
        from .spine import Scheduler, Text, TurnRequest, TurnRuntime, TurnState

        if not self._memory_backend_started:
            await self.memory_backend.start()
            self._memory_backend_started = True
            self.skill_watcher.start()
        if self._turn_runtime is None:
            self._turn_runtime = TurnRuntime(
                AgentTurnRunner(self), self.run_store, redactor=self.redact_artifact
            )
        loop = asyncio.get_running_loop()
        if self._scheduler is None:
            self._scheduler = Scheduler(
                self._turn_runtime,
                foreground_capacity=1,
                background_capacity=1,
            )
            self._scheduler_loop = loop
        elif self._scheduler_loop is not loop:
            raise RuntimeError(
                "RepoAgent async runtime is bound to another event loop; call aclose() before reusing it"
            )
        request = TurnRequest.create(
            session_id=self.session["id"],
            text=user_message,
        )
        handle = self._scheduler.submit(request)
        try:
            outcome = await handle.result()
        except asyncio.CancelledError:
            handle.cancel()
            try:
                await handle.result()
            except Exception:
                pass
            raise
        if outcome.state is TurnState.FAILED:
            raise RuntimeError(outcome.error or "Turn failed")
        if outcome.state is TurnState.CANCELLED:
            raise asyncio.CancelledError(outcome.error or "Turn cancelled")
        answers = [event.content for event in handle.events if isinstance(event, Text)]
        return "".join(answers) if answers else outcome.final_answer

    async def aclose(self, grace=5.0):
        try:
            if self._scheduler is not None:
                if self._scheduler_loop is not asyncio.get_running_loop():
                    raise RuntimeError(
                        "RepoAgent must be closed from its scheduler event loop"
                    )
                await self._scheduler.shutdown(grace=grace)
        finally:
            self._scheduler = None
            self._scheduler_loop = None
            if self._memory_backend_started:
                await self.memory_backend.stop()
                self._memory_backend_started = False
            self.skill_watcher.stop()
            if self.plugin_manager is not None:
                self.plugin_manager.stop()

    def next_tool_call_id(self):
        self._tool_call_sequence += 1
        state = self.current_task_state
        scope = state.task_id if state is not None else self.session["id"]
        return f"{scope}:tool:{self._tool_call_sequence}"

    def build_tool_request(
        self,
        name,
        args,
        *,
        call_id="",
        origin="internal",
        parent_call_id="",
        timeout_seconds=None,
        max_output_chars=None,
    ):
        state = self.current_task_state
        return ToolRequest(
            call_id=call_id or self.next_tool_call_id(),
            name=str(name),
            arguments=args,
            turn_id=state.run_id if state is not None else "",
            request_id=state.task_id if state is not None else "",
            session_id=self.session["id"],
            origin=origin,
            parent_call_id=parent_call_id,
            capability_token=self.capability_token,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )

    def capability_scope(self):
        claims = self.capability_authority.verify(self.capability_token)
        if claims is None:
            return {
                "token_digest": capability_token_digest(self.capability_token),
                "effects": [],
                "tools": [],
                "valid": False,
            }
        return {
            **claims.scope(),
            "token_digest": capability_token_digest(self.capability_token),
            "valid": True,
        }

    def execute_tool_request(self, request, *, cancellation_token=None):
        result = self.tool_gateway.execute(
            request, cancellation_token=cancellation_token
        )
        self._last_tool_result_metadata = compatibility_metadata(result)
        return result

    def execute_tool_batch(self, requests, *, cancellation_token=None):
        results = self.tool_gateway.execute_batch(
            requests, cancellation_token=cancellation_token
        )
        if results:
            self._last_tool_result_metadata = compatibility_metadata(results[-1])
        return results

    def execute_tool(
        self,
        name,
        args,
        *,
        call_id="",
        origin="internal",
        parent_call_id="",
        timeout_seconds=None,
        max_output_chars=None,
    ):
        request = self.build_tool_request(
            name,
            args,
            call_id=call_id,
            origin=origin,
            parent_call_id=parent_call_id,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )
        return self.execute_tool_request(request)

    def run_tool(self, name, args):
        """执行一次工具调用，并在执行前后套上完整护栏。

        为什么存在：
        在 agent 系统里，真正危险的不是“模型会不会想调用工具”，而是
        “平台有没有在执行前把边界守住”。这个函数就是工具层的总闸口：
        所有工具调用都必须先经过它，不能让模型直接碰到底层函数。

        输入 / 输出：
        - 输入：工具名 `name`，参数字典 `args`
        - 输出：字符串结果。无论是成功结果还是错误信息，都会统一返回文本，
          这样模型下一轮都能继续消费这份反馈。

        在 agent 链路里的位置：
        它位于 `ask()` 的“模型决定要调用工具”之后，是控制循环里真正把模型
        意图落到外部世界的一步。因此这里串起了几乎所有安全与可控设计：
        工具是否存在、参数是否合法、是否重复、是否需要审批、执行结果是否裁剪、
        是否需要回写记忆。
        """
        return self.execute_tool(name, args).content

    def repeated_tool_call(self, name, args):
        # agent 很常见的一种坏循环，是在没有新信息的情况下反复发起同一调用。
        # 这里提前挡掉最简单的这种循环。
        tool_events = [
            item for item in self.session["history"] if item["role"] == "tool"
        ]
        if len(tool_events) < 2:
            return False
        recent = tool_events[-2:]
        try:
            normalized_args = toolkit.normalize_tool_arguments(name, args)
        except ValueError:
            normalized_args = args

        def same_call(item):
            if item["name"] != name:
                return False
            try:
                historical_args = toolkit.normalize_tool_arguments(name, item["args"])
            except ValueError:
                historical_args = item["args"]
            return historical_args == normalized_args

        return all(same_call(item) for item in recent)

    @staticmethod
    def new_task_id():
        return (
            "task_"
            + datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex[:6]
        )

    @staticmethod
    def new_run_id():
        return (
            "run_"
            + datetime.now().strftime("%Y%m%d-%H%M%S")
            + "-"
            + uuid.uuid4().hex[:6]
        )

    def build_report(self, task_state):
        # report 是一次运行的最终摘要；
        # 和 trace 的区别在于，trace 关注过程，report 关注结果与关键指标。
        provider_calls = self.run_store.load_model_calls(task_state)
        return {
            "run_id": task_state.run_id,
            "task_id": task_state.task_id,
            "status": task_state.status,
            "stop_reason": task_state.stop_reason,
            "final_answer": task_state.final_answer,
            "tool_steps": task_state.tool_steps,
            "attempts": task_state.attempts,
            "checkpoint_id": task_state.checkpoint_id,
            "resume_status": task_state.resume_status,
            "workspace_checkpoint": {
                "status": task_state.workspace_checkpoint_status,
                "checkpoint_id": task_state.workspace_checkpoint_id,
                "edited_files": list(task_state.edited_files),
            },
            "task_state": task_state.to_dict(),
            "prompt_metadata": self.last_prompt_metadata,
            "usage": {
                key: self.last_completion_metadata.get(key)
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "cache_read_tokens",
                    "cache_write_tokens",
                    "usage_source",
                    "input_token_semantics",
                    "model_call_count",
                    "usage_source_counts",
                    "usage_complete",
                )
            },
            "call_efficiency": dict(self.last_call_efficiency_summary),
            "provider_call_ids": [
                row.get("provider_call_id", "") for row in provider_calls
            ],
            "durable_promotions": list(self.last_durable_promotions),
            "durable_rejections": list(self.last_durable_rejections),
            "durable_superseded": list(self.last_durable_superseded),
            "memory_backend": dict(self.last_memory_backend_metadata),
            "memory_consolidation": dict(self.last_memory_consolidation),
            "subagents": list(self.last_subagent_records),
            "subagent_summary": {
                "count": len(self.last_subagent_records),
                "completed": sum(
                    row.get("outcome", {}).get("state") == "completed"
                    for row in self.last_subagent_records
                ),
                "failed": sum(
                    row.get("outcome", {}).get("state") == "failed"
                    for row in self.last_subagent_records
                ),
                "cancelled": sum(
                    row.get("outcome", {}).get("state") == "cancelled"
                    for row in self.last_subagent_records
                ),
                "partial_estimated_cost_usd": sum(
                    float(
                        row.get("outcome", {})
                        .get("call_efficiency", {})
                        .get("partial_estimated_cost_usd", 0.0)
                        or 0.0
                    )
                    for row in self.last_subagent_records
                ),
            },
            "redacted_env": self.detected_secret_env_summary(),
            "plugins": (
                self.plugin_manager.report() if self.plugin_manager is not None else []
            ),
        }

    def tool_example(self, name):
        return toolkit.tool_example(name)

    def validate_tool(self, name, args):
        """把通用工具校验和 runtime 级额外约束串起来。"""
        if name not in toolkit.BASE_TOOL_DEFINITIONS and name != "delegate":
            entry = self.tools.get(name)
            if entry is None:
                raise ValueError(f"unknown tool: {name}")
            return validate_tool_arguments(entry["definition"], args)
        return toolkit.validate_tool(self.tool_context(), name, args)

    def tool_context(self):
        return ToolContext(
            root=self.root,
            path_resolver=self.path,
            shell_env_provider=self.shell_env,
            depth=self.depth,
            max_depth=self.max_depth,
            spawn_delegate=self.spawn_delegate,
            sandbox_adapter=self.sandbox_adapter,
        )

    def spawn_delegate(self, args, control=None):
        task = str(args.get("task", "")).strip()
        role = str(args.get("role", "reviewer"))
        profile = ROLE_PROFILES[role]
        child_allowed_tools = tuple(
            name
            for name in profile["tools"]
            if name in self.tools and name != "delegate"
        )
        if not child_allowed_tools:
            return "delegate_result:\nerror: no delegated read capabilities available"
        state = self.current_task_state
        remaining_steps = max(1, self.max_steps - int(state.tool_steps if state else 0))
        parent_timeout = (
            max(0.001, control.deadline - time.monotonic())
            if control is not None and control.deadline is not None
            else 60.0
        )
        parent_budget = SubagentBudget(
            max_steps=remaining_steps,
            max_input_tokens=self.context_window_budget.effective_input_tokens,
            max_output_tokens=self.max_new_tokens,
            timeout_seconds=parent_timeout,
        )
        budget = SubagentBudget(
            max_steps=int(args.get("max_steps", 3)),
            max_input_tokens=self.context_window_budget.effective_input_tokens,
            max_output_tokens=self.max_new_tokens,
            timeout_seconds=parent_timeout,
        ).attenuate(parent_budget)
        request = SubagentRequest.create(
            parent_turn_id=state.run_id if state else "",
            parent_request_id=state.task_id if state else "",
            parent_session_id=self.session["id"],
            task=task,
            role=role,
            budget=budget,
            allowed_tools=child_allowed_tools,
            messages=(
                SubagentMessage(
                    sender_id=state.run_id if state else self.session["id"],
                    recipient_id="pending-subagent",
                    kind="request",
                    content=self.redact_text(task),
                    sequence=1,
                ),
            ),
        )
        request = replace(
            request,
            messages=(replace(request.messages[0], recipient_id=request.subagent_id),),
        )
        if state is not None:
            self.emit_trace(
                state,
                "subagent_started",
                {
                    "subagent_id": request.subagent_id,
                    "role": role,
                    "budget": budget.to_dict(),
                },
            )
        child = None
        outcome = None
        with IsolatedSubagentWorkspace(self.root, request.subagent_id) as isolated:
            child_workspace = WorkspaceContext.build(isolated.root)
            child = RepoAgent(
                model_client=self.model_client,
                workspace=child_workspace,
                session_store=SessionStore(isolated.root / ".repoagent" / "sessions"),
                run_store=RunStore(isolated.root / ".repoagent" / "runs"),
                approval_policy=("auto" if role == "implementer" else "never"),
                max_steps=budget.max_steps,
                max_parallel_tools=self.max_parallel_tools,
                mutation_conflict_policy=self.mutation_conflict_policy,
                max_new_tokens=budget.max_output_tokens,
                depth=self.depth + 1,
                max_depth=self.max_depth,
                read_only=bool(profile["read_only"]),
                secret_env_names=self.secret_env_names,
                shell_env_allowlist=self.shell_env_allowlist,
                allowed_tools=child_allowed_tools,
                capability_authority=self.capability_authority,
                parent_capability_token=self.capability_token,
                mcp_servers=None,
                sandbox_adapter=self.sandbox_adapter,
                require_isolation=self.require_isolation,
                network_policy=self.network_policy,
                context_token_budget=budget.max_input_tokens,
                segment_token_budgets=self.context_manager.segment_token_budgets,
                token_counter=self.context_manager.token_counter,
                context_window_tokens=self.context_window_budget.context_window_tokens,
                context_window_source=self.context_window_budget.window_source,
                skill_roots={
                    "workspace": isolated.root / "skills",
                    "local": isolated.root / ".repoagent" / "skills",
                },
            )
            child.memory.set_task_summary(task)
            delegated_task = f"Role: {role}\n{profile['instruction']}\n\nTask: {task}"
            try:
                if control is None:
                    answer = child.ask(delegated_task)
                else:
                    from .agent_loop import AgentLoop

                    answer = AgentLoop(child).run(
                        delegated_task,
                        cancellation_token=control.cancellation_token,
                        deadline=control.deadline,
                    )
                outcome = SubagentOutcome(
                    subagent_id=request.subagent_id,
                    state="completed",
                    answer=self.redact_text(answer),
                    usage=child.last_completion_metadata,
                    call_efficiency=child.last_call_efficiency_summary,
                    tool_calls=(
                        int(child.current_task_state.tool_steps)
                        if child.current_task_state is not None
                        else 0
                    ),
                )
            except Exception as exc:
                outcome = SubagentOutcome(
                    subagent_id=request.subagent_id,
                    state=(
                        "cancelled"
                        if isinstance(exc, ProviderCancelledError)
                        else "failed"
                    ),
                    error=f"{type(exc).__name__}: {exc}",
                    usage=child.last_completion_metadata,
                    call_efficiency=child.last_call_efficiency_summary,
                    tool_calls=(
                        int(child.current_task_state.tool_steps)
                        if child.current_task_state is not None
                        else 0
                    ),
                )
            if child.current_run_dir is not None and self.current_run_dir is not None:
                evidence = persist_subagent_evidence(
                    self.current_run_dir, request, outcome, child.current_run_dir
                )
                outcome = replace(outcome, evidence=evidence)
        record = {"request": request.to_dict(), "outcome": outcome.to_dict()}
        self.last_subagent_records.append(self.redact_artifact(record))
        if state is not None:
            self.emit_trace(
                state,
                "subagent_completed",
                {
                    "subagent_id": request.subagent_id,
                    "state": outcome.state,
                    "role": role,
                    "usage": dict(outcome.usage),
                    "call_efficiency": dict(outcome.call_efficiency),
                    "evidence": dict(outcome.evidence),
                },
            )
        if outcome.state != "completed":
            raise RuntimeError(outcome.error)
        return "delegate_result:\n" + outcome.answer

    def tool_list_files(self, args):
        return toolkit.tool_list_files(self.tool_context(), args)

    def tool_read_file(self, args):
        return toolkit.tool_read_file(self.tool_context(), args)

    def tool_search(self, args):
        return toolkit.tool_search(self.tool_context(), args)

    def tool_run_shell(self, args):
        return toolkit.tool_run_shell(self.tool_context(), args)

    def tool_write_file(self, args):
        return toolkit.tool_write_file(self.tool_context(), args)

    def tool_patch_file(self, args):
        return toolkit.tool_patch_file(self.tool_context(), args)

    def tool_delegate(self, args):
        return toolkit.tool_delegate(self.tool_context(), args)

    @staticmethod
    def _prompt_for_approval(definition, request, arguments):
        try:
            answer = input(
                "approve "
                f"{definition.name} [{definition.effect.value}] "
                f"{json.dumps(dict(arguments), ensure_ascii=True)}? [y/N] "
            )
        except EOFError:
            return False
        return answer.strip().lower() in {"y", "yes"}

    def approve(self, name, args):
        """Compatibility helper; Gateway uses the structured approval engine."""
        entry = self.tools.get(name)
        if entry is None:
            return False
        request = self.build_tool_request(name, args)
        return self.approval_engine.decide(entry["definition"], request, args).allowed

    @staticmethod
    def parse(raw):
        """把模型原始输出解析成 runtime 可执行的动作或最终答案。

        为什么存在：
        模型输出首先是自然语言文本，而 runtime 需要的是结构化决策：
        “这是工具调用”还是“这是最终答案”。如果没有这层解析，后面的工具校验、
        审批和执行链路就没法可靠工作。

        输入 / 输出：
        - 输入：模型返回的原始文本 `raw`
        - 输出：`(kind, payload)`，其中 `kind` 可能是 `tool`、`final`、`retry`

        在 agent 链路里的位置：
        它位于 `model_client.complete()` 之后、`run_tool()` 之前，是模型输出
        进入平台控制流的第一道结构化关口。
        """
        raw = str(raw)
        # 这里支持两种工具格式：
        # 1. <tool>...</tool> 里包 JSON，适合简短调用
        # 2. XML 风格属性/子标签，适合写文件这类多行内容
        if "<tool>" in raw and (
            "<final>" not in raw or raw.find("<tool>") < raw.find("<final>")
        ):
            body = RepoAgent.extract(raw, "tool")
            try:
                payload = json.loads(body)
            except Exception:
                return "retry", RepoAgent.retry_notice(
                    "model returned malformed tool JSON"
                )
            if not isinstance(payload, dict):
                return "retry", RepoAgent.retry_notice(
                    "tool payload must be a JSON object"
                )
            if not str(payload.get("name", "")).strip():
                return "retry", RepoAgent.retry_notice(
                    "tool payload is missing a tool name"
                )
            args = payload.get("args", {})
            if args is None:
                payload["args"] = {}
            elif not isinstance(args, dict):
                return "retry", RepoAgent.retry_notice()
            return "tool", payload
        if "<tool" in raw and (
            "<final>" not in raw or raw.find("<tool") < raw.find("<final>")
        ):
            payload = RepoAgent.parse_xml_tool(raw)
            if payload is not None:
                return "tool", payload
            return "retry", RepoAgent.retry_notice()
        if "<final>" in raw:
            final = RepoAgent.extract(raw, "final").strip()
            if final:
                return "final", final
            return "retry", RepoAgent.retry_notice(
                "model returned an empty <final> answer"
            )
        raw = raw.strip()
        if raw:
            return "final", raw
        return "retry", RepoAgent.retry_notice("model returned an empty response")

    @staticmethod
    def retry_notice(problem=None):
        prefix = "Runtime notice"
        if problem:
            prefix += f": {problem}"
        else:
            prefix += ": model returned malformed tool output"
        return (
            f"{prefix}. Reply with a valid <tool> call or a non-empty <final> answer. "
            'For multi-line files, prefer <tool name="write_file" path="file.py"><content>...</content></tool>.'
        )

    @staticmethod
    def parse_xml_tool(raw):
        match = re.search(r"<tool(?P<attrs>[^>]*)>(?P<body>.*?)</tool>", raw, re.S)
        if not match:
            return None
        attrs = RepoAgent.parse_attrs(match.group("attrs"))
        name = str(attrs.pop("name", "")).strip()
        if not name:
            return None

        body = match.group("body")
        args = dict(attrs)
        for key in (
            "content",
            "old_text",
            "new_text",
            "command",
            "task",
            "pattern",
            "path",
        ):
            if f"<{key}>" in body:
                args[key] = RepoAgent.extract_raw(body, key)

        body_text = body.strip("\n")
        if name == "write_file" and "content" not in args and body_text:
            args["content"] = body_text
        if name == "delegate" and "task" not in args and body_text:
            args["task"] = body_text.strip()
        return {"name": name, "args": args}

    @staticmethod
    def parse_attrs(text):
        attrs = {}
        for match in re.finditer(
            r"""([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""", text
        ):
            attrs[match.group(1)] = (
                match.group(2) if match.group(2) is not None else match.group(3)
            )
        return attrs

    @staticmethod
    def extract(text, tag):
        start_tag = f"<{tag}>"
        end_tag = f"</{tag}>"
        start = text.find(start_tag)
        if start == -1:
            return text
        start += len(start_tag)
        end = text.find(end_tag, start)
        if end == -1:
            return text[start:].strip()
        return text[start:end].strip()

    @staticmethod
    def extract_raw(text, tag):
        start_tag = f"<{tag}>"
        end_tag = f"</{tag}>"
        start = text.find(start_tag)
        if start == -1:
            return text
        start += len(start_tag)
        end = text.find(end_tag, start)
        if end == -1:
            return text[start:]
        return text[start:end]

    def reset(self):
        if self._memory_backend_started:
            raise RuntimeError("close the memory backend before resetting the agent")
        default_backend = self.memory_backend is self.memory
        self.session["history"] = []
        self.session["memory"].clear()
        self.session["memory"].update(memorylib.default_memory_state())
        self.memory = memorylib.LayeredMemory(
            self.session["memory"], workspace_root=self.root
        )
        if default_backend:
            self.memory_backend = self.memory
            self._memory_backend_started = False
            self.last_memory_backend_metadata["backend"] = type(self.memory).__name__
        self.backend_memory_hits = []
        self.session_store.save(self.session)

    def path(self, raw_path):
        path = Path(raw_path)
        path = path if path.is_absolute() else self.root / path
        resolved = path.resolve()
        # 所有文件类工具都被锚定在 workspace root 之下。
        # 这样既能防住 "../" 逃逸，也能防住符号链接解析后跳出仓库。
        if os.path.commonpath([str(self.root), str(resolved)]) != str(self.root):
            raise ValueError(f"path escapes workspace: {raw_path}")
        return resolved
