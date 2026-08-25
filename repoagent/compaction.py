"""Deterministic history compaction with content-free provenance records."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from types import MappingProxyType
from typing import Mapping

from .tokenization import TokenCounter


HISTORY_COMPACTION_STRATEGY = "deterministic-history-v1"


def _digest(value):
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CompactionRecord:
    source_index: int
    source_role: str
    source_tool: str
    operation: str
    budget_action: str
    included: bool
    input_tokens: int
    output_tokens: int
    input_digest: str
    output_digest: str
    provenance: Mapping = field(default_factory=dict)

    def __post_init__(self):
        if self.source_index < 0:
            raise ValueError("compaction source_index must be non-negative")
        if self.budget_action not in {"full", "clipped", "dropped", "collapsed"}:
            raise ValueError("invalid compaction budget_action")
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("compaction token counts must be non-negative")
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))

    @property
    def record_id(self):
        return _digest(
            {
                "strategy": HISTORY_COMPACTION_STRATEGY,
                "source_index": self.source_index,
                "source_role": self.source_role,
                "source_tool": self.source_tool,
                "operation": self.operation,
                "budget_action": self.budget_action,
                "included": self.included,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "input_digest": self.input_digest,
                "output_digest": self.output_digest,
                "provenance": dict(self.provenance),
            }
        )

    def to_dict(self):
        return {
            "record_id": self.record_id,
            "source_index": self.source_index,
            "source_role": self.source_role,
            "source_tool": self.source_tool,
            "operation": self.operation,
            "budget_action": self.budget_action,
            "included": self.included,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class HistoryCompactionResult:
    raw: str
    rendered: str
    records: tuple[CompactionRecord, ...]
    recent_window: int
    recent_start: int

    @property
    def provenance_digest(self):
        return _digest([record.to_dict() for record in self.records])

    def details(self):
        rows = [record.to_dict() for record in self.records]
        return {
            "strategy": HISTORY_COMPACTION_STRATEGY,
            "provenance_digest": self.provenance_digest,
            "records": rows,
            "source_entry_count": len(self.records),
            "recent_window": self.recent_window,
            "recent_start": self.recent_start,
            "included_entry_count": sum(record.included for record in self.records),
            "dropped_entry_count": sum(
                record.budget_action in {"dropped", "collapsed"}
                for record in self.records
            ),
            "clipped_entry_count": sum(
                record.budget_action == "clipped" for record in self.records
            ),
            "collapsed_duplicate_reads": sum(
                record.operation == "collapse_duplicate_read"
                for record in self.records
            ),
            "reused_file_summary_count": sum(
                record.operation == "reuse_file_summary" for record in self.records
            ),
            "summarized_tool_count": sum(
                record.operation == "summarize_tool_output"
                for record in self.records
            ),
            "older_entries_count": sum(
                record.operation
                in {"reuse_file_summary", "summarize_tool_output"}
                for record in self.records
            ),
            "raw_digest": _digest(self.raw),
            "rendered_digest": _digest(self.rendered),
            "compaction_applied": any(
                record.operation != "retain_recent"
                or record.budget_action != "full"
                for record in self.records
            )
            or self.rendered != self.raw,
        }


class DeterministicHistoryCompactor:
    def __init__(self, token_counter, *, recent_window=6):
        if not isinstance(token_counter, TokenCounter):
            raise TypeError("history compactor requires a TokenCounter")
        if isinstance(recent_window, bool) or not isinstance(recent_window, int):
            raise TypeError("recent_window must be an integer")
        if recent_window < 1:
            raise ValueError("recent_window must be positive")
        self.token_counter = token_counter
        self.recent_window = recent_window

    def compact(self, history, budget, *, file_summary_lookup=None):
        history = [dict(item) for item in history]
        budget = int(budget)
        if budget < 0:
            raise ValueError("history budget must be non-negative")
        recent_start = max(0, len(history) - self.recent_window)
        raw = self._raw_history(history)
        if not history:
            return HistoryCompactionResult(
                raw=raw,
                rendered=self._clip(raw, budget),
                records=(),
                recent_window=self.recent_window,
                recent_start=recent_start,
            )
        candidates = []
        records = []
        seen_older_reads = {}

        for index, item in enumerate(history):
            candidate = self._candidate(
                index,
                item,
                recent=index >= recent_start,
                seen_older_reads=seen_older_reads,
                file_summary_lookup=file_summary_lookup,
            )
            if candidate["operation"] == "collapse_duplicate_read":
                records.append(self._record(candidate, (), "collapsed", False))
            else:
                candidates.append(candidate)

        rendered_entries = []
        for candidate in reversed(candidates):
            lines = list(candidate["lines"])
            action = "full"
            candidate_rendered = self._render(lines + rendered_entries)
            if self._count(candidate_rendered) > budget:
                if candidate["recent"]:
                    existing = self._render(rendered_entries)
                    available = max(0, budget - self._count(existing))
                    per_line = available // max(1, len(lines))
                    lines = [self._clip(line, per_line) for line in lines]
                else:
                    lines = [self._clip(line, 20) for line in lines]
                lines = [line for line in lines if line]
                action = "clipped"
                candidate_rendered = self._render(lines + rendered_entries)
            included = bool(lines) and self._count(candidate_rendered) <= budget
            if included:
                rendered_entries = lines + rendered_entries
            else:
                lines = []
                action = "dropped"
            records.append(self._record(candidate, lines, action, included))

        rendered = self._render(rendered_entries)
        if self._count(rendered) > budget:
            rendered = self._clip(raw, budget)
        records.sort(key=lambda record: record.source_index)
        return HistoryCompactionResult(
            raw=raw,
            rendered=rendered,
            records=tuple(records),
            recent_window=self.recent_window,
            recent_start=recent_start,
        )

    def raw_history(self, history):
        """Render a transcript without applying the compaction policy."""
        return self._raw_history([dict(item) for item in history])

    def _candidate(
        self,
        index,
        item,
        *,
        recent,
        seen_older_reads,
        file_summary_lookup,
    ):
        role = str(item.get("role", ""))
        tool = str(item.get("name", "")) if role == "tool" else ""
        provenance = {"created_at": str(item.get("created_at", ""))}
        if recent:
            return self._candidate_value(
                index, item, role, tool, "retain_recent", True,
                self._render_item(item, 900), provenance,
            )

        if role == "tool" and tool == "read_file":
            path = str(dict(item.get("args", {})).get("path", "")).strip()
            provenance["path"] = path
            if path in seen_older_reads:
                provenance["duplicate_of_index"] = seen_older_reads[path]
                return self._candidate_value(
                    index, item, role, tool, "collapse_duplicate_read", False,
                    (), provenance,
                )
            seen_older_reads[path] = index
            summary = file_summary_lookup(path) if file_summary_lookup else None
            if summary:
                summary = dict(summary)
                provenance.update(
                    {
                        "summary_source": "memory.file_summary",
                        "summary_created_at": str(summary.get("created_at", "")),
                        "summary_freshness": summary.get("freshness"),
                    }
                )
                return self._candidate_value(
                    index, item, role, tool, "reuse_file_summary", False,
                    (f"{path} -> {summary.get('summary', '')}",), provenance,
                )

        if role == "tool":
            provenance["summary_source"] = "deterministic.tool_output"
            return self._candidate_value(
                index, item, role, tool, "summarize_tool_output", False,
                (self._summarize_tool(item),), provenance,
            )
        return self._candidate_value(
            index, item, role, tool, "trim_old_message", False,
            self._render_item(item, 60), provenance,
        )

    def _candidate_value(
        self, index, item, role, tool, operation, recent, lines, provenance
    ):
        return {
            "index": index,
            "item": item,
            "role": role,
            "tool": tool,
            "operation": operation,
            "recent": recent,
            "lines": tuple(lines),
            "provenance": provenance,
        }

    def _record(self, candidate, lines, action, included):
        input_text = self._raw_item(candidate["item"])
        output_text = "\n".join(lines)
        return CompactionRecord(
            source_index=candidate["index"],
            source_role=candidate["role"],
            source_tool=candidate["tool"],
            operation=candidate["operation"],
            budget_action=action,
            included=included,
            input_tokens=self._count(input_text),
            output_tokens=self._count(output_text),
            input_digest=_digest(input_text),
            output_digest=_digest(output_text),
            provenance=candidate["provenance"],
        )

    def _raw_history(self, history):
        if not history:
            return "Transcript:\n- empty"
        lines = []
        for item in history:
            lines.extend(self._raw_item(item).splitlines())
        return self._render(lines)

    def _raw_item(self, item):
        if item.get("role") == "tool":
            args = json.dumps(item.get("args", {}), sort_keys=True, default=str)
            return f"[tool:{item.get('name', '')}] {args}\n{item.get('content', '')}"
        return f"[{item.get('role', '')}] {item.get('content', '')}"

    def _render_item(self, item, token_limit):
        if item.get("role") == "tool":
            args = json.dumps(item.get("args", {}), sort_keys=True, default=str)
            prefix = f"[tool:{item.get('name', '')}] {args}"
            return (prefix, self._clip(item.get("content", ""), token_limit))
        return (
            f"[{item.get('role', '')}] "
            f"{self._clip(item.get('content', ''), token_limit)}",
        )

    def _summarize_tool(self, item):
        if item.get("name") == "run_shell":
            command = str(dict(item.get("args", {})).get("command", "")).strip()
            lines = [
                line.strip()
                for line in str(item.get("content", "")).splitlines()
                if line.strip()
            ]
            return f"{command or 'shell'} -> {' | '.join(lines[:3]) or '(empty)'}"
        return self._render_item(item, 60)[0]

    @staticmethod
    def _render(lines):
        return "\n".join(["Transcript:", *lines])

    def _count(self, text):
        return self.token_counter.count(str(text))

    def _clip(self, text, token_limit):
        text = str(text)
        token_limit = int(token_limit)
        if token_limit <= 0:
            return ""
        if self._count(text) <= token_limit:
            return text
        suffix = "..."
        if self._count(suffix) > token_limit:
            return ""
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if self._count(text[:middle] + suffix) <= token_limit:
                low = middle
            else:
                high = middle - 1
        return text[:low] + suffix


__all__ = [
    "CompactionRecord",
    "DeterministicHistoryCompactor",
    "HISTORY_COMPACTION_STRATEGY",
    "HistoryCompactionResult",
]
