"""Crash-safe standard-library persistence primitives."""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


class StorageCorruptionError(ValueError):
    pass


@contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _fsync_directory(path):
    if os.name == "nt":
        return
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_replace_unlocked(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(path.parent),
            prefix=path.name + ".",
            suffix=".tmp",
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, path)
        _fsync_directory(path.parent)
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def atomic_replace(path, text, *, lock_path=None):
    path = Path(path)
    lock_path = Path(lock_path or path.parent / ".lock" / f"{path.name}.lock")
    with file_lock(lock_path):
        atomic_replace_unlocked(path, text)


def read_jsonl_unlocked(path, *, repair_tail=False):
    path = Path(path)
    if not path.exists():
        return []
    payload = path.read_bytes()
    complete = payload
    if payload and not payload.endswith(b"\n"):
        boundary = payload.rfind(b"\n")
        complete = payload[: boundary + 1] if boundary >= 0 else b""
        if not repair_tail:
            raise StorageCorruptionError(f"incomplete JSONL tail: {path}")
        with path.open("r+b") as handle:
            handle.truncate(len(complete))
            handle.flush()
            os.fsync(handle.fileno())
    try:
        text = complete.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StorageCorruptionError(f"invalid UTF-8 in JSONL file: {path}") from exc
    records = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise StorageCorruptionError(
                f"invalid JSONL record at {path}:{line_number}"
            ) from exc
    return records


def append_jsonl_unlocked(path, payload, *, validator=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = read_jsonl_unlocked(path, repair_tail=True)
    if validator is not None:
        validator(records, payload)
    encoded = (json.dumps(payload, sort_keys=True, ensure_ascii=True) + "\n").encode(
        "utf-8"
    )
    with path.open("ab") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return records


def append_jsonl(path, payload, *, validator=None, lock_path=None):
    path = Path(path)
    lock_path = Path(lock_path or path.parent / ".lock" / f"{path.name}.lock")
    with file_lock(lock_path):
        return append_jsonl_unlocked(path, payload, validator=validator)
