"""Durable directory-channel acceptance and idempotent outbound publication."""

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from .spine.ids import RequestId, TurnId


class ChannelReceipts:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS receipts (
                identity TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                scope TEXT NOT NULL, chat_id TEXT NOT NULL,
                turn_id TEXT UNIQUE NOT NULL, request_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved', content TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '')""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, message, scope):
        request = message.to_turn_request()
        identity = hashlib.sha256(
            json.dumps(
                [
                    scope,
                    message.channel,
                    message.chat_id,
                    message.sender_id,
                    message.message_id,
                ]
            ).encode()
        ).hexdigest()
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    message.session_id,
                    request.text,
                    message.work_class.value,
                ]
            ).encode()
        ).hexdigest()
        with self.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO receipts(identity,fingerprint,scope,chat_id,turn_id,request_id) VALUES(?,?,?,?,?,?)",
                (
                    identity,
                    fingerprint,
                    scope,
                    message.chat_id,
                    str(request.turn_id),
                    str(request.request_id),
                ),
            )
            created = cursor.rowcount == 1
            row = dict(
                db.execute(
                    "SELECT * FROM receipts WHERE identity=?", (identity,)
                ).fetchone()
            )
        if row["fingerprint"] != fingerprint:
            return None, None, False
        request = replace(
            request,
            turn_id=TurnId(row["turn_id"]),
            request_id=RequestId(row["request_id"]),
        )
        return row, request, created

    def rows(self, scope):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM receipts WHERE scope=? AND status NOT IN ('delivered','review') ORDER BY rowid",
                    (scope,),
                )
            ]

    def update(self, identity, **fields):
        if not fields or not set(fields) <= {
            "status",
            "content",
            "attempts",
            "next_attempt",
            "error",
        }:
            raise ValueError("invalid receipt update")
        with self.connect() as db:
            db.execute(
                "UPDATE receipts SET "
                + ",".join(f"{key}=?" for key in fields)
                + " WHERE identity=?",
                (*fields.values(), identity),
            )

    def summary(self):
        with self.connect() as db:
            counts = {
                row["status"]: row["n"]
                for row in db.execute(
                    "SELECT status,COUNT(*) AS n FROM receipts GROUP BY status"
                )
            }
            review = [
                dict(row)
                for row in db.execute(
                    "SELECT turn_id,status,attempts,error FROM receipts WHERE status='review' ORDER BY rowid",
                )
            ]
        return {"counts": counts, "review": review}

    def retry_delivery(self, turn_id):
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE receipts SET status='pending',attempts=0,next_attempt=0,error='' WHERE turn_id=? AND status='review' AND content<>''",
                (str(turn_id),),
            )
            return cursor.rowcount == 1


class DurableDirectoryDelivery:
    """One gateway-lease owner. Uncertain execution is never automatically replayed."""

    def __init__(self, host, channel, receipts, *, retry_base=1.0, max_attempts=8):
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("delivery attempt limit must be positive")
        self.host, self.channel, self.receipts = host, channel, receipts
        self.scope = str(channel.root.resolve())
        self.retry_base, self.max_attempts = retry_base, max_attempts
        self._task = None
        self._stopped = asyncio.Event()
        self.last_error = ""

    async def submit(self, message):
        row, request, created = self.receipts.reserve(message, self.scope)
        if row is None:
            return {"accepted": False, "reason": "identity_conflict"}
        events = self.host.agent.run_store.load_turn_events(request.turn_id)
        if not created and (events or row["status"] != "reserved"):
            return {"accepted": True, "duplicate": True, "turn_id": row["turn_id"]}
        result = await self.host.submit(
            message, request=request, dedup_key=row["identity"]
        )
        self.receipts.update(row["identity"], status="running")
        return result

    async def reconcile(self, *, startup=False):
        for row in self.receipts.rows(self.scope):
            if row["status"] in {"reserved", "running"}:
                try:
                    events = self.host.agent.run_store.load_turn_events(row["turn_id"])
                except (OSError, ValueError) as exc:
                    self.receipts.update(
                        row["identity"], status="review", error=type(exc).__name__
                    )
                    continue
                terminals = [
                    event
                    for event in events
                    if event["kind"]
                    in {"turn.completed", "turn.failed", "turn.cancelled"}
                ]
                if terminals:
                    outcome = terminals[-1]["payload"]
                    if outcome.get("error") == "interrupted by process restart":
                        self.receipts.update(
                            row["identity"], status="review", error="interrupted_execution"
                        )
                        continue
                    content = (
                        outcome.get("final_answer")
                        or f"Turn {row['turn_id']} ended: {outcome.get('state', 'failed')}."
                    )
                    self.receipts.update(
                        row["identity"], status="pending", content=content
                    )
                    row.update(status="pending", content=content)
                elif startup and events:
                    self.receipts.update(
                        row["identity"], status="review", error="interrupted_execution"
                    )
                    continue
                elif startup and row["status"] == "running":
                    self.receipts.update(
                        row["identity"], status="review", error="missing_turn_evidence"
                    )
                    continue
            if row["status"] != "pending" or row["next_attempt"] > time.time():
                continue
            if row["attempts"] >= self.max_attempts:
                self.receipts.update(
                    row["identity"],
                    status="review",
                    error="delivery_attempts_exhausted",
                )
                continue
            attempt = row["attempts"] + 1
            self.receipts.update(row["identity"], attempts=attempt)
            try:
                await self.channel.send_once(
                    row["turn_id"], row["chat_id"], row["content"]
                )
            except Exception as exc:
                self.receipts.update(
                    row["identity"],
                    status="review" if attempt >= self.max_attempts else "pending",
                    error=type(exc).__name__,
                    next_attempt=time.time()
                    + min(60, self.retry_base * 2 ** min(attempt - 1, 10)),
                )
            else:
                self.receipts.update(row["identity"], status="delivered", error="")

    async def start(self):
        if self._task is not None:
            return False
        await self.reconcile(startup=True)
        self._stopped.clear()
        self._task = asyncio.create_task(self._run())
        return True

    async def _run(self):
        while not self._stopped.is_set():
            try:
                await self.reconcile()
            except Exception as exc:
                self.last_error = type(exc).__name__
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=0.1)
            except asyncio.TimeoutError:
                pass

    async def stop(self):
        self._stopped.set()
        if self._task is not None:
            await self._task
            self._task = None
