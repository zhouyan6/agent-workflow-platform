# bridge/pi_client.py
"""Pi RPC 命令封装：id 关联 response，其余事件转 stream_events。"""
import asyncio
import uuid


class PiClient:
    def __init__(self, process):
        self.process = process
        self.stream_events: asyncio.Queue[dict] = asyncio.Queue()
        self._futures: dict[str, asyncio.Future] = {}
        self._router = asyncio.create_task(self._route())

    async def _route(self) -> None:
        while True:
            obj = await self.process.events.get()
            if obj.get("type") == "response" and obj.get("id") in self._futures:
                fut = self._futures.pop(obj["id"])
                if not fut.done():
                    fut.set_result(obj)
            else:
                await self.stream_events.put(obj)

    async def command(self, payload: dict, timeout: float = 120) -> dict:
        rid = uuid.uuid4().hex[:12]
        fut = asyncio.get_running_loop().create_future()
        self._futures[rid] = fut
        await self.process.send({**payload, "id": rid})
        return await asyncio.wait_for(fut, timeout)

    async def prompt(self, message: str, streaming_behavior: str | None = None) -> dict:
        payload: dict = {"type": "prompt", "message": message}
        if streaming_behavior:
            payload["streamingBehavior"] = streaming_behavior
        resp = await self.command(payload)
        return resp.get("data") or {}

    async def get_state(self) -> dict:
        return (await self.command({"type": "get_state"})).get("data") or {}

    async def abort(self) -> dict:
        return (await self.command({"type": "abort"})).get("data") or {}

    async def switch_session(self, path: str) -> dict:
        return (await self.command(
            {"type": "switch_session", "sessionPath": path})).get("data") or {}

    async def ui_response(self, resp: dict) -> None:
        await self.process.send(resp)

    async def close(self) -> None:
        self._router.cancel()
        await self.process.stop()
