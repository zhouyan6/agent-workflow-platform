# tests/test_routes.py
import asyncio
import json

import bridge.routes as routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _fake_resolve(token):
    return {"username": "tester", "role": "advisor"} if token == "good" else None


class FakeClient:
    def __init__(self):
        self.stream_events = asyncio.Queue()
        self.prompts = []

    async def get_state(self):
        return {"isStreaming": False}

    async def prompt(self, text, sb=None):
        self.prompts.append((text, sb))
        await self.stream_events.put({"type": "agent_settled"})
        return {"disposition": "started"}

    async def abort(self):
        return {}

    async def ui_response(self, resp):
        self.ui = resp


class FakeManager:
    def __init__(self):
        self.client = FakeClient()

    async def get(self, conv_id):
        return self.client


def _make_client(monkeypatch):
    app = FastAPI()
    routes.manager = FakeManager()  # 直接替换模块级 manager
    app.include_router(routes.router)
    return TestClient(app), routes.manager


def test_ws_rejects_without_session(monkeypatch):
    client, _ = _make_client(monkeypatch)
    routes._resolve_token = _fake_resolve
    with client.websocket_connect("/api/pi/ws") as ws:
        data = json.loads(ws.receive_text())
        assert data == {"type": "error", "message": "未登录"}


def test_ws_chat_roundtrip(monkeypatch):
    client, mgr = _make_client(monkeypatch)
    routes._resolve_token = _fake_resolve
    with client.websocket_connect(
            "/api/pi/ws?conv=t1",
            headers={"cookie": "wb_session=good"}) as ws:
        ws.send_text(json.dumps({"type": "message", "text": "你好"}))
        msgs = []
        while True:
            m = json.loads(ws.receive_text())
            msgs.append(m)
            if m["type"] == "turn_done":
                break
        assert msgs == [{"type": "turn_done"}]
        assert mgr.client.prompts == [("你好", None)]
