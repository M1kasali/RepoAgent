"""Adapter for evaluating the real pico-harness runtime on Polyglot tasks."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from ..call_efficiency import CallEfficiencyEntry, CallEfficiencySummary
from ..providers.base import (
    ModelMessage,
    ModelRequest,
    ModelTool,
    ModelUsage,
    ModelUsageAggregate,
    ProviderError,
    ToolCall,
    generate_model,
)
from ..sandbox import DockerSandboxAdapter
from ..task_state import STOP_REASON_FINAL_ANSWER_RETURNED
from ..tokenization import resolve_token_counter
from ..tool_execution import ToolExecutionControl


PICO_BASELINE_EVIDENCE_SCHEMA = "repoagent.pico-baseline-runtime/v1"
PICO_BASELINE_VARIANT = "pico-harness"
PICO_CODING_TOOLS = frozenset(
    {
        "edit_file",
        "exec",
        "find",
        "grep",
        "list_dir",
        "read_file",
        "write_file",
    }
)
PICO_DISABLED_TOOLS = (
    "ask_user",
    "message",
    "skill_read",
    "spawn",
    "web_fetch",
    "web_search",
)


class PicoBaselineRuntimeError(RuntimeError):
    """Raised when the baseline host fails before producing a valid final turn."""


def _detach_pico_call_efficiency(assembly):
    """Keep pico's duplicate observer off its thread-based shutdown path."""

    controller = assembly.call_efficiency
    assembly.call_efficiency = None
    return controller


def _stop_pico_skill_watcher(assembly) -> None:
    """Close the short-lived runtime's catalog watcher if pico left it running."""

    context = getattr(assembly.agent_loop, "context", None)
    skills = getattr(context, "skills", None)
    stop_watcher = getattr(skills, "stop_file_watcher", None)
    if callable(stop_watcher):
        stop_watcher()


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        text = content.get("text")
        return str(text) if text is not None else json.dumps(dict(content), sort_keys=True)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, Mapping) and item.get("text") is not None:
                parts.append(str(item["text"]))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content)


def _tool_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {"_raw_arguments": value}
        if isinstance(decoded, Mapping):
            return dict(decoded)
    return {"_raw_arguments": value}


def pico_messages_to_model_messages(messages) -> tuple[ModelMessage, ...]:
    """Project pico's OpenAI-style history into RepoAgent's typed provider contract."""

    projected = []
    for message_index, raw in enumerate(messages or ()):
        role = str(raw.get("role", ""))
        if role not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"unsupported pico message role: {role!r}")
        if role == "assistant":
            calls = []
            for call_index, raw_call in enumerate(raw.get("tool_calls") or ()):
                function = raw_call.get("function") or {}
                calls.append(
                    ToolCall(
                        id=str(raw_call.get("id") or f"pico-{message_index}-{call_index}"),
                        name=str(function.get("name") or raw_call.get("name") or ""),
                        arguments=_tool_arguments(
                            function.get("arguments", raw_call.get("arguments", {}))
                        ),
                    )
                )
            thinking = tuple(
                dict(block)
                for block in (raw.get("thinking_blocks") or ())
                if isinstance(block, Mapping)
            )
            projected.append(
                ModelMessage(
                    role=role,
                    content=_content_text(raw.get("content")),
                    tool_calls=tuple(calls),
                    reasoning_content=str(raw.get("reasoning_content") or ""),
                    thinking_blocks=thinking,
                )
            )
            continue
        if role == "tool":
            projected.append(
                ModelMessage(
                    role=role,
                    content=_content_text(raw.get("content")) or "(empty)",
                    tool_call_id=str(raw.get("tool_call_id") or ""),
                    name=str(raw.get("name") or ""),
                )
            )
            continue
        projected.append(ModelMessage(role=role, content=_content_text(raw.get("content"))))
    return tuple(projected)


