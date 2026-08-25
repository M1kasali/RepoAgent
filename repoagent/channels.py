"""Channel intake, delivery, media normalization, and a filesystem adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Protocol, runtime_checkable

from .atomic_io import atomic_replace
from .spine import TurnRequest, WorkClass


def _safe_name(name):
    return os.path.basename(str(name or "")) or "file"


@dataclass(frozen=True)
class MediaAttachment:
    path: str
    mime: str = "application/octet-stream"
    kind: str = "file"
    size_bytes: int = 0
    sha256: str = ""


def save_media_bytes(
    root,
    channel,
    data,
    name,
    *,
    mime="application/octet-stream",
    max_bytes=20 * 1024 * 1024,
):
    data = bytes(data)
    if len(data) > int(max_bytes):
        raise ValueError("channel media exceeds the configured size limit")
    digest = hashlib.sha256(data).hexdigest()
    target = Path(root) / _safe_name(channel) / f"{digest[:16]}_{_safe_name(name)}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        target.write_bytes(data)
    return MediaAttachment(
        path=str(target),
        mime=str(mime),
        size_bytes=len(data),
        sha256="sha256:" + digest,
    )


@dataclass(frozen=True)
class ChannelMessage:
    channel: str
    chat_id: str
    sender_id: str
    text: str
    message_id: str
    conversation: str = ""
    media: tuple[MediaAttachment, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    work_class: WorkClass = WorkClass.FOREGROUND

    def __post_init__(self):
        if not all((self.channel, self.chat_id, self.sender_id, self.message_id)):
            raise ValueError("channel message identity must not be empty")
        if not self.text.strip() and not self.media:
            raise ValueError("channel message requires text or media")
        object.__setattr__(self, "media", tuple(self.media))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def session_id(self):
        return self.conversation or f"{self.channel}:{self.chat_id}"

    @property
    def dedup_key(self):
        return f"{self.channel}:{self.message_id}"

    def to_turn_request(self):
        media_note = "\n".join(
            f"[attachment: {item.kind} {item.mime} {item.path}]" for item in self.media
        )
        text = self.text.strip()
        if media_note:
            text = f"{text}\n\n{media_note}".strip()
        return TurnRequest.create(
            session_id=self.session_id,
            text=text,
            work_class=self.work_class,
        )


@dataclass(frozen=True)
class DeliveryResult:
    channel: str
    chat_id: str
    status: str
    error: str = ""


@runtime_checkable
class Channel(Protocol):
    name: str

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send(self, chat_id: str, content: str, media=()) -> None: ...


@runtime_checkable
class Transcriber(Protocol):
    async def transcribe(self, attachment: MediaAttachment) -> str: ...


async def normalize_media(attachments, *, transcriber=None):
    attachments = tuple(attachments)
    transcripts = []
    for attachment in attachments:
        if attachment.kind != "audio":
            continue
        if transcriber is None:
            transcripts.append(
                {"path": attachment.path, "status": "not_configured", "text": ""}
            )
            continue
        text = await transcriber.transcribe(attachment)
        transcripts.append(
            {"path": attachment.path, "status": "completed", "text": str(text)}
        )
    return {"attachments": attachments, "transcripts": tuple(transcripts)}


class ChannelIntake:
    def __init__(self, channel_name, *, allow_from=()):
        self.channel_name = str(channel_name)
        self.allow_from = frozenset(str(item) for item in allow_from)
        self._submit: Callable[[ChannelMessage], Awaitable[Any]] | None = None
        self._sealed = False

    def wire(self, submit):
        if not callable(submit):
            raise TypeError("channel intake submitter must be callable")
        self._submit = submit

    def seal(self):
        self._sealed = True

    async def publish(self, message):
        if self._sealed:
            return {"accepted": False, "reason": "sealed"}
        if message.channel != self.channel_name:
            return {"accepted": False, "reason": "channel_mismatch"}
        if not self.allow_from or (
            "*" not in self.allow_from and message.sender_id not in self.allow_from
        ):
            return {"accepted": False, "reason": "sender_denied"}
        if self._submit is None:
            return {"accepted": False, "reason": "not_wired"}
        return await self._submit(message)


class DirectoryChannel:
    """Usable local channel backed by JSON inbox and outbox directories."""

    name = "directory"

    def __init__(self, root, *, allow_from=("local",), poll_interval=0.1):
        self.root = Path(root)
        self.inbox = self.root / "inbox"
        self.outbox = self.root / "outbox"
        self.processed = self.root / "processed"
        self.intake = ChannelIntake(self.name, allow_from=allow_from)
        self.poll_interval = max(0.02, float(poll_interval))
        self._task = None
        self._stopping = False

    async def start(self):
        for path in (self.inbox, self.outbox, self.processed):
            path.mkdir(parents=True, exist_ok=True)
        self._stopping = False
        self._task = asyncio.create_task(self._poll())

    async def stop(self):
        self._stopping = True
        self.intake.seal()
        if self._task is not None:
            await self._task
            self._task = None

    async def send(self, chat_id, content, media=()):
        digest = hashlib.sha256(
            f"{chat_id}\0{content}\0{len(tuple(media))}".encode("utf-8")
        ).hexdigest()[:20]
        path = self.outbox / f"{digest}.json"
        atomic_replace(
            path,
            json.dumps(
                {"chat_id": str(chat_id), "content": str(content), "media": list(media)},
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )

    async def poll_once(self):
        accepted = 0
        for path in sorted(self.inbox.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            message = ChannelMessage(
                channel=self.name,
                chat_id=str(payload["chat_id"]),
                sender_id=str(payload["sender_id"]),
                text=str(payload.get("text", "")),
                message_id=str(payload.get("message_id", path.stem)),
                conversation=str(payload.get("conversation", "")),
                metadata=payload.get("metadata", {}),
            )
            result = await self.intake.publish(message)
            if result.get("accepted"):
                accepted += 1
            path.replace(self.processed / path.name)
        return accepted

    async def _poll(self):
        while not self._stopping:
            await self.poll_once()
            await asyncio.sleep(self.poll_interval)


__all__ = [
    "Channel",
    "ChannelIntake",
    "ChannelMessage",
    "DeliveryResult",
    "DirectoryChannel",
    "MediaAttachment",
    "Transcriber",
    "normalize_media",
    "save_media_bytes",
]
