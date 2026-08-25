"""Bounded subagent contracts, roles, isolated workspaces, and evidence."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from .evidence import sha256_file
from .workspace import IGNORED_PATH_NAMES


SUBAGENT_STATES = frozenset({"completed", "failed", "cancelled"})
SUBAGENT_ROLES = frozenset({"implementer", "reviewer", "red-team-verifier"})


@dataclass(frozen=True)
class SubagentBudget:
    max_steps: int
    max_input_tokens: int
    max_output_tokens: int
    timeout_seconds: float

    def __post_init__(self):
        for name in ("max_steps", "max_input_tokens", "max_output_tokens"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"subagent {name} must be a positive integer")
        if self.timeout_seconds <= 0:
            raise ValueError("subagent timeout_seconds must be positive")

    def attenuate(self, parent: "SubagentBudget") -> "SubagentBudget":
        return SubagentBudget(
            max_steps=min(self.max_steps, parent.max_steps),
            max_input_tokens=min(self.max_input_tokens, parent.max_input_tokens),
            max_output_tokens=min(self.max_output_tokens, parent.max_output_tokens),
            timeout_seconds=min(self.timeout_seconds, parent.timeout_seconds),
        )

    def to_dict(self):
        return {
            "max_steps": self.max_steps,
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class SubagentMessage:
    sender_id: str
    recipient_id: str
    kind: str
    content: str
    sequence: int

    def __post_init__(self):
        if self.kind not in {"request", "result", "error"}:
            raise ValueError("unsupported subagent message kind")
        if not self.sender_id or not self.recipient_id or not self.content:
            raise ValueError("subagent message endpoints and content must not be empty")
        if self.sequence < 1:
            raise ValueError("subagent message sequence must be positive")

    def to_dict(self):
        return {
            "sender_id": self.sender_id,
            "recipient_id": self.recipient_id,
            "kind": self.kind,
            "content": self.content,
            "sequence": self.sequence,
        }


@dataclass(frozen=True)
class SubagentRequest:
    subagent_id: str
    parent_turn_id: str
    parent_request_id: str
    parent_session_id: str
    task: str
    role: str
    budget: SubagentBudget
    allowed_tools: tuple[str, ...]
    messages: tuple[SubagentMessage, ...] = ()

    def __post_init__(self):
        if not self.subagent_id.startswith("subagent_"):
            raise ValueError("subagent_id must use the subagent_ prefix")
        if self.role not in SUBAGENT_ROLES:
            raise ValueError(f"unsupported subagent role: {self.role}")
        if not self.task or not self.parent_session_id:
            raise ValueError("subagent task and parent session must not be empty")
        if not self.allowed_tools or len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("subagent allowed_tools must be non-empty and unique")

    @classmethod
    def create(cls, **values):
        values.setdefault("subagent_id", "subagent_" + uuid4().hex[:16])
        return cls(**values)

    def to_dict(self):
        return {
            "subagent_id": self.subagent_id,
            "parent_turn_id": self.parent_turn_id,
            "parent_request_id": self.parent_request_id,
            "parent_session_id": self.parent_session_id,
            "task": self.task,
            "role": self.role,
            "budget": self.budget.to_dict(),
            "allowed_tools": list(self.allowed_tools),
            "messages": [message.to_dict() for message in self.messages],
        }


@dataclass(frozen=True)
class SubagentOutcome:
    subagent_id: str
    state: str
    answer: str = ""
    error: str = ""
    usage: Mapping[str, Any] = field(default_factory=dict)
    call_efficiency: Mapping[str, Any] = field(default_factory=dict)
    tool_calls: int = 0
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.state not in SUBAGENT_STATES:
            raise ValueError(f"unsupported subagent outcome state: {self.state}")
        if self.tool_calls < 0:
            raise ValueError("subagent tool_calls must not be negative")
        object.__setattr__(self, "usage", MappingProxyType(dict(self.usage)))
        object.__setattr__(self, "call_efficiency", MappingProxyType(dict(self.call_efficiency)))
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))

    def to_dict(self):
        return {
            "subagent_id": self.subagent_id,
            "state": self.state,
            "answer": self.answer,
            "error": self.error,
            "usage": dict(self.usage),
            "call_efficiency": dict(self.call_efficiency),
            "tool_calls": self.tool_calls,
            "evidence": dict(self.evidence),
        }


ROLE_PROFILES = MappingProxyType(
    {
        "implementer": {
            "instruction": "Implement the requested change in the isolated workspace and report the patch.",
            "tools": ("list_files", "read_file", "search", "write_file", "patch_file", "git_diff"),
            "read_only": False,
        },
        "reviewer": {
            "instruction": "Review repository evidence without changing files and report concrete findings.",
            "tools": (
                "list_files",
                "read_file",
                "search",
                "git_status",
                "git_diff",
                "git_worktree_list",
            ),
            "read_only": True,
        },
        "red-team-verifier": {
            "instruction": "Adversarially inspect the proposed change for policy, security, and correctness failures without changing files.",
            "tools": (
                "list_files",
                "read_file",
                "search",
                "git_status",
                "git_diff",
                "git_worktree_list",
            ),
            "read_only": True,
        },
    }
)


class IsolatedSubagentWorkspace:
    def __init__(self, source, subagent_id):
        self.source = Path(source).resolve()
        self.subagent_id = str(subagent_id)
        self._temporary = None
        self.root = None

    def __enter__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix=f"{self.subagent_id}-")
        self.root = Path(self._temporary.name) / "workspace"

        def ignore(_directory, names):
            return [name for name in names if name in IGNORED_PATH_NAMES or name == ".git"]

        shutil.copytree(self.source, self.root, ignore=ignore)
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._temporary.cleanup()
        self._temporary = None

    @property
    def digest(self):
        digest = hashlib.sha256()
        for path in sorted(item for item in self.root.rglob("*") if item.is_file()):
            relative = path.relative_to(self.root).as_posix().encode("utf-8")
            digest.update(relative)
            digest.update(path.read_bytes())
        return "sha256:" + digest.hexdigest()


def persist_subagent_evidence(parent_run_dir, request, outcome, child_run_dir):
    target = Path(parent_run_dir) / "subagents" / request.subagent_id
    if target.exists():
        raise FileExistsError(f"subagent evidence already exists: {request.subagent_id}")
    target.mkdir(parents=True)
    records = []
    for filename in ("task_state.json", "trace.jsonl", "calls.jsonl", "report.json"):
        source = Path(child_run_dir) / filename
        if not source.is_file():
            continue
        destination = target / filename
        shutil.copyfile(source, destination)
        records.append(
            {"path": filename, "sha256": sha256_file(destination), "size_bytes": destination.stat().st_size}
        )
    manifest = {
        "schema": "repoagent.subagent-evidence/v1",
        "request": request.to_dict(),
        "outcome": outcome.to_dict(),
        "files": records,
    }
    manifest_path = target / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "path": target.relative_to(parent_run_dir).as_posix(),
        "manifest_sha256": sha256_file(manifest_path),
    }


__all__ = [
    "IsolatedSubagentWorkspace",
    "ROLE_PROFILES",
    "SUBAGENT_ROLES",
    "SubagentBudget",
    "SubagentMessage",
    "SubagentOutcome",
    "SubagentRequest",
    "persist_subagent_evidence",
]