def pico_tools_to_model_tools(tools) -> tuple[ModelTool, ...]:
    """Project pico's function schemas without changing their names or parameters."""

    projected = []
    for raw in tools or ():
        function = raw.get("function") if isinstance(raw, Mapping) else None
        definition = function if isinstance(function, Mapping) else raw
        if not isinstance(definition, Mapping):
            raise TypeError("pico tool definitions must be mappings")
        projected.append(
            ModelTool(
                name=str(definition.get("name") or ""),
                description=str(definition.get("description") or ""),
                parameters=dict(definition.get("parameters") or {}),
            )
        )
    return tuple(projected)


class RepoAgentProviderBridge:
    """Expose one RepoAgent provider through pico's asynchronous provider shape."""

    def __init__(self, client, *, max_calls: int, max_output_tokens: int):
        if max_calls < 1 or max_output_tokens < 1:
            raise ValueError("pico provider call and output budgets must be positive")
        self.client = client
        self.profile = getattr(client, "profile", None)
        self.max_calls = int(max_calls)
        self.max_output_tokens = int(max_output_tokens)
        self.generation = SimpleNamespace(
            temperature=getattr(self.profile, "temperature", None),
            max_tokens=self.max_output_tokens,
            reasoning_effort=None,
        )
        self.entries: list[CallEfficiencyEntry] = []
        self.usages: list[ModelUsage] = []
        self.budget_exhausted = False

    def get_default_model(self) -> str:
        return str(
            getattr(self.profile, "model", "")
            or getattr(self.client, "model", "")
        )

    def supports_explicit_cache_control(self, _model: str) -> bool:
        return False

    def classify_error(self, error=None, *, content=None):
        from pico.providers.base import ErrorClassification

        if isinstance(error, ProviderError):
            return ErrorClassification(
                category=error.category,
                retryable=error.retryable,
                should_fallback=error.should_fallback,
                should_compress=error.should_compress,
            )
        text = str(content or error or "").lower()
        return ErrorClassification(
            category="context_overflow" if "context" in text and "length" in text else "unknown"
        )

    async def chat_with_retry(
        self,
        messages,
        tools=None,
        model=None,
        *,
        fallback_models=None,
        request_transform=None,
        response_observer=None,
        attempt_started=None,
        **kwargs,
    ):
        del fallback_models
        attempted_model = model or self.get_default_model()
        if request_transform is not None:
            messages, tools, attempted_model = request_transform(
                messages,
                tools,
                attempted_model,
            )
        if attempt_started is not None:
            attempt_started(attempted_model)
        response = await self.chat(
            messages=messages,
            tools=tools,
            model=attempted_model,
            **kwargs,
        )
        if response_observer is not None:
            await response_observer(response, attempted_model)
        return response

    async def chat(self, messages, tools=None, model=None, **_kwargs):
        from pico.providers.base import ErrorClassification, LLMResponse, ToolCallRequest

        if len(self.entries) >= self.max_calls:
            self.budget_exhausted = True
            return LLMResponse(
                content="Provider call budget exhausted.",
                finish_reason="error",
                error_classification=ErrorClassification("budget_exhausted"),
                model=model or self.get_default_model(),
            )

        call_index = len(self.entries) + 1
        request = ModelRequest(
            prompt="pico-harness agent turn",
            max_output_tokens=self.max_output_tokens,
            timeout_seconds=float(getattr(self.profile, "timeout_seconds", 120.0)),
            turn_id="pico-polyglot-turn",
            session_id="pico-polyglot-session",
            request_id=f"pico-request-{call_index}",
            attempt=1,
            tools=pico_tools_to_model_tools(tools),
            messages=pico_messages_to_model_messages(messages),
        )
        started = time.monotonic()
        try:
            # The baseline host runs one turn at a time. Keep the synchronous
            # transport on the event-loop thread so its response-close callbacks
            # remain in the same lifecycle as pico's scheduler.
            result = generate_model(self.client, request)
        except BaseException as exc:
            status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            category = exc.category if isinstance(exc, ProviderError) else type(exc).__name__
            self.entries.append(
                self._entry(
                    call_index,
                    status=status,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    usage=ModelUsage(),
                    finish_reason="error",
                    error_category=str(category),
                )
            )
            raise

        self.usages.append(result.usage)
        self.entries.append(
            self._entry(
                call_index,
                status="completed",
                duration_ms=result.latency_ms
                or int((time.monotonic() - started) * 1000),
                usage=result.usage,
                finish_reason=result.finish_reason,
            )
        )
        return LLMResponse(
            content=result.text or None,
            tool_calls=[
                ToolCallRequest(
                    id=call.id,
                    name=call.name,
                    arguments=dict(call.arguments),
                )
                for call in result.tool_calls
            ],
            finish_reason=result.finish_reason,
            usage={
                "prompt_tokens": result.usage.input_tokens,
                "completion_tokens": result.usage.output_tokens,
                "total_tokens": result.usage.total_tokens,
                "cache_read_tokens": result.usage.cache_read_tokens,
                "cache_write_tokens": result.usage.cache_write_tokens,
            },
            reasoning_content=result.reasoning_content or None,
            thinking_blocks=[dict(block) for block in result.thinking_blocks] or None,
            model=result.model or model or self.get_default_model(),
        )

    def _entry(
        self,
        call_index,
        *,
        status,
        duration_ms,
        usage,
        finish_reason,
        error_category="",
    ):
        return CallEfficiencyEntry(
            provider_call_id=f"pico-{uuid.uuid4().hex}",
            turn_id="pico-polyglot-turn",
            request_id=f"pico-request-{call_index}",
            session_id="pico-polyglot-session",
            agent_attempt=1,
            provider_attempt=0,
            provider=str(getattr(self.profile, "provider", type(self.client).__name__)),
            model=self.get_default_model(),
            status=status,
            duration_ms=max(0, int(duration_ms)),
            usage=usage,
            pricing=getattr(self.profile, "pricing", None),
            finish_reason=str(finish_reason),
            error_category=str(error_category),
        )


