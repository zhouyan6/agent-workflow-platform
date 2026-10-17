# tests/test_pi_client.py
import asyncio
import types

from bridge.pi_client import PiClient


class FakePiProcess:
    """记录 send()，按脚本回放 response；其余事件转入 stream_events。"""

    def __init__(self, replies: list[dict]):
        self.events = asyncio.Queue()
        self.sent: list[dict] = []
        self._replies = list(replies)
        self.proc = types.SimpleNamespace(returncode=None)

    @property
    def alive(self):
        return True

    async def send(self, obj):
        self.sent.append(obj)
        if self._replies:
            reply = {**self._replies.pop(0), "id": obj.get("id")}
            await asyncio.sleep(0)
            await self.events.put(reply)

    async def stop(self):
        pass


async def test_prompt_sends_id_and_returns_data():
    fp = FakePiProcess([{"type": "response", "command": "prompt",
                         "success": True, "data": {"disposition": "started"}}])
    client = PiClient(fp)
    data = await client.prompt("你好")
    assert data == {"disposition": "started"}
    sent = fp.sent[0]
    assert sent["type"] == "prompt"
    assert sent["message"] == "你好"
    assert isinstance(sent["id"], str) and sent["id"]
    await client.close()


async def test_non_response_events_go_to_stream():
    fp = FakePiProcess([{"type": "response", "command": "get_state",
                         "success": True, "data": {"isStreaming": False}}])
    client = PiClient(fp)
    await fp.events.put({"type": "agent_settled"})
    assert (await client.get_state()) == {"isStreaming": False}
    ev = await asyncio.wait_for(client.stream_events.get(), timeout=1)
    assert ev == {"type": "agent_settled"}
    await client.close()


async def test_ui_response_has_no_id_added():
    fp = FakePiProcess([])
    client = PiClient(fp)
    await client.ui_response({"type": "extension_ui_response",
                              "id": "d1", "confirmed": True})
    assert fp.sent == [{"type": "extension_ui_response", "id": "d1", "confirmed": True}]
    await client.close()
