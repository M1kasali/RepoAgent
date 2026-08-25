"""运行工件落盘。

session.json 负责保存“可恢复的会话状态”；RunStore 负责保存“单次运行的审计工件”，
例如 task_state、trace 和 report。两者分开后，恢复现场和复盘证据不会混在一起。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .atomic_io import (
    StorageCorruptionError,
    append_jsonl,
    append_jsonl_unlocked,
    atomic_replace,
    atomic_replace_unlocked,
    file_lock,
    read_jsonl_unlocked,
)


TURN_FORMAT_VERSION = 1
TERMINAL_EVENT_KINDS = {"turn.completed", "turn.failed", "turn.cancelled"}


class DuplicateTerminalEventError(StorageCorruptionError):
    pass


class TurnEventSequenceError(StorageCorruptionError):
    pass


def _run_id(value):
    if hasattr(value, "run_id"):
        return value.run_id
    return str(value)


class RunStore:
    def __init__(self, root, *, redactor=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._redactor = redactor or (lambda value: value)

    def set_redactor(self, redactor):
        if not callable(redactor):
            raise TypeError("RunStore redactor must be callable")
        self._redactor = redactor

    def run_dir(self, run_id):
        return self.root / _run_id(run_id)

    def task_state_path(self, run_id):
        return self.run_dir(run_id) / "task_state.json"

    def trace_path(self, run_id):
        return self.run_dir(run_id) / "trace.jsonl"

    def report_path(self, run_id):
        return self.run_dir(run_id) / "report.json"

    def call_ledger_path(self, run_id):
        return self.run_dir(run_id) / "calls.jsonl"

    def turn_path(self, turn_id):
        return self.run_dir(turn_id) / "turn.json"

    def turn_events_path(self, turn_id):
        return self.run_dir(turn_id) / "turn_events.jsonl"

    def turn_lock_path(self, turn_id):
        return self.run_dir(turn_id) / ".lock" / "turn.lock"

    def start_run(self, task_state):
        # 每次 ask() 都会生成一个 run 目录。
        # 这样一次用户请求对应一组独立工件，后续排查更容易。
        run_dir = self.run_dir(task_state)
        run_dir.mkdir(parents=True, exist_ok=True)
        self.write_task_state(task_state)
        return run_dir

    def write_task_state(self, task_state):
        path = self.task_state_path(task_state)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(path, self._redactor(task_state.to_dict()))
        return path

    def append_trace(self, task_state, event):
        path = self.trace_path(task_state)
        path.parent.mkdir(parents=True, exist_ok=True)
        # trace 采用 jsonl 追加写入，原因是 agent 运行过程是流式事件序列，
        # 逐条落盘比“最后一次性写整份 trace”更稳，也更适合调试。
        append_jsonl(path, self._redactor(event))
        return path

    def append_model_call(self, task_state, record):
        path = self.call_ledger_path(task_state)
        path.parent.mkdir(parents=True, exist_ok=True)
        append_jsonl(path, self._redactor(record))
        return path

    def load_model_calls(self, run_id):
        path = self.call_ledger_path(run_id)
        return read_jsonl_unlocked(path) if path.exists() else []

    def write_report(self, task_state, report):
        path = self.report_path(task_state)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_atomic(path, self._redactor(report))
        return path

    def write_turn(self, turn_id, payload):
        path = self.turn_path(turn_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_replace(
            path,
            json.dumps(self._redactor(payload), indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            lock_path=self.turn_lock_path(turn_id),
        )
        return path

    def append_turn_event(self, turn_id, event):
        return self.commit_turn_event(turn_id, event)

    def commit_turn_event(self, turn_id, event, turn_snapshot=None):
        path = self.turn_events_path(turn_id)
        event = self._redactor(event)
        turn_snapshot = (
            self._redactor(turn_snapshot) if turn_snapshot is not None else None
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.turn_lock_path(turn_id)):
            append_jsonl_unlocked(
                path,
                event,
                validator=self._validate_turn_event,
            )
            if turn_snapshot is not None:
                atomic_replace_unlocked(
                    self.turn_path(turn_id),
                    json.dumps(
                        turn_snapshot, indent=2, sort_keys=True, ensure_ascii=True
                    )
                    + "\n",
                )
        return path

    def load_turn_events(self, turn_id, *, repair_tail=False):
        with file_lock(self.turn_lock_path(turn_id)):
            records = read_jsonl_unlocked(
                self.turn_events_path(turn_id), repair_tail=repair_tail
            )
            self._validate_event_history(records)
            return records

    def recover_incomplete_turns(self):
        recovered = []
        for run_dir in sorted(path for path in self.root.iterdir() if path.is_dir()):
            turn_id = run_dir.name
            event_path = self.turn_events_path(turn_id)
            snapshot_path = self.turn_path(turn_id)
            if not event_path.exists() and not snapshot_path.exists():
                continue
            with file_lock(self.turn_lock_path(turn_id)):
                records = read_jsonl_unlocked(event_path, repair_tail=True)
                snapshot = self._load_optional_json(snapshot_path)
                self._validate_snapshot(snapshot, turn_id, records)
                records = self._seed_missing_history(records, snapshot)
                self._validate_event_history(records)
                terminal = [
                    record for record in records if record["kind"] in TERMINAL_EVENT_KINDS
                ]
                if terminal:
                    projection = self._project_turn(records, snapshot)
                    atomic_replace_unlocked(
                        snapshot_path,
                        json.dumps(
                            projection, indent=2, sort_keys=True, ensure_ascii=True
                        )
                        + "\n",
                    )
                    continue
                if not records:
                    continue
                failed_event, projection = self._recovery_failure(records, snapshot)
                append_jsonl_unlocked(
                    event_path,
                    failed_event,
                    validator=self._validate_turn_event,
                )
                atomic_replace_unlocked(
                    snapshot_path,
                    json.dumps(
                        projection, indent=2, sort_keys=True, ensure_ascii=True
                    )
                    + "\n",
                )
                recovered.append(turn_id)
        return recovered

    def load_task_state(self, task_id):
        return json.loads(self.task_state_path(task_id).read_text(encoding="utf-8"))

    def load_report(self, task_id):
        return json.loads(self.report_path(task_id).read_text(encoding="utf-8"))

    def query_trace(
        self,
        run_id,
        *,
        events=(),
        stages=(),
        provider_call_id="",
        tool_call_id="",
        limit=None,
    ):
        """Query legacy execution trace rows without exposing storage paths."""
        event_filter = {str(value) for value in events}
        stage_filter = {str(value) for value in stages}
        path = self.trace_path(run_id)
        if not path.exists():
            return []
        rows = read_jsonl_unlocked(path)
        selected = []
        for row in rows:
            if event_filter and str(row.get("event", "")) not in event_filter:
                continue
            if stage_filter and str(row.get("stage", "")) not in stage_filter:
                continue
            if provider_call_id and row.get("provider_call_id") != provider_call_id:
                continue
            if tool_call_id and row.get("tool_call_id") != tool_call_id:
                continue
            selected.append(row)
        if limit is not None:
            if isinstance(limit, bool) or int(limit) < 0:
                raise ValueError("trace query limit must be non-negative")
            selected = selected[-int(limit) :]
        return selected

    def query_turn_events(self, turn_id, *, kinds=(), stages=(), limit=None):
        kind_filter = {str(value) for value in kinds}
        stage_filter = {str(value) for value in stages}
        rows = self.load_turn_events(turn_id)
        selected = [
            row
            for row in rows
            if (not kind_filter or row.get("kind") in kind_filter)
            and (not stage_filter or row.get("stage") in stage_filter)
        ]
        if limit is not None:
            if isinstance(limit, bool) or int(limit) < 0:
                raise ValueError("Turn event query limit must be non-negative")
            selected = selected[-int(limit) :]
        return selected

    def export_trace(self, run_id):
        """Return a self-describing in-memory export for inspection tooling."""
        return {
            "format_version": 1,
            "run_id": _run_id(run_id),
            "turn_events": (
                self.load_turn_events(run_id)
                if self.turn_events_path(run_id).exists()
                else []
            ),
            "trace": self.query_trace(run_id),
            "model_calls": (
                read_jsonl_unlocked(self.call_ledger_path(run_id))
                if self.call_ledger_path(run_id).exists()
                else []
            ),
        }

    def apply_retention(self, *, keep_latest=100, older_than=None, protected=()):
        """Delete old completed run directories and return their IDs."""
        if isinstance(keep_latest, bool) or int(keep_latest) < 0:
            raise ValueError("keep_latest must be non-negative")
        protected_ids = {str(value) for value in protected}
        candidates = sorted(
            (path for path in self.root.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        removed = []
        for index, path in enumerate(candidates):
            if index < int(keep_latest) or path.name in protected_ids:
                continue
            if older_than is not None and path.stat().st_mtime >= float(older_than):
                continue
            if not self._run_is_terminal(path.name):
                continue
            self._remove_tree(path)
            removed.append(path.name)
        return removed

    def _run_is_terminal(self, run_id):
        event_path = self.turn_events_path(run_id)
        if event_path.exists():
            rows = read_jsonl_unlocked(event_path)
            return bool(rows and rows[-1].get("kind") in TERMINAL_EVENT_KINDS)
        report_path = self.report_path(run_id)
        if not report_path.exists():
            return False
        report = self._load_optional_json(report_path) or {}
        return str(report.get("status", "")) in {
            "completed",
            "stopped",
            "failed",
            "cancelled",
        }

    @classmethod
    def _remove_tree(cls, path):
        for child in path.iterdir():
            if child.is_dir():
                cls._remove_tree(child)
            else:
                child.unlink()
        path.rmdir()

    def _write_json_atomic(self, path, payload):
        # 原子写：先写临时文件，再 replace。
        # 这样即使中途异常，也不容易留下半截 JSON。
        atomic_replace(
            path,
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        )

    @staticmethod
    def _validate_turn_event(records, event):
        if int(event.get("format_version", 0)) != TURN_FORMAT_VERSION:
            raise StorageCorruptionError("unsupported Turn event format version")
        expected_sequence = len(records) + 1
        if int(event.get("sequence", 0)) != expected_sequence:
            raise TurnEventSequenceError(
                f"expected Turn event sequence {expected_sequence}, "
                f"found {event.get('sequence')}"
            )
        if records:
            first = records[0]
            for key in ("turn_id", "session_id", "request_id"):
                if event.get(key) != first.get(key):
                    raise StorageCorruptionError(
                        f"Turn event correlation mismatch for {key}"
                    )
            if any(record.get("kind") in TERMINAL_EVENT_KINDS for record in records):
                raise DuplicateTerminalEventError("cannot append after a terminal Turn event")
            if event.get("event_id") in {record.get("event_id") for record in records}:
                raise StorageCorruptionError("duplicate Turn event id")
        elif event.get("kind") != "turn.accepted":
            raise TurnEventSequenceError("the first Turn event must be turn.accepted")

    @classmethod
    def _validate_event_history(cls, records):
        accepted = []
        for record in records:
            cls._validate_turn_event(accepted, record)
            accepted.append(record)

    @staticmethod
    def _load_optional_json(path):
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageCorruptionError(f"invalid Turn snapshot: {path}") from exc
        if not isinstance(payload, dict):
            raise StorageCorruptionError(f"Turn snapshot must be an object: {path}")
        try:
            version = int(payload.get("format_version", 0))
        except (TypeError, ValueError) as exc:
            raise StorageCorruptionError(
                f"invalid Turn snapshot format version: {path}"
            ) from exc
        if version not in {0, TURN_FORMAT_VERSION}:
            raise StorageCorruptionError(
                f"unsupported Turn snapshot format version {version}: {path}"
            )
        return payload

    @staticmethod
    def _validate_snapshot(snapshot, expected_turn_id, records):
        if snapshot is None:
            return
        if str(snapshot.get("turn_id", "")) != str(expected_turn_id):
            raise StorageCorruptionError(
                f"Turn snapshot id does not match its run directory: {expected_turn_id}"
            )
        if not records:
            return
        first = records[0]
        for key in ("turn_id", "session_id", "request_id"):
            if str(snapshot.get(key, "")) != str(first.get(key, "")):
                raise StorageCorruptionError(
                    f"Turn snapshot correlation mismatch for {key}"
                )

    def _seed_missing_history(self, records, snapshot):
        if records or not snapshot:
            return records
        turn_id = str(snapshot.get("turn_id", ""))
        session_id = str(snapshot.get("session_id", ""))
        request_id = str(snapshot.get("request_id", ""))
        if not all((turn_id, session_id, request_id)):
            raise StorageCorruptionError("Turn snapshot is missing correlation ids")
        accepted = self._make_event(
            "turn.accepted",
            turn_id,
            session_id,
            request_id,
            1,
            {"request": dict(snapshot.get("request") or {})},
            trace_source=dict(snapshot.get("trace_context") or {}),
            trace_stage="scheduler",
        )
        append_jsonl_unlocked(
            self.turn_events_path(turn_id),
            accepted,
            validator=self._validate_turn_event,
        )
        records = [accepted]
        state = str(snapshot.get("state", ""))
        if state in {"completed", "failed", "cancelled"}:
            terminal = self._make_event(
                f"turn.{state}",
                turn_id,
                session_id,
                request_id,
                2,
                dict(snapshot.get("outcome") or {}),
                trace_source=dict(snapshot.get("trace_context") or {}),
                trace_stage="delivery",
            )
            append_jsonl_unlocked(
                self.turn_events_path(turn_id),
                terminal,
                validator=self._validate_turn_event,
            )
            records.append(terminal)
        return records

    @staticmethod
    def _project_turn(records, snapshot):
        first = records[0]
        terminal = next(
            record for record in reversed(records) if record["kind"] in TERMINAL_EVENT_KINDS
        )
        outcome = dict(terminal.get("payload") or {})
        request = dict(first.get("payload", {}).get("request") or {})
        if not request and snapshot:
            request = dict(snapshot.get("request") or {})
        return {
            "format_version": TURN_FORMAT_VERSION,
            "turn_id": first["turn_id"],
            "session_id": first["session_id"],
            "request_id": first["request_id"],
            "state": terminal["kind"].removeprefix("turn."),
            "request": request,
            "outcome": outcome,
        }

    def _recovery_failure(self, records, snapshot):
        first = records[0]
        error = "interrupted by process restart"
        outcome = {
            "turn_id": first["turn_id"],
            "request_id": first["request_id"],
            "session_id": first["session_id"],
            "state": "failed",
            "final_answer": "",
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "explicit_reply": False,
            "tool_calls": 0,
            "tool_failures": 0,
            "error": error,
        }
        event = self._make_event(
            "turn.failed",
            first["turn_id"],
            first["session_id"],
            first["request_id"],
            len(records) + 1,
            outcome,
            trace_source=first,
            trace_stage="delivery",
        )
        request = dict(first.get("payload", {}).get("request") or {})
        if not request and snapshot:
            request = dict(snapshot.get("request") or {})
        projection = {
            "format_version": TURN_FORMAT_VERSION,
            "turn_id": first["turn_id"],
            "session_id": first["session_id"],
            "request_id": first["request_id"],
            "state": "failed",
            "request": request,
            "outcome": outcome,
        }
        return event, projection

    @staticmethod
    def _make_event(
        kind,
        turn_id,
        session_id,
        request_id,
        sequence,
        payload,
        *,
        trace_source=None,
        trace_stage="",
    ):
        event = {
            "format_version": TURN_FORMAT_VERSION,
            "event_id": "event_recovery_" + uuid4().hex,
            "kind": kind,
            "turn_id": turn_id,
            "session_id": session_id,
            "request_id": request_id,
            "sequence": sequence,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        source = dict(trace_source or {})
        if source.get("trace_id"):
            event.update(
                {
                    "trace_id": source["trace_id"],
                    "span_id": source.get("span_id", ""),
                    "parent_span_id": source.get("parent_span_id", ""),
                    "stage": trace_stage or source.get("stage", "runtime"),
                }
            )
        return event
