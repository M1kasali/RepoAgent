"""Single execution seam for typed tool requests and results."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import re
import time

from .capabilities import CapabilityDecision, capability_token_digest
from .tool_contracts import ToolEffect, ToolRequest, ToolResult
from .tool_execution import ToolExecutionControl, ToolRunnerOutput, limit_output
from .tool_scheduling import MutationConflictPolicy, ToolBatch, ToolBatchMode


def compatibility_metadata(result: ToolResult) -> dict:
    """Project a typed result for legacy reports during the P3 migration."""

    metadata = dict(result.metadata)
    value = {
        "tool_status": result.status,
        "tool_error_code": result.error_code,
        "security_event_type": metadata.get("security_event_type", ""),
        "risk_level": metadata.get("risk_level", "low"),
        "read_only": metadata.get("read_only", result.effect is ToolEffect.READ),
        "affected_paths": list(result.affected_paths),
        "workspace_changed": result.workspace_changed,
        "diff_summary": list(metadata.get("diff_summary", [])),
        "capability": dict(metadata.get("capability", {})),
        "approval": dict(metadata.get("approval", {})),
        "execution_status": metadata.get("execution_status", ""),
        "timeout_seconds": metadata.get("timeout_seconds"),
        "output_chars": metadata.get("output_chars", len(result.content)),
        "output_limit_chars": metadata.get("output_limit_chars"),
        "output_truncated": bool(metadata.get("output_truncated", False)),
    }
    if metadata.get("workspace_fingerprint"):
        value["workspace_fingerprint"] = metadata["workspace_fingerprint"]
    return value


class ToolGateway:
    """Validate, authorize, execute, and account for every tool request."""

    DEFAULT_MAX_PARALLEL = 4

    def __init__(
        self,
        host,
        *,
        max_parallel=DEFAULT_MAX_PARALLEL,
        mutation_conflict_policy=MutationConflictPolicy.SERIAL,
    ) -> None:
        if (
            isinstance(max_parallel, bool)
            or not isinstance(max_parallel, int)
            or max_parallel < 1
        ):
            raise ValueError("max_parallel must be a positive integer")
        self._host = host
        self._max_parallel = max_parallel
        try:
            self._mutation_conflict_policy = MutationConflictPolicy(
                mutation_conflict_policy
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("unsupported mutation conflict policy") from exc

    @property
    def definitions(self):
        return {name: entry["definition"] for name, entry in self._host.tools.items()}

    @property
    def max_parallel(self):
        return self._max_parallel

    @property
    def mutation_conflict_policy(self):
        return self._mutation_conflict_policy

    def request_effect(self, request: ToolRequest) -> ToolEffect:
        if not isinstance(request, ToolRequest):
            raise TypeError("request_effect requires ToolRequest")
        entry = self._host.tools.get(request.name)
        return (
            entry["definition"].effect
            if entry is not None
            else ToolEffect.UNKNOWN
        )

    def is_concurrency_safe(self, request: ToolRequest) -> bool:
        if not isinstance(request, ToolRequest):
            raise TypeError("is_concurrency_safe requires ToolRequest")
        entry = self._host.tools.get(request.name)
        if entry is None:
            return False
        definition = entry["definition"]
        return bool(
            definition.effect is ToolEffect.READ and definition.concurrency_safe
        )

    def plan_batches(self, requests):
        """Split calls into bounded read-only parallel batches and serial barriers."""

        pending = tuple(requests)
        if any(not isinstance(request, ToolRequest) for request in pending):
            raise TypeError("plan_batches requires ToolRequest values")
        batches = []
        index = 0
        while index < len(pending):
            if not self.is_concurrency_safe(pending[index]):
                request = pending[index]
                effect = self.request_effect(request)
                if effect is ToolEffect.READ:
                    reason = "read_not_concurrency_safe"
                elif effect is ToolEffect.UNKNOWN:
                    reason = "unresolved_tool_serialized"
                else:
                    reason = "mutation_conflict_policy"
                batches.append(
                    ToolBatch(
                        requests=(request,),
                        mode=ToolBatchMode.SERIAL,
                        reason=reason,
                        effects=(effect,),
                        mutation_conflict_policy=self._mutation_conflict_policy,
                    )
                )
                index += 1
                continue
            end = index + 1
            while end < len(pending) and self.is_concurrency_safe(pending[end]):
                end += 1
            for start in range(index, end, self._max_parallel):
                requests_in_batch = pending[
                    start : min(start + self._max_parallel, end)
                ]
                parallel = len(requests_in_batch) > 1
                batches.append(
                    ToolBatch(
                        requests=requests_in_batch,
                        mode=(
                            ToolBatchMode.PARALLEL
                            if parallel
                            else ToolBatchMode.SERIAL
                        ),
                        reason=(
                            "concurrency_safe_read"
                            if parallel
                            else "concurrency_safe_read_singleton"
                        ),
                        effects=(ToolEffect.READ,) * len(requests_in_batch),
                        mutation_conflict_policy=self._mutation_conflict_policy,
                    )
                )
            index = end
        return tuple(batches)

    def execute_batch(self, requests, *, cancellation_token=None):
        """Execute one planned batch and return results in request order."""

        decision = requests if isinstance(requests, ToolBatch) else None
        batch = tuple(requests)
        if not batch:
            return ()
        if any(not isinstance(request, ToolRequest) for request in batch):
            raise TypeError("execute_batch requires ToolRequest values")
        parallel = (
            decision.mode is ToolBatchMode.PARALLEL
            if decision is not None
            else len(batch) > 1
            and all(self.is_concurrency_safe(request) for request in batch)
        )
        if parallel and not all(
            self.is_concurrency_safe(request) for request in batch
        ):
            raise ValueError("parallel tool batch contains an unsafe request")
        if not parallel:
            return tuple(
                self.execute(request, cancellation_token=cancellation_token)
                for request in batch
            )
        if len(batch) > self._max_parallel:
            raise ValueError("tool batch exceeds max_parallel")
        with ThreadPoolExecutor(
            max_workers=len(batch), thread_name_prefix="repoagent-tool"
        ) as executor:
            futures = tuple(
                executor.submit(
                    self.execute,
                    request,
                    cancellation_token=cancellation_token,
                    observe=False,
                )
                for request in batch
            )
            results = tuple(future.result() for future in futures)
        for request, result in zip(batch, results, strict=True):
            self.observe_result(request, result)
        return results

    def observe_result(self, request: ToolRequest, result: ToolResult) -> None:
        """Commit mutable host observations in deterministic request order."""

        try:
            arguments = self._host.validate_tool(request.name, request.arguments)
        except Exception:
            arguments = dict(request.arguments)
        self._host.update_memory_after_tool(request.name, arguments, result.content)
        self._host.record_process_note_for_tool(
            request.name, compatibility_metadata(result)
        )

    def _result(
        self,
        request,
        *,
        status,
        effect,
        content,
        started_at,
        error_code="",
        affected_paths=(),
        workspace_changed=False,
        metadata=None,
    ):
        return ToolResult(
            call_id=request.call_id,
            name=request.name,
            status=status,
            effect=effect,
            content=content,
            duration_ms=(time.monotonic() - started_at) * 1000,
            error_code=error_code,
            affected_paths=tuple(affected_paths),
            workspace_changed=workspace_changed,
            metadata=metadata or {},
        )

    def execute(
        self,
        request: ToolRequest,
        *,
        cancellation_token=None,
        observe=True,
    ) -> ToolResult:
        if not isinstance(request, ToolRequest):
            raise TypeError("ToolGateway.execute requires ToolRequest")
        host = self._host
        started_at = time.monotonic()
        if host.allowed_tools is not None and request.name not in host.allowed_tools:
            return self._result(
                request,
                status="rejected",
                effect=ToolEffect.UNKNOWN,
                content=f"error: tool '{request.name}' is not allowed in this run",
                started_at=started_at,
                error_code="tool_not_allowed",
                metadata={"risk_level": "high", "read_only": False},
            )

        entry = host.tools.get(request.name)
        if entry is None:
            return self._result(
                request,
                status="rejected",
                effect=ToolEffect.UNKNOWN,
                content=f"error: unknown tool '{request.name}'",
                started_at=started_at,
                error_code="unknown_tool",
                metadata={"risk_level": "high", "read_only": False},
            )

        definition = entry["definition"]
        effect = definition.effect
        requires_approval = (
            definition.requires_approval or effect is not ToolEffect.READ
        )
        read_only = effect is ToolEffect.READ
        captures_workspace = effect in {ToolEffect.WRITE, ToolEffect.EXECUTE}
        if request.session_id != host.session["id"]:
            capability = CapabilityDecision(
                False,
                "request_session_mismatch",
                token_digest=capability_token_digest(request.capability_token),
            )
        else:
            capability = host.capability_authority.authorize(
                request.capability_token,
                subject_id=host.capability_subject_id,
                session_id=host.session["id"],
                tool_name=request.name,
                effect=effect,
            )
        base_metadata = {
            "risk_level": "high" if requires_approval else "low",
            "read_only": read_only,
            "capability": capability.to_dict(),
            "sandbox_identity": host.sandbox_adapter.identity,
            "sandbox_isolated": host.sandbox_adapter.is_isolated,
        }
        if not capability.allowed:
            return self._result(
                request,
                status="rejected",
                effect=effect,
                content=(
                    f"error: capability denied for {request.name}: {capability.reason}"
                ),
                started_at=started_at,
                error_code="capability_denied",
                metadata={
                    **base_metadata,
                    "security_event_type": "capability_denied",
                },
            )
        requires_isolation = bool(
            definition.requires_isolation
            or (
                host.require_isolation
                and effect in {ToolEffect.EXECUTE, ToolEffect.EXTERNAL}
            )
        )
        base_metadata["requires_isolation"] = requires_isolation
        if requires_isolation and not host.sandbox_adapter.is_isolated:
            return self._result(
                request,
                status="rejected",
                effect=effect,
                content=f"error: tool {request.name} requires an isolated sandbox",
                started_at=started_at,
                error_code="sandbox_required",
                metadata={
                    **base_metadata,
                    "security_event_type": "sandbox_required",
                },
            )
        try:
            arguments = host.validate_tool(request.name, request.arguments)
        except Exception as exc:
            example = host.tool_example(request.name)
            message = f"error: invalid arguments for {request.name}: {exc}"
            if example:
                message += f"\nexample: {example}"
            return self._result(
                request,
                status="rejected",
                effect=effect,
                content=message,
                started_at=started_at,
                error_code="invalid_arguments",
                metadata={
                    **base_metadata,
                    "security_event_type": (
                        "path_escape" if "path escapes workspace" in str(exc) else ""
                    ),
                },
            )

        if host.repeated_tool_call(request.name, arguments):
            return self._result(
                request,
                status="rejected",
                effect=effect,
                content=(
                    "error: repeated identical tool call for "
                    f"{request.name}; choose a different tool or return a final answer"
                ),
                started_at=started_at,
                error_code="repeated_identical_call",
                metadata=base_metadata,
            )

        approval = host.approval_engine.decide(definition, request, arguments)
        authorized_metadata = {
            **base_metadata,
            "approval": approval.to_dict(),
        }
        if not approval.allowed:
            return self._result(
                request,
                status="rejected",
                effect=effect,
                content=f"error: approval denied for {request.name}",
                started_at=started_at,
                error_code="approval_denied",
                metadata={
                    **authorized_metadata,
                    "security_event_type": (
                        "read_only_block"
                        if approval.reason == "read_only"
                        else "approval_denied"
                    ),
                },
            )

        timeout_seconds = min(
            float(request.timeout_seconds or definition.timeout_seconds),
            float(definition.timeout_seconds),
        )
        if request.name == "run_shell":
            timeout_seconds = min(timeout_seconds, float(arguments["timeout"]))
        max_output_chars = min(
            int(request.max_output_chars or definition.max_output_chars),
            int(definition.max_output_chars),
        )
        control = ToolExecutionControl(
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
            cancellation_token=cancellation_token,
        )
        execution_metadata = {
            **authorized_metadata,
            "timeout_seconds": timeout_seconds,
            "output_limit_chars": max_output_chars,
        }
        before = host.capture_workspace_snapshot() if captures_workspace else {}
        try:
            if control.status() == "running":
                raw_output = entry["run"](arguments, control)
            else:
                raw_output = ToolRunnerOutput(
                    "tool execution did not start",
                    {"execution_status": control.status()},
                )
            if isinstance(raw_output, ToolRunnerOutput):
                content = raw_output.content
                runner_metadata = dict(raw_output.metadata)
            else:
                content = str(raw_output)
                runner_metadata = {}
            content, gateway_truncated, output_chars = limit_output(
                content, max_output_chars
            )
            content = host.redact_text(content)
            output_truncated = bool(
                gateway_truncated or runner_metadata.get("output_truncated")
            )
            after = host.capture_workspace_snapshot() if captures_workspace else before
            affected_paths, diff_summary = host.diff_workspace_snapshots(before, after)
            workspace_changed = bool(affected_paths)
            status = "ok"
            error_code = ""
            execution_status = str(
                runner_metadata.get("execution_status") or control.status()
            )
            if execution_status == "cancelled":
                status = "cancelled"
                error_code = "tool_cancelled"
            elif execution_status == "timeout":
                status = "timeout"
                error_code = "tool_timeout"
            elif request.name == "run_shell" or runner_metadata.get(
                "exit_code_is_error"
            ):
                exit_code = runner_metadata.get("exit_code")
                if exit_code is None:
                    match = re.search(r"exit_code:\s*(-?\d+)", content)
                    exit_code = int(match.group(1)) if match else 0
                if exit_code != 0 and workspace_changed:
                    status = "partial_success"
                    error_code = "tool_partial_success"
                elif exit_code != 0:
                    status = "error"
                    error_code = "tool_failed"
            if status == "ok" and output_truncated:
                status = "partial_success"
                error_code = "tool_output_truncated"
            result = self._result(
                request,
                status=status,
                effect=effect,
                content=content,
                started_at=started_at,
                error_code=error_code,
                affected_paths=affected_paths,
                workspace_changed=workspace_changed,
                metadata={
                    **execution_metadata,
                    **runner_metadata,
                    "execution_status": execution_status,
                    "output_chars": output_chars,
                    "output_truncated": output_truncated,
                    "workspace_fingerprint": host.workspace.fingerprint(),
                    "diff_summary": diff_summary,
                },
            )
        except Exception as exc:
            after = host.capture_workspace_snapshot() if captures_workspace else before
            affected_paths, diff_summary = host.diff_workspace_snapshots(before, after)
            workspace_changed = bool(affected_paths)
            execution_status = control.status()
            if execution_status == "cancelled":
                status, error_code = "cancelled", "tool_cancelled"
            elif execution_status == "timeout":
                status, error_code = "timeout", "tool_timeout"
            else:
                status = "partial_success" if workspace_changed else "error"
                error_code = (
                    "tool_partial_success" if workspace_changed else "tool_failed"
                )
            result = self._result(
                request,
                status=status,
                effect=effect,
                content=f"error: tool {request.name} failed: {exc}",
                started_at=started_at,
                error_code=error_code,
                affected_paths=affected_paths,
                workspace_changed=workspace_changed,
                metadata={
                    **execution_metadata,
                    "security_event_type": (
                        "path_escape" if "path escapes workspace" in str(exc) else ""
                    ),
                    "workspace_fingerprint": host.workspace.fingerprint(),
                    "diff_summary": diff_summary,
                    "execution_status": execution_status,
                    "output_chars": 0,
                    "output_truncated": False,
                },
            )
        if observe:
            self.observe_result(request, result)
        return result


__all__ = ["ToolGateway", "compatibility_metadata"]
