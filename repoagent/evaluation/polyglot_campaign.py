"""Single-task executable Polyglot campaign with retained evidence."""

from __future__ import annotations

import difflib
import hashlib
import json
import time
from contextlib import nullcontext
from pathlib import Path

from ..atomic_io import LockUnavailableError, atomic_replace, file_lock
from ..evidence import EvidenceBundleBuilder, sha256_file
from ..paths import workspace_state_root
from ..task_state import STOP_REASON_FINAL_ANSWER_RETURNED
from .polyglot import PolyglotInstance, polyglot_workspace_context
from .provider_probe import (
    PROVIDER_PROBE_MAX_ATTEMPTS,
    ProviderProbeError,
    ProviderProbeResult,
    build_probe_approval,
    run_provider_probe,
)
from .schema import (
    EvaluationResult,
    EvaluationRow,
    collect_environment_provenance,
    collect_source_provenance,
    new_experiment,
)
from .statistics import wilson_interval
from .workspace import RawRowWriter


class PolyglotSingleTaskCampaign:
    EXECUTION_GUIDANCE = """

# Harness execution policy

Implement the requested solution in the provided workspace. The official tests are
hidden and will be run by the external grader after your turn. Use available local
checks when useful, but do not search repeatedly for hidden tests. Once the
implementation is complete, return a concise <final> answer.
""".rstrip()

    def __init__(
        self,
        *,
        repo_root,
        output_root,
        instance,
        benchmark,
        agent_factory,
        grader,
        require_provider_probe=False,
        require_clean_source=False,
        repetition=0,
        manage_paid_lock=True,
    ):
        if not isinstance(instance, PolyglotInstance):
            raise TypeError("Polyglot campaign requires one PolyglotInstance")
        if not callable(agent_factory):
            raise TypeError("Polyglot campaign agent_factory must be callable")
        self.repo_root = Path(repo_root).resolve()
        self.output_root = Path(output_root).resolve()
        self.instance = instance
        self.benchmark = dict(benchmark)
        self.agent_factory = agent_factory
        self.grader = grader
        self.require_provider_probe = bool(require_provider_probe)
        self.require_clean_source = bool(require_clean_source)
        if isinstance(repetition, bool) or not isinstance(repetition, int) or repetition < 0:
            raise ValueError("Polyglot repetition must be a non-negative integer")
        self.repetition = repetition
        self.manage_paid_lock = bool(manage_paid_lock)

    def run(self):
        lock = (
            file_lock(self._campaign_lock_path(), blocking=False)
            if self.require_provider_probe and self.manage_paid_lock
            else nullcontext()
        )
        try:
            with lock:
                return self._run_locked()
        except LockUnavailableError as exc:
            raise RuntimeError(
                "this benchmark already has an active paid campaign"
            ) from exc

    def _run_locked(self):
        source = collect_source_provenance(self.repo_root)
        if self.require_clean_source:
            if source.get("dirty"):
                raise ValueError("formal Polyglot campaign requires a clean worktree")
            if not str(source.get("commit_sha", "")).strip():
                raise ValueError("formal Polyglot campaign requires a Git commit")
        if self.output_root.exists():
            raise FileExistsError(
                f"Polyglot campaign output already exists: {self.output_root}"
            )
        self.output_root.mkdir(parents=True)
        context = polyglot_workspace_context(
            self.instance, self.output_root / "runner-workspace"
        )
        agent = self.agent_factory(context)
        started = time.monotonic()
        answer = ""
        error = ""
        evidence = {}
        provider_probe = {"status": "not_required"}
        if self.require_provider_probe:
            profile = getattr(agent.model_client, "profile", None)
            requested_model = str(
                getattr(profile, "model", getattr(agent.model_client, "model", ""))
            )
            pricing = getattr(profile, "pricing", None)
            try:
                approval_identity = self._approval_identity(agent, source)
                tokenizer_metadata = agent.context_manager.token_counter.metadata()
                pricing_source = str(getattr(pricing, "source", ""))
                _, approval_digest = build_probe_approval(
                    provider=str(getattr(profile, "provider", "")),
                    model=requested_model,
                    tokenizer_metadata=tokenizer_metadata,
                    pricing_source=pricing_source,
                    max_attempts=PROVIDER_PROBE_MAX_ATTEMPTS,
                    max_output_tokens=128,
                    timeout_seconds=60.0,
                    approval_identity=approval_identity,
                )
                probe = self._read_probe_cache(approval_digest)
                cache_hit = probe is not None
                if probe is None:
                    probe = run_provider_probe(
                        agent.model_client,
                        requested_model=requested_model,
                        tokenizer_metadata=tokenizer_metadata,
                        pricing_source=pricing_source,
                        approval_identity=approval_identity,
                    )
                    self._write_probe_cache(probe)
                if collect_source_provenance(self.repo_root) != source:
                    raise ValueError("source changed after provider preflight")
                provider_probe = {
                    "status": "pass",
                    "cache_hit": cache_hit,
                    **probe.to_dict(),
                }
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                provider_probe = {"status": "fail", "error": error}
            probe_path = self.output_root / "provider-preflight.json"
            probe_path.write_text(
                json.dumps(provider_probe, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            evidence.update(
                {
                    "provider_preflight": probe_path.relative_to(
                        self.output_root
                    ).as_posix(),
                    "provider_preflight_sha256": sha256_file(probe_path),
                }
            )
        if not error:
            try:
                answer = agent.ask(
                    self.instance.runner.instructions + self.EXECUTION_GUIDANCE
                )
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        run_id = str(getattr(getattr(agent, "current_task_state", None), "run_id", ""))
        report = {}
        if run_id:
            bundle = self.output_root / "agent-evidence"
            EvidenceBundleBuilder(agent.run_store).build(run_id, bundle)
            report_path = bundle / "report.json"
            if report_path.is_file():
                report = json.loads(report_path.read_text(encoding="utf-8"))
            evidence.update(
                {
                    "agent_bundle": bundle.relative_to(self.output_root).as_posix(),
                    "agent_manifest_sha256": sha256_file(bundle / "manifest.json"),
                }
            )
        patch_path = self._write_patch(context.repo_root)
        evidence.update(
            {
                "patch": patch_path.relative_to(self.output_root).as_posix(),
                "patch_sha256": sha256_file(patch_path),
            }
        )
        grade = None
        if not error:
            try:
                grade = self.grader.grade(self.instance, context.repo_root)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
        if grade is None:
            grade = {
                "task_id": self.instance.runner.task_id,
                "passed": False,
                "status": "not_run" if error else "error",
                "exit_code": None,
                "duration_seconds": 0.0,
                "stdout": "",
                "stderr": "",
                "output_truncated": False,
            }
        grade_path = self.output_root / "grade.json"
        grade_path.write_text(
            json.dumps(grade, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        evidence.update(
            {
                "grade": grade_path.relative_to(self.output_root).as_posix(),
                "grade_sha256": sha256_file(grade_path),
            }
        )
        code_passed = bool(grade.get("passed")) and not error
        stop_reason = str(report.get("stop_reason", ""))
        turn_converged = stop_reason == STOP_REASON_FINAL_ANSWER_RETURNED
        passed = code_passed and turn_converged
        row = EvaluationRow(
            task_id=self.instance.runner.task_id,
            variant="repoagent-harness",
            repetition=self.repetition,
            status="pass" if passed else ("error" if error else "fail"),
            metrics={
                "duration_seconds": round(time.monotonic() - started, 6),
                "grader_duration_seconds": float(grade.get("duration_seconds", 0.0)),
                "attempts": int(report.get("attempts", 0)),
                "tool_steps": int(report.get("tool_steps", 0)),
                "usage": dict(report.get("usage", {})),
                "call_efficiency": dict(report.get("call_efficiency", {})),
                "stop_reason": stop_reason,
                "turn_converged": turn_converged,
                "provider_preflight_status": str(provider_probe.get("status", "")),
                "provider_preflight_cache_hit": bool(
                    provider_probe.get("cache_hit", False)
                ),
            },
            verifier={
                "passed": passed,
                "code_passed": code_passed,
                "turn_converged": turn_converged,
                "provider_preflight_status": str(provider_probe.get("status", "")),
                "stop_reason": stop_reason,
                "status": str(grade.get("status", "")),
                "exit_code": grade.get("exit_code"),
                "failure_category": self._failure_category(
                    error, grade, provider_probe
                ),
            },
            evidence=evidence,
            error=error,
        )
        RawRowWriter(self.output_root / "rows.jsonl").append(row)
        profile = getattr(agent.model_client, "profile", None)
        run_kind = (
            "scripted"
            if type(agent.model_client).__name__ == "FakeModelClient"
            else "live"
        )
        model = {
            "run_kind": run_kind,
            "provider": str(
                getattr(profile, "provider", type(agent.model_client).__name__)
            ),
            "model": str(
                getattr(profile, "model", getattr(agent.model_client, "model", ""))
            ),
            "temperature": getattr(profile, "temperature", None),
            "provider_preflight": provider_probe,
        }
        benchmark = {
            **self.benchmark,
            "unique_tasks": 1,
            "selected_task_ids": [self.instance.runner.task_id],
        }
        result = EvaluationResult(
            experiment=new_experiment("aider-polyglot-single"),
            source=source,
            environment=collect_environment_provenance(self.repo_root),
            benchmark=benchmark,
            model=model,
            design={
                "variants": ["repoagent-harness"],
                "repetitions": 1,
                "repetition_index": self.repetition,
                "paired": False,
                "max_grader_attempts": 1,
            },
            rows=[row],
            aggregates={
                "effective_n": 1,
                "run_n": 1,
                "passes": int(passed),
                "pass_rate": float(passed),
                "pass_rate_wilson_95": wilson_interval(int(passed), 1),
                "code_passes": int(code_passed),
                "code_pass_rate": float(code_passed),
                "converged_turns": int(turn_converged),
                "convergence_rate": float(turn_converged),
            },
            gates=(
                [
                    {
                        "id": "provider_preflight",
                        "status": str(provider_probe.get("status", "fail")),
                        "observed": str(provider_probe.get("status", "fail")),
                        "threshold": "pass",
                    }
                ]
                if self.require_provider_probe
                else []
            )
            + [
                {
                    "id": "code_tests_passed",
                    "status": "pass" if code_passed else "fail",
                    "observed": f"{int(code_passed)}/1",
                    "threshold": "1/1",
                },
                {
                    "id": "runtime_converged",
                    "status": "pass" if turn_converged else "fail",
                    "observed": f"{int(turn_converged)}/1",
                    "threshold": "1/1",
                },
            ],
            limitations=[
                "One selected task is a transport and grading smoke, not a model-quality estimate.",
                "Code correctness and runtime convergence are reported separately.",
                "USD cost is unavailable unless explicit pricing is configured.",
            ],
        )
        result.write(self.output_root / "results.json")
        (self.output_root / "answer.txt").write_text(answer + "\n", encoding="utf-8")
        return result

    def _campaign_lock_path(self):
        digest = str(self.benchmark.get("definition_digest", "unknown"))
        safe_digest = "".join(
            character for character in digest if character.isalnum()
        )
        return (
            workspace_state_root(self.repo_root)
            / "evaluation-locks"
            / f"polyglot-{safe_digest or 'unknown'}.lock"
        )

    def _probe_cache_path(self, approval_digest):
        key = str(approval_digest).removeprefix("sha256:")
        return (
            workspace_state_root(self.repo_root)
            / "provider-probes"
            / f"{key}.json"
        )

    def _read_probe_cache(self, approval_digest):
        path = self._probe_cache_path(approval_digest)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema") != "repoagent.provider-probe-cache/v1":
                raise ValueError("schema mismatch")
            result_payload = dict(payload["result"])
            integrity_material = {
                "schema": payload["schema"],
                "approval_digest": payload["approval_digest"],
                "result": result_payload,
            }
            expected = self._canonical_digest(integrity_material)
            if payload.get("record_digest") != expected:
                raise ValueError("record digest mismatch")
            if payload.get("approval_digest") != approval_digest:
                raise ValueError("approval digest mismatch")
            result_payload["usage_fields"] = tuple(result_payload["usage_fields"])
            probe = ProviderProbeResult(**result_payload)
            if probe.approval_digest != approval_digest:
                raise ValueError("result approval digest mismatch")
            return probe
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderProbeError(
                f"cached Provider preflight is invalid: {exc}"
            ) from exc

    def _write_probe_cache(self, probe):
        path = self._probe_cache_path(probe.approval_digest)
        material = {
            "schema": "repoagent.provider-probe-cache/v1",
            "approval_digest": probe.approval_digest,
            "result": probe.to_dict(),
        }
        payload = {**material, "record_digest": self._canonical_digest(material)}
        atomic_replace(
            path,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )

    @staticmethod
    def _canonical_digest(payload):
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def _approval_identity(self, agent, source):
        runtime_config = {
            "provider": str(
                getattr(getattr(agent.model_client, "profile", None), "provider", "")
            ),
            "model": str(
                getattr(
                    getattr(agent.model_client, "profile", None),
                    "model",
                    getattr(agent.model_client, "model", ""),
                )
            ),
            "max_steps": int(getattr(agent, "max_steps", 0)),
            "max_new_tokens": int(getattr(agent, "max_new_tokens", 0)),
            "context_token_budget": int(
                getattr(getattr(agent, "context_manager", None), "total_token_budget", 0)
            ),
            "tool_signature": str(
                agent.tool_signature() if callable(getattr(agent, "tool_signature", None)) else ""
            ),
            "sandbox_identity": str(
                getattr(getattr(agent, "sandbox_adapter", None), "identity", "unknown")
            ),
            "sandbox_isolated": bool(
                getattr(getattr(agent, "sandbox_adapter", None), "is_isolated", False)
            ),
        }
        payload = json.dumps(
            runtime_config,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return {
            "source_commit": str(source.get("commit_sha", "")) or "uncommitted",
            "source_tree_digest": str(source.get("tree_digest", "")),
            "source_dirty": bool(source.get("dirty")),
            "benchmark_digest": str(self.benchmark.get("definition_digest", "")),
            "runtime_config_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "sandbox_identity": runtime_config["sandbox_identity"],
            "sandbox_isolated": runtime_config["sandbox_isolated"],
        }

    def _write_patch(self, runner_root):
        runner_root = Path(runner_root)
        parts = []
        for relative in self.instance.runner.solution_files:
            original = (
                (self.instance.exercise_root / relative)
                .read_text(encoding="utf-8", errors="replace")
                .splitlines(keepends=True)
            )
            produced_path = runner_root / relative
            produced = (
                produced_path.read_text(encoding="utf-8", errors="replace").splitlines(
                    keepends=True
                )
                if produced_path.is_file()
                else []
            )
            parts.extend(
                difflib.unified_diff(
                    original,
                    produced,
                    fromfile=f"a/{relative}",
                    tofile=f"b/{relative}",
                )
            )
        path = self.output_root / "model.patch"
        path.write_text("".join(parts), encoding="utf-8")
        return path

    @staticmethod
    def _failure_category(error, grade, provider_probe):
        if provider_probe.get("status") == "fail":
            return "provider_preflight_failed"
        if error:
            return "runner_or_grader_error"
        if grade.get("status") == "timeout":
            return "test_timeout"
        if grade.get("exit_code") != 0:
            return "tests_failed"
        return ""


__all__ = ["PolyglotSingleTaskCampaign"]
