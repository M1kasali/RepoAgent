"""Own the SDK loop so SDK heartbeats cannot outlive the channel."""

import asyncio
from importlib.util import find_spec
import threading


class QQSDKBridge:
    def __init__(self, channel, *, sdk_factory=None):
        from .qq_channel import make_sdk_client

        if sdk_factory is None and find_spec("botpy") is None:
            raise ValueError("QQ gateway requires the repoagent[qq] extra")
        self.channel = channel
        self.factory = sdk_factory or make_sdk_client
        self.api = self
        self.thread = None
        self.loop = None
        self.task = None
        self.client = None
        self.main_loop = None
        self.done = None
        self.stopping = threading.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self, **credentials):
        self.main_loop = asyncio.get_running_loop()
        self.done = self.main_loop.create_future()
        bridge = self

        class Ready:
            def set(self):
                bridge.main_loop.call_soon_threadsafe(bridge.channel.ready.set)

        class Proxy:
            ready = Ready()
            last_error = ""

            async def receive(self, data, kind):
                future = asyncio.run_coroutine_threadsafe(
                    bridge.channel.receive(data, kind), bridge.main_loop
                )
                return await asyncio.wrap_future(future)

        async def run():
            self.loop = asyncio.get_running_loop()
            self.task = asyncio.current_task()
            if self.stopping.is_set():
                return
            self.client = self.factory(Proxy())
            async with self.client:
                await self.client.start(**credentials)

        def finish():
            if not self.done.done():
                self.done.set_result(None)

        def worker():
            try:
                asyncio.run(run())
            except BaseException:
                # SDK exceptions may carry credentials; never forward raw errors.
                self.channel.last_error = "QQ SDK connection stopped"
            finally:
                self.main_loop.call_soon_threadsafe(finish)

        self.thread = threading.Thread(target=worker, name="repoagent-qq", daemon=True)
        self.thread.start()
        await self.done

    async def close(self):
        self.stopping.set()
        if (
            self.loop is not None
            and not self.loop.is_closed()
            and self.task is not None
        ):
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                pass
        if self.thread is not None:
            await asyncio.to_thread(self.thread.join, 10)
            if self.thread.is_alive():
                raise RuntimeError("QQ SDK shutdown timed out")
            self.thread = None

    async def _send(self, method, kwargs):
        if self.loop is None or self.loop.is_closed() or self.client is None:
            raise RuntimeError("QQ SDK is not connected")

        async def invoke():
            return await getattr(self.client.api, method)(**kwargs)

        coroutine = invoke()
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        except RuntimeError:
            coroutine.close()
            raise RuntimeError("QQ SDK is not connected") from None
        return await asyncio.wrap_future(future)

    async def post_c2c_message(self, **kwargs):
        return await self._send("post_c2c_message", kwargs)

    async def post_group_message(self, **kwargs):
        return await self._send("post_group_message", kwargs)

    async def post_dms(self, **kwargs):
        return await self._send("post_dms", kwargs)
