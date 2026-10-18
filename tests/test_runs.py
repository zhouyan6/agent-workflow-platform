# tests/test_runs.py
import asyncio
import json

import bridge.tool_backend as tb
from bridge.runs import Runs
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_runs_crud(tmp_path):
    runs = Runs(str(tmp_path))
    r = runs.create("c1", "复现 PmcmFL", [{"title": "调研"}, {"title": "跑实验"}])
    assert r["goal"] == "复现 PmcmFL"
    assert [s["title"] for s in r["stages"]] == ["调研", "跑实验"]
    assert all(s["status"] == "pending" for s in r["stages"])
    r["stages"][0]["status"] = "in_progress"
    r2 = runs.update(r["id"], r["stages"])
    assert r2["stages"][0]["status"] == "in_progress"
    assert runs.update("nope", []) is None
    assert runs.for_conv("c1")[0]["id"] == r["id"]
    assert Runs(str(tmp_path)).for_conv("c1")[0]["id"] == r["id"]  # 持久化


class _M:
    bridge_token = "t"
    ctx_factory = None

    def __init__(self):
        self.pushed = []

    def user_for(self, c):
        return {"username": "u"}

    async def push_run(self, conv, run):
        self.pushed.append((conv, run))


def _client(monkeypatch, root):
    app = FastAPI()
    tb._manager = _M()
    tb.mount_tool_backend(app, tb._manager)
    monkeypatch.setattr(tb, "RUNS", Runs(root))
    return TestClient(app), tb._manager


def test_run_create_tool(monkeypatch, tmp_path):
    c, m = _client(monkeypatch, str(tmp_path))
    r = c.post("/api/pi/tools/run_create",
               headers={"x-bridge-token": "t"},
               json={"args": {"goal": "复现X", "stages": [{"title": "调研"}]},
                     "conv": "c1"})
    body = r.json()["result"]
    assert json.loads(body)["ok"] is True
    assert m.pushed[0][0] == "c1"


def test_ccf_lookup_tool(monkeypatch, tmp_path):
    c, _ = _client(monkeypatch, str(tmp_path))
    # 仓库根的真实 ccf.json（绝对路径写死）
    monkeypatch.setattr(tb, "CCF_PATH", "D:/gpu-workbench/ccf.json")
    r = c.post("/api/pi/tools/ccf_lookup",
               headers={"x-bridge-token": "t"},
               json={"args": {"query": "tocs"}, "conv": "c1"})
    body = r.json()["result"]
    assert "TOCS" in body
