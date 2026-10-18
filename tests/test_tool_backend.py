# tests/test_tool_backend.py
import asyncio
import json

import bridge.tool_backend as tb
from fastapi import FastAPI
from fastapi.testclient import TestClient


class FakeManager:
    def __init__(self):
        self.bridge_token = "tok123"
        self._users = {"c1": {"username": "u", "role": "advisor"}}
        self.plans = {}
        self.watched = None

    def user_for(self, conv_id):
        return self._users.get(conv_id)

    async def push_plan(self, conv_id, steps):
        self.plans[conv_id] = steps

    def watch_task(self, conv, tid):
        self.watched = (conv, tid)


def _client(monkeypatch):
    app = FastAPI()
    tb._manager = FakeManager()
    tb.mount_tool_backend(app, tb._manager)
    return TestClient(app), tb._manager


def test_schema_requires_token():
    app = FastAPI()
    tb._manager = FakeManager()
    tb.mount_tool_backend(app, tb._manager)
    c = TestClient(app)
    assert c.get("/api/pi/tools/schema").status_code == 403
    assert c.get("/api/pi/tools/schema",
                 headers={"x-bridge-token": "wrong"}).status_code == 403


def test_schema_returns_openai_tools():
    app = FastAPI()
    tb._manager = FakeManager()
    tb.mount_tool_backend(app, tb._manager)
    c = TestClient(app)
    r = c.get("/api/pi/tools/schema", headers={"x-bridge-token": "tok123"})
    assert r.status_code == 200
    names = [t["function"]["name"] for t in r.json()["tools"]]
    assert "gpu_status" in names and "submit_task" in names


def test_call_tool_requires_known_conv(monkeypatch):
    c, _ = _client(monkeypatch)
    r = c.post("/api/pi/tools/gpu_status", headers={"x-bridge-token": "tok123"},
               json={"args": {}, "conv": "never-connected"})
    assert r.status_code == 403


def test_call_tool_dispatches_and_wraps_result(monkeypatch):
    c, _ = _client(monkeypatch)
    tb._manager.ctx_factory = lambda user: {"ctx": True}

    def fake_dispatch(name, args, ctx):
        assert name == "gpu_status"
        assert ctx == {"ctx": True}
        return json.dumps({"ok": True, "gpus": []}, ensure_ascii=False)

    monkeypatch.setattr(tb.agent_tools, "dispatch", fake_dispatch)
    r = c.post("/api/pi/tools/gpu_status", headers={"x-bridge-token": "tok123"},
               json={"args": {}, "conv": "c1"})
    assert r.status_code == 200
    assert r.json() == {"result": "{\"ok\": true, \"gpus\": []}"}


def test_update_plan_special_case(monkeypatch):
    c, mgr = _client(monkeypatch)
    r = c.post("/api/pi/tools/update_plan", headers={"x-bridge-token": "tok123"},
               json={"args": {"steps": [{"title": "s", "status": "pending"}]},
                     "conv": "c1"})
    assert r.status_code == 200
    assert mgr.plans["c1"] == [{"title": "s", "status": "pending"}]


def test_submit_task_registers_watch(monkeypatch):
    c, mgr = _client(monkeypatch)
    mgr.ctx_factory = lambda user: {"ctx": True}

    def fake_dispatch(name, args, ctx):
        return json.dumps({"ok": True, "task": {"id": 7, "name": "t"}},
                          ensure_ascii=False)

    monkeypatch.setattr(tb.agent_tools, "dispatch", fake_dispatch)
    r = c.post("/api/pi/tools/submit_task", headers={"x-bridge-token": "tok123"},
               json={"args": {}, "conv": "c1"})
    assert r.status_code == 200
    assert mgr.watched == ("c1", 7)
