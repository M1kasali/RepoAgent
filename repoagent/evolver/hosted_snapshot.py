"""Pinned Agent snapshots with host-owned budgeted inference and call journals."""

import asyncio
from decimal import Decimal
import json
import math
from pathlib import Path
from uuid import uuid4

from ..tool_execution import ProcessOutcome
from .agent_snapshot import ScriptedAgentSnapshotEvaluator
from .contracts import sha256_bytes
from .evaluation import CandidateEvaluationError, payload_digest
from .model_budget import BudgetedEvaluationClient
from .model_channel import ModelCallJournal, run_model_worker
from .model_proxy import HostModelProxy


class HostedAgentSnapshotEvaluator(ScriptedAgentSnapshotEvaluator):
    """The trusted factory must return a fresh leaf gateway per trial, no retries."""

    task_mode = "host"

    def __init__(
        self, tasks, *, client_factory, model_descriptor, journal_root, **kwargs
    ):
        super().__init__(tasks, **kwargs)
        if not callable(client_factory):
            raise TypeError("hosted snapshots require a client factory")
        self._factory = client_factory
        self._model_json = json.dumps(model_descriptor, sort_keys=True, allow_nan=False)
        self._model = json.loads(self._model_json)["model"]
        self._channel_source = (
            Path(__file__).with_name("model_channel_guest.py").read_text()
        )
        self.journal_root = Path(journal_root).resolve()
        self.journal_root.mkdir(parents=True, exist_ok=True)
        self._used_clients = []

    def descriptor(self, repo_root):
        return {
            **super().descriptor(repo_root),
            "worker": "hosted-agent-snapshot/v1",
            "model_mode": "host-budgeted",
            "model_gateway": json.loads(self._model_json),
            "channel_digest": sha256_bytes(self._channel_source.encode()),
            "journal_root": str(self.journal_root),
        }

    def _worker_input(self, task):
        values = task.worker_input()
        values.pop("responses")
        return {**values, "model": self._model, "channel_source": self._channel_source}

    def _execute(self, adapter, work, task, check, identity, repetition, cost_limit):
        if (
            type(cost_limit) not in {int, float}
            or not math.isfinite(cost_limit)
            or cost_limit <= 0
        ):
            raise CandidateEvaluationError(
                "hosted trial requires a positive outer cost reservation"
            )
        client = self._factory()
        if not isinstance(client, BudgetedEvaluationClient):
            raise CandidateEvaluationError("factory did not return a budget gateway")
        if (
            any(client is previous for previous in self._used_clients)
            or client.evidence()["calls_reserved"]
        ):
            raise CandidateEvaluationError("trial model gateway was already used")
        self._used_clients.append(client)
        if client.descriptor() != json.loads(self._model_json):
            raise CandidateEvaluationError(
                "model gateway differs from frozen descriptor"
            )
        if (
            Decimal(str(client.limits.max_estimated_cost_usd))
            > Decimal(str(cost_limit))
            or task.max_calls != client.limits.max_calls
            or task.max_output_tokens != client.limits.max_output_tokens
            or client.limits.timeout_seconds > task.timeout_seconds
        ):
            raise CandidateEvaluationError(
                "model limits exceed trial reservation or task limits"
            )
        journal = ModelCallJournal(
            self.journal_root / uuid4().hex, worker_root=adapter.workspace
        )
        journal(
            {
                "status": "bound",
                "source": dict(identity),
                "task": task.descriptor(),
                "repetition": repetition,
                "outer_cost_limit_usd": cost_limit,
                "gateway_digest": payload_digest(client.descriptor()),
            }
        )
        proxy = HostModelProxy(client, evidence_sink=journal)
        worker = None
        error_type = None
        try:
            worker = asyncio.run(
                run_model_worker(
                    adapter,
                    "python",
                    [
                        "-I",
                        "-B",
                        "-c",
                        self._driver,
                        "../harness",
                        ".",
                        "../input.json",
                    ],
                    cwd=work,
                    proxy=proxy,
                    timeout_seconds=task.timeout_seconds,
                )
            )
        except Exception as exc:
            error_type = type(exc).__name__
        evidence = client.evidence()
        valid = evidence["measurement_valid"] and evidence["cost_complete"]
        cost = (
            evidence["known_estimated_cost_usd"] if evidence["cost_complete"] else None
        )
        if not evidence["calls_reserved"]:
            cost = 0.0
        if (
            worker is not None
            and len(worker.get("calls", [])) != evidence["calls_reserved"]
        ):
            valid = False
        journal(
            {
                "status": "trial_finished",
                "error_type": error_type,
                "model_evidence": evidence,
            }
        )
        rows = journal.ledger.events()
        raw = {
            "model_evidence": evidence,
            "model_journal": {
                "path": str(journal.path),
                "tail_digest": rows[-1]["digest"],
                "events": len(rows),
            },
            "worker_error_type": error_type,
        }
        text = json.dumps(worker, allow_nan=False) if worker is not None else ""
        ok = worker is not None and error_type is None and valid
        outcome = ProcessOutcome(
            "completed" if ok else "failed",
            0 if ok else 1,
            text,
            "",
            len(text),
            0,
            len(text) > check.max_output_chars,
        )
        return outcome, raw, cost
