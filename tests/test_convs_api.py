# tests/test_convs_api.py
import asyncio
import json

import bridge.routes as routes
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _fake_resolve(token):
    return {"username": "tester", "role": "advisor"} if token == "good" else None


class FakeManager:
    def __init__(self):
        self.deleted = []
        self.closed = None

    def list_convs(self):
        return [{"id": "c1", "title": "复现X", "updated": "2026-10-18T01:00:00"}]

    async def aclose_client(self, conv_id):
        self.closed = conv_id

    def delete_conv(self, conv_id):
        self.deleted.append(conv_id)
        return True


def _client():
    app = FastAPI()
    routes.manager = FakeManager()
    routes._resolve_token = _fake_resolve
    app.include_router(routes.router)
    return TestClient(app), routes.manager


def test_convs_requires_login():
    c, _ = _client()
    assert c.get("/api/pi/convs").status_code == 401


def test_convs_list_and_delete():
    c, m = _client()
    r = c.get("/api/pi/convs", headers={"cookie": "wb_session=good"})
    assert r.json() == {"convs": [{"id": "c1", "title": "复现X",
                                   "updated": "2026-10-18T01:00:00"}]}
    r = c.delete("/api/pi/convs/c1", headers={"cookie": "wb_session=good"})
    assert r.json() == {"ok": True}
    assert m.deleted == ["c1"]
    assert m.closed == "c1"
