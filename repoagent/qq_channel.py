"""Optional QQ SDK adapter; platform acceptance is not task verification."""

import asyncio
import json
from pathlib import PurePosixPath

from .channels import ChannelIntake, ChannelMessage
from .qq_sdk import QQSDKBridge


def normalize_message(data, kind):
    author = data.author
    sender = (
        getattr(author, "member_openid", None)
        if kind == "group"
        else getattr(author, "user_openid", None) or getattr(author, "id", None)
    )
    target = (
        getattr(data, "group_openid", None)
        if kind == "group"
        else getattr(data, "guild_id", None)
        if kind == "guild_dm"
        else sender
    )
    message_id = getattr(data, "id", None)
    if not all(
        isinstance(value, str) and value.strip()
        for value in (sender, target, message_id)
    ):
        raise ValueError("QQ message has incomplete identity")
    text = getattr(data, "content", "") or ""
    if not isinstance(text, str):
        raise ValueError("QQ content must be text")
    labels = []
    for attachment in getattr(data, "attachments", None) or ():
        name = str(getattr(attachment, "filename", "") or "file")
        name = PurePosixPath(name.replace("\\", "/")).name
        name = "".join(ch for ch in name if ch.isprintable())[:200]
        labels.append(f"[Attachment metadata only; not downloaded: {name}]")
    content = "\n".join([text.strip(), *labels]).strip()
    if len(content) > 100_000:
        raise ValueError("QQ message exceeds input limit")
    # The reply target includes the source message, but session ordering does not.
    return ChannelMessage(
        channel="qq",
        chat_id=json.dumps([kind, target, message_id]),
        sender_id=sender,
        text=content,
        message_id=json.dumps([kind, target, message_id]),
        conversation="qq:" + json.dumps([kind, target]),
    )


def make_sdk_client(channel):
    try:
        import botpy
    except ImportError:
        raise ValueError("QQ gateway requires the repoagent[qq] extra") from None

    class Client(botpy.Client):
        def __init__(self):
            super().__init__(
                intents=botpy.Intents(public_messages=True, direct_message=True),
                ext_handlers=False,
                bot_log=False,
            )
            self.connections = set()

        async def bot_connect(self, session):
            task = asyncio.current_task()
            self.connections.add(task)
            task.add_done_callback(self.connections.discard)
            return await super().bot_connect(session)

        async def close(self):
            for task in tuple(self.connections):
                task.cancel()
            await asyncio.gather(*tuple(self.connections), return_exceptions=True)
            await super().close()

        async def on_ready(self):
            channel.ready.set()

        async def on_c2c_message_create(self, message):
            await channel.receive(message, "c2c")

        async def on_group_at_message_create(self, message):
            await channel.receive(message, "group")

        async def on_direct_message_create(self, message):
            await channel.receive(message, "guild_dm")

        async def on_error(self, event_method, *args, **kwargs):
            channel.last_error = "SDK callback failed"

    return Client()


class QQChannel:
    name = "qq"

    def __init__(
        self,
        *,
        app_id,
        secret,
        allow_from,
        client_factory=QQSDKBridge,
        startup_timeout=30,
    ):
        if not app_id or not secret or not allow_from:
            raise ValueError(
                "QQ requires app ID, secret and an explicit sender allowlist"
            )
        self.app_id, self.secret = app_id, secret
        self.intake = ChannelIntake(self.name, allow_from=allow_from)
        self.client_factory = client_factory
        self.startup_timeout = startup_timeout
        self.ready = asyncio.Event()
        self.client = None
        self.task = None
        self.last_error = ""
        self._callbacks = set()
        self.intake.seal()

    async def _run(self):
        try:
            async with self.client:
                await self.client.start(appid=self.app_id, secret=self.secret)
        finally:
            self.ready.clear()
            self.intake.seal()

    async def start(self):
        if self.task is not None:
            return False
        self.client = self.client_factory(self)
        self.ready.clear()
        self.intake.reopen()
        self.task = asyncio.create_task(self._run())
        waiter = asyncio.create_task(self.ready.wait())
        try:
            done, _ = await asyncio.wait(
                (self.task, waiter),
                timeout=self.startup_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if self.task in done or waiter not in done:
                raise RuntimeError("QQ connection failed or readiness timed out")
            return True
        except BaseException:
            await self.stop()
            raise
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    async def stop(self):
        self.intake.seal()
        for task in tuple(self._callbacks):
            task.cancel()
        await asyncio.gather(*tuple(self._callbacks), return_exceptions=True)
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self.client is not None:
            await self.client.close()
            self.client = None
        self.ready.clear()

    async def receive(self, data, kind):
        task = asyncio.current_task()
        self._callbacks.add(task)
        try:
            message = normalize_message(data, kind)
            return await self.intake.publish(message)
        except Exception:
            self.last_error = "QQ message rejected or intake failed"
            return {"accepted": False, "reason": "invalid_or_failed"}
        finally:
            self._callbacks.discard(task)

    async def send(self, chat_id, content, media=()):
        if self.client is None or not self.ready.is_set():
            raise RuntimeError("QQ channel is not connected")
        if media:
            raise ValueError("QQ outgoing attachments are not supported")
        kind, target, message_id = json.loads(chat_id)
        try:
            if kind == "guild_dm":
                await self.client.api.post_dms(
                    guild_id=target, content=content, msg_id=message_id
                )
            elif kind in {"group", "c2c"}:
                method = (
                    self.client.api.post_group_message
                    if kind == "group"
                    else self.client.api.post_c2c_message
                )
                key = "group_openid" if kind == "group" else "openid"
                await method(
                    **{key: target},
                    msg_type=0,
                    content=content,
                    msg_id=message_id,
                    msg_seq=1,
                )
            else:
                raise ValueError("unknown QQ reply route")
        except Exception:
            raise RuntimeError("QQ reply failed; delivery is unconfirmed") from None
