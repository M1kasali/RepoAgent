"""Persistent scheduled work with atomic claims, deduplication, and outcomes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from pathlib import Path
import time
from uuid import uuid4

from .atomic_io import atomic_replace_unlocked, file_lock
from .channels import ChannelMessage
from .spine import WorkClass


CRON_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CronSchedule:
    kind: str
    at: float | None = None
    every_seconds: float | None = None

    def __post_init__(self):
        if self.kind not in {"at", "every"}:
            raise ValueError("cron schedule kind must be at or every")
        value = self.at if self.kind == "at" else self.every_seconds
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value <= 0
        ):
            raise ValueError("cron schedule value must be positive")
        if self.kind == "at" and self.every_seconds is not None:
            raise ValueError("at schedule cannot define every_seconds")
        if self.kind == "every" and self.at is not None:
            raise ValueError("every schedule cannot define at")

    def first_run(self, now):
        return float(self.at) if self.kind == "at" else float(now) + float(self.every_seconds)


@dataclass
class CronJob:
    job_id: str
    name: str
    schedule: CronSchedule
    prompt: str
    channel: str
    chat_id: str
    enabled: bool
    next_run_at: float
    dedup_key: str
    created_at: float
    updated_at: float
    claim_owner: str = ""
    claim_expires_at: float = 0.0
    run_count: int = 0
    last_outcome: dict = field(default_factory=dict)

    def to_dict(self):
        payload = asdict(self)
        payload["schedule"] = asdict(self.schedule)
        return payload

    @classmethod
    def from_dict(cls, payload):
        values = dict(payload)
        values["schedule"] = CronSchedule(**values["schedule"])
        return cls(**values)


class CronStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock_path = self.path.parent / ".lock" / f"{self.path.name}.lock"

    def load(self):
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != CRON_SCHEMA_VERSION:
            raise ValueError("unsupported cron store schema")
        return [CronJob.from_dict(item) for item in payload.get("jobs", [])]

    def transaction(self, update):
        with file_lock(self.lock_path):
            jobs = self.load()
            result = update(jobs)
            atomic_replace_unlocked(
                self.path,
                json.dumps(
                    {
                        "schema_version": CRON_SCHEMA_VERSION,
                        "jobs": [job.to_dict() for job in jobs],
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
        return result


class CronService:
    def __init__(self, path, *, claim_ttl=300.0, clock=time.time):
        self.store = CronStore(path)
        self.claim_ttl = float(claim_ttl)
        if self.claim_ttl <= 0:
            raise ValueError("cron claim TTL must be positive")
        self.clock = clock
        self._jobs = []
        self._mtime_ns = -1
        self.reload(force=True)

    def reload(self, *, force=False):
        mtime_ns = self.store.path.stat().st_mtime_ns if self.store.path.exists() else 0
        if force or mtime_ns != self._mtime_ns:
            self._jobs = self.store.load()
            self._mtime_ns = mtime_ns
            return True
        return False

    def list(self):
        self.reload()
        return tuple(self._jobs)

    @staticmethod
    def _dedup(schedule, prompt, channel, chat_id):
        payload = json.dumps(
            {
                "schedule": asdict(schedule),
                "prompt": prompt,
                "channel": channel,
                "chat_id": chat_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def add(self, name, schedule, prompt, *, channel, chat_id):
        now = self.clock()
        dedup_key = self._dedup(schedule, prompt, channel, chat_id)

        def update(jobs):
            for job in jobs:
                if job.enabled and job.dedup_key == dedup_key:
                    return job, True
            job = CronJob(
                job_id="cron_" + uuid4().hex,
                name=str(name),
                schedule=schedule,
                prompt=str(prompt),
                channel=str(channel),
                chat_id=str(chat_id),
                enabled=True,
                next_run_at=schedule.first_run(now),
                dedup_key=dedup_key,
                created_at=now,
                updated_at=now,
            )
            jobs.append(job)
            return job, False

        result = self.store.transaction(update)
        self.reload(force=True)
        return result

    def claim_due(self, owner, *, limit=10):
        now = self.clock()
        owner = str(owner)
        if not owner:
            raise ValueError("cron claim owner must not be empty")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("cron claim limit must be a positive integer")

        def update(jobs):
            claimed = []
            for job in sorted(jobs, key=lambda item: (item.next_run_at, item.job_id)):
                if len(claimed) >= int(limit):
                    break
                if not job.enabled or job.next_run_at > now:
                    continue
                if job.claim_owner and job.claim_expires_at > now:
                    continue
                job.claim_owner = owner
                job.claim_expires_at = now + self.claim_ttl
                job.updated_at = now
                claimed.append(CronJob.from_dict(job.to_dict()))
            return claimed

        result = self.store.transaction(update)
        self.reload(force=True)
        return tuple(result)

    def complete(self, job_id, owner, outcome):
        now = self.clock()

        def update(jobs):
            job = next((item for item in jobs if item.job_id == job_id), None)
            if job is None:
                raise KeyError(f"unknown cron job: {job_id}")
            if job.claim_owner != owner:
                raise ValueError("cron completion owner does not hold the claim")
            job.run_count += 1
            job.last_outcome = dict(outcome)
            job.claim_owner = ""
            job.claim_expires_at = 0.0
            job.updated_at = now
            if job.schedule.kind == "at":
                job.enabled = False
            else:
                interval = float(job.schedule.every_seconds)
                job.next_run_at += interval
                while job.next_run_at <= now:
                    job.next_run_at += interval
            return CronJob.from_dict(job.to_dict())

        result = self.store.transaction(update)
        self.reload(force=True)
        return result

    async def run_due(self, host, channels, *, owner, limit=10):
        results = []
        for job in self.claim_due(owner, limit=limit):
            channel = channels.get(job.channel)
            if channel is None:
                outcome = {"status": "delivery_unavailable", "channel": job.channel}
            else:
                try:
                    accepted = await host.submit(
                        ChannelMessage(
                            channel=job.channel,
                            chat_id=job.chat_id,
                            sender_id="cron",
                            text=job.prompt,
                            message_id=f"{job.job_id}:{job.run_count + 1}",
                            conversation=f"cron:{job.job_id}",
                            work_class=WorkClass.BACKGROUND,
                        ),
                        deliver=channel.send,
                    )
                    turn = await host.wait(accepted["turn_id"])
                    outcome = {
                        "status": turn.state.value,
                        "turn_id": str(turn.turn_id),
                        "error": turn.error or "",
                    }
                except Exception as exc:
                    outcome = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            results.append(self.complete(job.job_id, owner, outcome))
        return tuple(results)


__all__ = ["CRON_SCHEMA_VERSION", "CronJob", "CronSchedule", "CronService", "CronStore"]