class PicoDockerExecutor:
    """Adapt RepoAgent's disposable Docker sandbox to pico's executor contract."""

    def __init__(self, adapter: DockerSandboxAdapter):
        self.adapter = adapter

    @property
    def is_sandboxed(self) -> bool:
        return True

    @property
    def supports_process_spawning(self) -> bool:
        return False

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb):
        await self.stop()

    async def exec(self, command, cwd=None, timeout=None, env=None):
        from pico.sandbox import ExecResult

        effective_timeout = float(timeout or 60)
        control = ToolExecutionControl(
            timeout_seconds=effective_timeout,
            max_output_chars=10_000,
        )
        outcome = self.adapter.execute(
            str(command),
            cwd=cwd or self.adapter.workspace,
            env=dict(env or {}),
            control=control,
        )
        exit_code = outcome.exit_code
        if exit_code is None:
            exit_code = 124 if outcome.status == "timeout" else 130 if outcome.status == "cancelled" else 1
        stderr = outcome.stderr
        if outcome.status != "completed":
            suffix = f"execution_status: {outcome.status}"
            stderr = f"{stderr.rstrip()}\n{suffix}".strip()
        return ExecResult(
            stdout=outcome.stdout,
            stderr=stderr,
            exit_code=int(exit_code),
        )


