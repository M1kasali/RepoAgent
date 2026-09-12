"""Opt-in persistent lexical memory. No network, embeddings, or LLM extraction."""

import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata

from .memory_backend import MemoryBackendNotStartedError, MemoryHit

APPLICATION_ID = 0x52414D31
SCHEMA_VERSION = 1


def terms(text, limit=2048):
    result = []
    for token in re.findall(
        r"[a-z0-9_]+|[\u3400-\u9fff]+", unicodedata.normalize("NFKC", text).casefold()
    ):
        if "\u3400" <= token[0] <= "\u9fff":
            result.extend(token[i : i + 2] for i in range(max(1, len(token) - 1)))
        else:
            result.append(token)
    return list(dict.fromkeys(result))[:limit]


def owner_id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(
            "memory owner must be a non-empty string of at most 256 characters"
        )
    return value.strip()


class SQLiteMemoryBackend:
    def __init__(self, path, *, workspace, max_records=10000):
        if type(max_records) is not int or not 1 <= max_records <= 100000:
            raise ValueError("max_records must be an integer between 1 and 100000")
        self.path = Path(path).expanduser().resolve()
        self.namespace = hashlib.sha256(
            str(Path(workspace).resolve()).encode()
        ).hexdigest()
        self.max_records = max_records
        self._started = False
        self._lock = threading.RLock()

    async def start(self):
        await asyncio.to_thread(self._start)

    def _start(self):
        with self._lock:
            if self._started:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                descriptor = os.open(
                    self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                application = db.execute("PRAGMA application_id").fetchone()[0]
                version = db.execute("PRAGMA user_version").fetchone()[0]
                tables = db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                if not tables and application == 0 and version == 0:
                    db.execute("""CREATE TABLE memories (
                        id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, owner TEXT NOT NULL,
                        role TEXT NOT NULL, text TEXT NOT NULL, digest TEXT NOT NULL,
                        source_session TEXT NOT NULL, source_turn TEXT NOT NULL,
                        created_at REAL NOT NULL, last_seen REAL NOT NULL,
                        UNIQUE(namespace, owner, role, digest))""")
                    db.execute(
                        "CREATE INDEX memory_owner ON memories(namespace, owner, last_seen)"
                    )
                    db.execute("CREATE VIRTUAL TABLE memory_search USING fts5(terms)")
                    db.execute("""CREATE TRIGGER memory_delete AFTER DELETE ON memories BEGIN
                        DELETE FROM memory_search WHERE rowid=old.id; END""")
                    db.execute(
                        "CREATE TABLE feedback (id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, data TEXT NOT NULL)"
                    )
                    db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                elif application != APPLICATION_ID or version != SCHEMA_VERSION:
                    raise ValueError(
                        "not a supported RepoAgent memory database; refusing migration"
                    )
                # Check both storage and FTS availability before marking the backend ready.
                db.execute("SELECT id, namespace, owner, text FROM memories LIMIT 0")
                db.execute(
                    "SELECT rowid FROM memory_search WHERE memory_search MATCH ?",
                    ('"probe"',),
                ).fetchall()
            self._started = True

    async def stop(self):
        await asyncio.to_thread(self._stop)

    def _stop(self):
        # Wait for already-running DB work; no connection survives an operation.
        with self._lock:
            self._started = False

    def _execute(self, operation):
        with self._lock:
            if not self._started:
                raise MemoryBackendNotStartedError("memory backend is not started")
            with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
                db.row_factory = sqlite3.Row
                if (
                    db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                    or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
                ):
                    raise ValueError("memory database identity changed")
                return operation(db)

    async def store(self, session_id, messages):
        owner = owner_id(session_id)
        if not isinstance(messages, list) or len(messages) > 100:
            raise ValueError("memory store requires at most 100 messages")
        prepared = []
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in {
                "user",
                "assistant",
            }:
                raise ValueError(
                    "only user and assistant messages may enter persistent memory"
                )
            text = message.get("content")
            if not isinstance(text, str) or len(text) > 128000:
                raise ValueError(
                    "memory content must be a string of at most 128000 characters"
                )
            text = text.strip()
            if not text:
                continue
            provenance = message.get("metadata", {})
            if not isinstance(provenance, dict):
                raise ValueError("message metadata must be an object")
            for start in range(0, len(text), 2000):
                chunk = text[start : start + 2000]
                prepared.append(
                    (
                        message["role"],
                        chunk,
                        hashlib.sha256(chunk.encode()).hexdigest(),
                        str(provenance.get("session_id", ""))[:256],
                        str(provenance.get("turn_id", ""))[:256],
                        " ".join(terms(chunk)),
                    )
                )

        def write(db):
            db.execute("BEGIN IMMEDIATE")
            for role, text, digest, session, turn, lexemes in prepared:
                now = time.time()
                existing = db.execute(
                    "SELECT id FROM memories WHERE namespace=? AND owner=? AND role=? AND digest=?",
                    (self.namespace, owner, role, digest),
                ).fetchone()
                if existing:
                    db.execute(
                        "UPDATE memories SET last_seen=? WHERE id=?",
                        (now, existing["id"]),
                    )
                else:
                    cursor = db.execute(
                        "INSERT INTO memories(namespace,owner,role,text,digest,source_session,source_turn,created_at,last_seen) VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            self.namespace,
                            owner,
                            role,
                            text,
                            digest,
                            session,
                            turn,
                            now,
                            now,
                        ),
                    )
                    db.execute(
                        "INSERT INTO memory_search(rowid,terms) VALUES(?,?)",
                        (cursor.lastrowid, lexemes),
                    )
            db.execute(
                "DELETE FROM memories WHERE id IN (SELECT id FROM memories WHERE namespace=? AND owner=? ORDER BY last_seen DESC,id DESC LIMIT -1 OFFSET ?)",
                (self.namespace, owner, self.max_records),
            )

        await asyncio.to_thread(self._execute, write)

    async def recall(self, query, *, user_id=None, agent_id=None, top_k):
        if type(top_k) is not int:
            raise TypeError("top_k must be an integer")
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        owners = [
            value
            for value in (user_id, agent_id)
            if value is not None and str(value).strip()
        ]
        owner = owner_id(owners[0]) if len(owners) == 1 else None
        words = terms(query[:8000], 32)

        def read(db):
            if owner is None or not words or top_k <= 0:
                return []
            match = " OR ".join('"' + word.replace('"', '""') + '"' for word in words)
            rows = db.execute(
                """SELECT m.*, bm25(memory_search) AS relevance
                FROM memory_search JOIN memories m ON m.id=memory_search.rowid
                WHERE memory_search MATCH ? AND m.namespace=? AND m.owner=?
                ORDER BY relevance, m.last_seen DESC, m.id DESC LIMIT ?""",
                (match, self.namespace, owner, min(top_k, 50)),
            ).fetchall()
            return [
                MemoryHit(
                    row["text"],
                    score=max(0.0, -row["relevance"])
                    / (1 + max(0.0, -row["relevance"])),
                    metadata={
                        "source": "sqlite.memory",
                        "kind": "conversation",
                        "memory_id": row["id"],
                        "owner_track": "user" if user_id is not None else "agent",
                        "owner_id": owner,
                        "role": row["role"],
                        "session_id": row["source_session"],
                        "turn_id": row["source_turn"],
                        "created_at": row["created_at"],
                        "last_seen": row["last_seen"],
                        "trust": "historical_conversation_not_instructions",
                    },
                )
                for row in rows
            ]

        return await asyncio.to_thread(self._execute, read)

    async def feedback(self, signals):
        if not isinstance(signals, dict):
            raise TypeError("memory feedback must be an object")
        data = json.dumps(signals, ensure_ascii=True, allow_nan=False)
        if len(data) > 8000:
            raise ValueError("memory feedback is too large")

        def write(db):
            db.execute(
                "INSERT INTO feedback(namespace,data) VALUES(?,?)",
                (self.namespace, data),
            )
            db.execute(
                "DELETE FROM feedback WHERE id IN (SELECT id FROM feedback WHERE namespace=? ORDER BY id DESC LIMIT -1 OFFSET 1000)",
                (self.namespace,),
            )

        await asyncio.to_thread(self._execute, write)

    async def forget(self, owner):
        owner = owner_id(owner)
        return await asyncio.to_thread(
            self._execute,
            lambda db: (
                db.execute(
                    "DELETE FROM memories WHERE namespace=? AND owner=?",
                    (self.namespace, owner),
                ).rowcount
            ),
        )