class PicoHarnessAgent:
    """Synchronous campaign adapter around pico-harness's real runtime host."""

    def __init__(
        self,
        *,
        workspace,
        model_client,
        max_provider_calls: int,
        max_output_tokens: int,
        context_token_budget: int,
        context_window_tokens: int,
        context_window_source: str,
        docker_executable: str,
        docker_image: str,
        docker_memory: str,
        docker_cpus: float,
        docker_pids_limit: int,
        docker_workspace_path_converter=None,
        adapter_source: Mapping[str, Any] | None = None,
    ):
        self.workspace = Path(workspace).resolve()
        self.model_client = model_client
        self.max_steps = int(max_provider_calls)
        self.max_provider_calls = int(max_provider_calls)
        self.max_new_tokens = int(max_output_tokens)
        self.context_manager = SimpleNamespace(
            total_token_budget=int(context_token_budget),
            token_counter=resolve_token_counter(model_client),
        )
        self.context_window_budget = SimpleNamespace(
            context_window_tokens=int(context_window_tokens),
            window_source=str(context_window_source),
        )
        self.sandbox_adapter = DockerSandboxAdapter(
            self.workspace,
            executable=docker_executable,
            image=docker_image,
            memory=docker_memory,
            cpus=docker_cpus,
            pids_limit=docker_pids_limit,
            workspace_path_converter=docker_workspace_path_converter,
        )
        self.sandbox_adapter.verify_available()
        self.provider = RepoAgentProviderBridge(
            model_client,
            max_calls=self.max_provider_calls,
            max_output_tokens=self.max_new_tokens,
        )
        self.state_dir = self.workspace.parent / f"{self.workspace.name}-pico-state"
        self.current_task_state = None
        self.adapter_source = dict(adapter_source or {})
        self.evaluation_report: dict[str, Any] = {}
        self.evaluation_evidence: dict[str, Any] = {}

    def tool_signature(self) -> str:
        payload = json.dumps(
            {
                "active_tools": sorted(PICO_CODING_TOOLS),
                "disabled_tools": list(PICO_DISABLED_TOOLS),
                "sandbox": self.sandbox_adapter.identity,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    def ask(self, prompt: str) -> str:
        return asyncio.run(self._ask(prompt))

    async def _ask(self, prompt: str) -> str:
        from benchmarks.picobench.host import RecordingOutlet, RuntimeTrialHost
        from pico.cli._runtime_assembly import assemble_runtime
        from pico.config.paths import RuntimePaths
        from pico.config.pico import PicoConfig
        from pico.config.schema import Config
        from pico.spine import ChatType, Origin, Source, TurnRequest
        from pico.spine.events import Text

        self.state_dir.mkdir(parents=True, exist_ok=True)
        # Pico reserves output inside context_window_tokens. Adding the shared output
        # ceiling makes its effective prompt ceiling equal context_token_budget.
        assembly_window = self.context_manager.total_token_budget + self.max_new_tokens
        config = Config(
            agents={
                "defaults": {
                    "workspace": str(self.workspace),
                    "model": str(getattr(self.model_client.profile, "model", "")),
                    "max_tokens": self.max_new_tokens,
                    "context_window_tokens": assembly_window,
                    "temperature": getattr(self.model_client.profile, "temperature", 0.2),
                    "max_tool_iterations": self.max_provider_calls,
                }
            },
            tools={
                "restrict_to_workspace": True,
                "exec": {"timeout": 120},
                "sandbox": {"backend": "none"},
                "disabled_tools": list(PICO_DISABLED_TOOLS),
            },
        )
        pico_config = PicoConfig(
            call_efficiency={
                "mode": "off",
                "enabled": False,
                "usage_tracking": False,
            },
            memory={"backend": None},
            plugins={"disabled": []},
            skill_forge={"router": {"enabled": False}},
            runtime={"checkpoint": {"policy": "never"}},
        )
        assembly = assemble_runtime(
            config,
            pico_config,
            provider=self.provider,
            cron_service=None,
            interactive=False,
            paths=RuntimePaths(workspace=self.workspace, state=self.state_dir),
        )
        pico_call_efficiency = _detach_pico_call_efficiency(assembly)
        host = None
        try:
            executor = PicoDockerExecutor(self.sandbox_adapter)
            loop = assembly.agent_loop
            loop._executor = executor
            exec_tool = loop.tools.get("exec")
            if exec_tool is None:
                raise PicoBaselineRuntimeError(
                    "pico-harness did not register the exec tool"
                )
            exec_tool._executor = executor
            actual_tools = frozenset(loop.tools.tool_names)
            if actual_tools != PICO_CODING_TOOLS:
                raise PicoBaselineRuntimeError(
                    "unexpected pico-harness tool surface: "
                    f"expected {sorted(PICO_CODING_TOOLS)}, got {sorted(actual_tools)}"
                )

            start_backend = getattr(assembly, "start_memory_backend", None)
            if start_backend is not None:
                await start_backend()
            outlet = RecordingOutlet("polyglot")
            host = RuntimeTrialHost(assembly=assembly, outlet=outlet)
            request = TurnRequest(
                origin=Origin.USER,
                source=Source(
                    channel="polyglot",
                    chat_id="attempt",
                    sender_id="evaluator",
                    chat_type=ChatType.DM,
                ),
                text=str(prompt),
                conversation="polyglot:attempt",
            )
            observation = await host.run(request)
        finally:
            try:
                if host is None:
                    await assembly.close()
                else:
                    await host.close()
            finally:
                try:
                    _stop_pico_skill_watcher(assembly)
                finally:
                    if pico_call_efficiency is not None:
                        pico_call_efficiency.close()

        runtime_state = observation.runtime_state.value
        delivery_state = observation.delivery_state.value
        outcome = observation.outcome
        terminal_errors = [
            str(event.error)
            for event in observation.events
            if getattr(event, "error", None)
        ]
        answer = "\n".join(
            event.content for event in outlet.events if isinstance(event, Text)
        ).strip()
        runtime_failed = bool(observation.failure_category) or runtime_state in {
            "cancelled",
            "error",
            "provider_failed",
        }
        converged = bool(
            not runtime_failed
            and not self.provider.budget_exhausted
            and outcome is not None
            and outcome.explicit_reply
            and answer
        )
        stop_reason = (
            STOP_REASON_FINAL_ANSWER_RETURNED
            if converged
            else "provider_call_limit_reached"
            if self.provider.budget_exhausted
            else observation.failure_category or "runtime_did_not_converge"
        )
        usage = ModelUsageAggregate.from_usages(self.provider.usages)
        efficiency = CallEfficiencySummary.from_entries(
            self.provider.entries,
            turn_succeeded=converged,
        )
        self.evaluation_report = {
            "attempts": 1,
            "tool_steps": int(getattr(outcome, "tool_calls", 0) or 0),
            "usage": usage.to_metadata(),
            "call_efficiency": efficiency.to_dict(),
            "stop_reason": stop_reason,
        }
        event_counts = Counter(type(event).__name__ for event in observation.events)
        self.evaluation_evidence = {
            "schema": PICO_BASELINE_EVIDENCE_SCHEMA,
            "harness": PICO_BASELINE_VARIANT,
            "adapter_source": self.adapter_source,
            "runtime_state": runtime_state,
            "delivery_state": delivery_state,
            "failure_category": observation.failure_category,
            "terminal_error": terminal_errors[-1] if terminal_errors else None,
            "budget_exhausted": self.provider.budget_exhausted,
            "active_tools": sorted(actual_tools),
            "event_counts": dict(sorted(event_counts.items())),
            "outcome": {
                "explicit_reply": bool(getattr(outcome, "explicit_reply", False)),
                "tool_calls": int(getattr(outcome, "tool_calls", 0) or 0),
                "tool_failures": int(getattr(outcome, "tool_failures", 0) or 0),
                "memory_hits": int(getattr(outcome, "memory_hits", 0) or 0),
                "context_path": getattr(outcome, "context_path", None),
                "context_fallback_reason": getattr(
                    outcome, "context_fallback_reason", None
                ),
            },
            "provider_calls": [entry.to_dict() for entry in self.provider.entries],
        }
        if runtime_failed:
            detail = terminal_errors[-1] if terminal_errors else runtime_state
            raise PicoBaselineRuntimeError(
                "pico-harness runtime failed: "
                f"{observation.failure_category or runtime_state}: {detail}"
            )
        if not answer:
            raise PicoBaselineRuntimeError("pico-harness produced no delivered final text")
        return answer


__all__ = [
    "PICO_BASELINE_EVIDENCE_SCHEMA",
    "PICO_BASELINE_VARIANT",
    "PICO_CODING_TOOLS",
    "PicoBaselineRuntimeError",
    "PicoDockerExecutor",
    "PicoHarnessAgent",
    "RepoAgentProviderBridge",
    "pico_messages_to_model_messages",
    "pico_tools_to_model_tools",
]
