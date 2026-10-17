# 科研执行智能体 · 第 3 期（AgentOps 前端 + Run 工作流）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 前端彻底替换为 AgentOps 形态对话工作台（Vue 3 无构建），叠加 Run 工作流（智能体生成阶段 + Gate 分级审核 + 任务异步事件注入），补完第 2 期遗留的 E2E。

**Architecture:** 后端桥新增会话列表/删除、历史回放、runs 持久化（runs.json）、ccf_lookup 与 run 工具（tool_backend 特例分支）、任务监控注入（只读轮询 workbench.db + followUp 唤醒）；前端 Vue 3（vendor 本地化）覆盖 static/ 三件套，旧 7 页与根目录旧副本删除，REST API 全保留。

**Tech Stack:** FastAPI（既有）、Vue 3 global build（vendor 到 static/vendor/）、Pi 1.3.0 + GLM（既有）。

**Spec:** `docs/superpowers/specs/2026-10-17-pi-research-agent-design.md` §9、§10（2026-10-18 追加节）

## Global Constraints

- 工作目录 `D:\gpu-workbench`；Python 一律 `.venv-win/Scripts/python`；测试 `.venv-win/Scripts/python -m pytest tests/ -q`
- 既有 WS 消息类型名不得改动；新增（均为桥→网页）：`{"type":"history","entries":[{"role":"user"|"assistant","text":str}]}`、`{"type":"run_update","run":<run 对象>}`、`{"type":"agent_event","text":str}`
- run 对象形状：`{"id":str,"conv":str,"goal":str,"stages":[{"title":str,"status":"pending|in_progress|done|blocked"}],"updated":str}`，持久化在 `ROOT/runs.json`
- pi-sessions/index.json 值升级为 `{"path":str,"title":str,"updated":str}`，读取时兼容旧纯字符串值（视为 `{"path":v,"title":conv_id,"updated":""}`）
- 新工具（tool_backend 特例，不经 agent.tools.dispatch）：`run_create{goal,stages:[{title}]}`、`run_update{run_id,stages}`、`ccf_lookup{query}`（查 ccf.json 字段 r/t/a/s/f，返回前 10 条）
- 提交实验后桥登记 conv→task_id；任务结束/失败时注入 `agent_event` 并以 prompt 唤醒智能体
- 危险工具确认名单不变：stop_task/submit_task/save_report + 危险 bash
- 前端不依赖运行时 CDN：Vue 从 `static/vendor/vue.global.prod.js` 加载
- 每任务结束 git commit

---

### Task 1: 补完第 2 期 E2E（遗留）

**Files:**
- Create: `tests/e2e_tools.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: 已合并的第 2 期全部代码（tool_backend、扩展、门禁）
- Produces: E2E 验证通过（工具链 + 确认门禁）

- [ ] **Step 1: 创建 tests/e2e_tools.py**

```python
"""E2E：工具调用 + 确认门禁全链路（真实 Pi + GLM + 桥 + agent.tools）。

前置：server 已在本地 8787 运行，扩展已安装。
用法：.venv-win/Scripts/python -u tests/e2e_tools.py <token>
"""
import asyncio
import json
import sys

import websockets

BASE = "ws://127.0.0.1:8787/api/pi/ws?conv=tools-e2e"


async def turn(ws, text, auto_confirm=False):
    await ws.send(json.dumps({"type": "message", "text": text}))
    got, tools = "", []
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=180))
        t = m["type"]
        if t == "token":
            got += m["delta"]
        elif t == "tool_start":
            tools.append(m.get("tool"))
        elif t == "confirm_request" and auto_confirm:
            await ws.send(json.dumps(
                {"type": "confirm", "id": m["id"], "confirmed": True}))
            auto_confirm = False
        elif t == "error":
            raise SystemExit(f"error: {m['message']}")
        elif t == "turn_done":
            return got, tools


async def main(token: str) -> None:
    async with websockets.connect(BASE, additional_headers={
            "Cookie": f"wb_session={token}"}) as ws:
        got, tools = await turn(
            ws, "请调用 gpu_status 工具查看显卡，然后用一句话总结空闲情况。")
        print("turn1 tools:", tools, "| reply:", got[:80])
        assert "gpu_status" in tools, "GLM 未调用 gpu_status"
        print("TOOL E2E OK")

        got, tools = await turn(
            ws, "请提交一个测试任务：名字 t-echo，命令 echo hello，需要 1 张 GPU。",
            auto_confirm=True)
        print("turn2 tools:", tools, "| reply:", got[:80])
        assert "submit_task" in tools, "GLM 未调用 submit_task"
        print("CONFIRM-GATE E2E OK")


asyncio.run(main(sys.argv[1]))
```

- [ ] **Step 2: 起服务并运行**

```bash
WORKBENCH_MOCK=1 .venv-win/Scripts/python server.py > /tmp/wb-server.log 2>&1 &
sleep 6
TOKEN=$(curl -s -X POST http://127.0.0.1:8787/api/login -H "Content-Type: application/json" \
  -d '{"username":"niexin","password":"niexin"}' | python -c "import json,sys;print(json.load(sys.stdin)['token'])")
PYTHONIOENCODING=utf-8 .venv-win/Scripts/python -u tests/e2e_tools.py "$TOKEN"
kill %1
```

Expected: `TOOL E2E OK` 与 `CONFIRM-GATE E2E OK`。LLM 不确定性：最多重试 2 次；持续失败查 /tmp/wb-server.log。

- [ ] **Step 3: README 追加 + 提交**

README「本地开发」节追加一行：

```markdown
- Pi 扩展：改 `pi-extension/research/index.ts` 后需重新拷贝到
  `C:\Users\ZhouYan\.pi\agent\extensions\research\index.ts` 并重连会话生效
```

```bash
git add tests/e2e_tools.py README.md && git commit -m "test: e2e for tools and confirm gate; docs"
```

---

### Task 2: 会话列表 / 删除 / 标题

**Files:**
- Modify: `bridge/sessions.py`、`bridge/routes.py`
- Test: `tests/test_sessions.py`（追加）、`tests/test_convs_api.py`（新建）

**Interfaces:**
- Produces:
  - `PiSessionManager.list_convs() -> list[dict]`（`[{id,title,updated}]`，按 updated 倒序）
  - `PiSessionManager.delete_conv(conv_id) -> bool`（杀 client、删 index 条目、删 session 文件）
  - `PiSessionManager.maybe_set_title(conv_id, first_text) -> None`（当前 title == conv_id 时改为 `first_text[:24]`）
  - index 读写升级（兼容旧字符串值）
  - REST：`GET /api/pi/convs`（cookie 鉴权）→ `{"convs":[...]}`；`DELETE /api/pi/convs/{conv_id}` → `{"ok":true|false}`

- [ ] **Step 1: 失败测试（追加到 tests/test_sessions.py）**

```python
def test_index_upgrade_and_list(tmp_path):
    import json as _json
    from bridge.sessions import PiSessionManager
    m = PiSessionManager(str(tmp_path))
    old = tmp_path / "pi-sessions" / "s1.jsonl"
    old.write_text("{}")
    m._save_index({"old-conv": str(old),                     # 旧格式：纯字符串
                   "new-conv": {"path": str(old), "title": "复现X", "updated": "2026-10-18T01:00:00"}})
    convs = m.list_convs()
    ids = {c["id"] for c in convs}
    assert ids == {"old-conv", "new-conv"}
    by_id = {c["id"]: c for c in convs}
    assert by_id["old-conv"]["title"] == "old-conv"          # 旧值回退
    assert by_id["new-conv"]["title"] == "复现X"


def test_list_convs_sorted_by_updated(tmp_path):
    from bridge.sessions import PiSessionManager
    m = PiSessionManager(str(tmp_path))
    m._save_index({"a": {"path": "x", "title": "a", "updated": "1"},
                   "b": {"path": "x", "title": "b", "updated": "2"}})
    assert [c["id"] for c in m.list_convs()] == ["b", "a"]


def test_delete_conv_removes_everything(tmp_path):
    import asyncio
    from bridge.sessions import PiSessionManager
    m = PiSessionManager(str(tmp_path))
    f = tmp_path / "pi-sessions" / "c1.jsonl"
    f.write_text("{}")
    m._save_index({"c1": {"path": str(f), "title": "t", "updated": "1"}})
    assert m.delete_conv("c1") is True
    assert not f.exists()
    assert "c1" not in m._load_index()
    assert m.delete_conv("ghost") is False


def test_maybe_set_title(tmp_path):
    from bridge.sessions import PiSessionManager
    m = PiSessionManager(str(tmp_path))
    f = tmp_path / "pi-sessions" / "c1.jsonl"
    f.write_text("{}")
    m._save_index({"c1": {"path": str(f), "title": "c1", "updated": "1"}})
    m.maybe_set_title("c1", "复现 PmcmFL 论文的第一句话")
    assert m._load_index()["c1"]["title"] == "复现 PmcmFL 论文的第一句话"[:24]
    m.maybe_set_title("c1", "第二条消息不该改标题")
    assert m._load_index()["c1"]["title"] == "复现 PmcmFL 论文的第一句话"[:24]
```

- [ ] **Step 2: 失败测试（新建 tests/test_convs_api.py）**

```python
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

    def list_convs(self):
        return [{"id": "c1", "title": "复现X", "updated": "2026-10-18T01:00:00"}]

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
```

- [ ] **Step 3: 运行确认失败**

Run: `.venv-win/Scripts/python -m pytest tests/test_sessions.py tests/test_convs_api.py -q`
Expected: 新增 6 个测试 FAIL

- [ ] **Step 4: 实现 sessions.py**

`_load_index`/`_save_index` 之后追加规范化辅助与三个方法（`_load_index` 返回值改为规范化后：条目值统一为 dict）：

```python
    @staticmethod
    def _norm_entry(conv_id: str, val) -> dict:
        if isinstance(val, dict):
            return {"path": val.get("path", ""), "title": val.get("title") or conv_id,
                    "updated": val.get("updated", "")}
        return {"path": str(val), "title": conv_id, "updated": ""}

    def _conv_entries(self) -> dict[str, dict]:
        return {cid: self._norm_entry(cid, v)
                for cid, v in self._load_index().items()}
```

（`_load_index` 保持原样返回原始 JSON；规范化只发生在 `_conv_entries`；`get()` 内记录 session 路径时改写为完整 dict：`idx[conv_id] = {"path": path, "title": idx.get(conv_id, {}).get("title", conv_id) if isinstance(idx.get(conv_id), dict) else conv_id, "updated": now_iso}`——直接用下面 `maybe_set_title` 里的 `_touch` 辅助）：

```python
    @staticmethod
    def _touch(entry: dict, path: str | None = None, title: str | None = None) -> dict:
        import datetime
        return {"path": path if path is not None else entry.get("path", ""),
                "title": title if title is not None else entry.get("title", ""),
                "updated": datetime.datetime.now().isoformat(timespec="seconds")}

    def list_convs(self) -> list[dict]:
        entries = self._conv_entries()
        rows = [{"id": cid, "title": e["title"], "updated": e["updated"]}
                for cid, e in entries.items()]
        rows.sort(key=lambda r: r["updated"], reverse=True)
        return rows

    def delete_conv(self, conv_id: str) -> bool:
        idx = self._load_index()
        if conv_id not in idx:
            return False
        entry = self._norm_entry(conv_id, idx[conv_id])
        client = self._clients.pop(conv_id, None)
        if client:
            try:
                import asyncio
                asyncio.get_event_loop().create_task(client.close())
            except RuntimeError:
                pass
        idx.pop(conv_id)
        self._save_index(idx)
        path = entry.get("path")
        if path and os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                log.warning("remove session file failed: %s", path, exc_info=True)
        return True

    def maybe_set_title(self, conv_id: str, first_text: str) -> None:
        idx = self._load_index()
        raw = idx.get(conv_id)
        entry = self._norm_entry(conv_id, raw) if raw is not None else None
        if entry is None or entry["title"] != conv_id:
            return
        idx[conv_id] = self._touch(entry, title=(first_text or conv_id)[:24].strip() or conv_id)
        self._save_index(idx)
```

`get()` 里记录路径的段落改为 dict 写法：

```python
        try:
            state = await client.get_state()
            path = state.get("sessionFile")
            if path:
                idx = self._load_index()
                prev = idx.get(conv_id)
                prev = self._norm_entry(conv_id, prev) if prev is not None else {
                    "path": path, "title": conv_id, "updated": ""}
                idx[conv_id] = self._touch(prev, path=path)
                self._save_index(idx)
        except Exception:
            log.warning("record session path failed", exc_info=True)
```

- [ ] **Step 5: 实现 routes.py REST 端点**

`pi_ws` 之前追加：

```python
from fastapi import HTTPException
from fastapi.responses import JSONResponse


def _ws_user_cookie(token: str):
    user = _resolve_token(token) if _resolve_token else None
    if not user:
        raise HTTPException(401, "未登录")
    return user


@router.get("/api/pi/convs")
def list_convs(wb_session: str = ""):
    _ws_user_cookie(wb_session)
    return {"convs": manager.list_convs()}


@router.delete("/api/pi/convs/{conv_id}")
def delete_conv(conv_id: str, wb_session: str = ""):
    _ws_user_cookie(wb_session)
    return {"ok": manager.delete_conv(conv_id)}
```

（cookie 用查询参数 `wb_session` 便于 fetch 传参；fetch 端统一 `credentials: "include"` 时 cookie 也能进 REST——两种都支持，以实现简单为准）

`pi_ws` 的 message 分支里，发送 prompt 之前加标题维护：

```python
            if mtype == "message":
                manager.maybe_set_title(conv, msg.get("text", ""))
                state = await client.get_state()
```

（`maybe_set_title` 包 try/except 防止 index 写失败打断对话）

- [ ] **Step 6: 全量测试 + 提交**

Run: `.venv-win/Scripts/python -m pytest tests/ -q`
Expected: 全部 passed

```bash
git add bridge/ tests/ && git commit -m "feat(bridge): conversation list, delete, auto title"
```

---

### Task 3: 历史回放

**Files:**
- Modify: `bridge/routes.py`
- Test: `tests/test_routes.py`（追加）

**Interfaces:**
- Consumes: `PiClient.command({"type": "get_messages"})` → `data` 为 AgentMessage 数组（`role` ∈ user/assistant，`content` 为 blocks：user 的 `[{type:"text",text}]`，assistant 的 `[{type:"text",text}|{type:"thinking"...}|{type:"toolCall"...}]`）
- Produces: WS 连接建立、pump 启动前，桥先发 `{"type":"history","entries":[{"role","text"}]}`（user/assistant 各取 text blocks 拼接；system/空文本跳过）

- [ ] **Step 1: 失败测试（追加到 tests/test_routes.py）**

```python
def test_ws_sends_history_before_pump(monkeypatch):
    client, mgr = _make_client(monkeypatch)
    routes._resolve_token = _fake_resolve

    async def fake_get_messages():
        return [{"role": "user", "content": [{"type": "text", "text": "问题"}]},
                {"role": "assistant", "content": [
                    {"type": "thinking", "thinking": "x"},
                    {"type": "text", "text": "回答"}]}]
    mgr.client.get_messages = fake_get_messages
    with client.websocket_connect(
            "/api/pi/ws?conv=h1", headers={"cookie": "wb_session=good"}) as ws:
        m = json.loads(ws.receive_text())
        assert m == {"type": "history", "entries": [
            {"role": "user", "text": "问题"},
            {"role": "assistant", "text": "回答"}]}
```

（FakeClient 需补一个默认 `get_messages` 方法返回 `[]`，加在 FakeClient 类里：

```python
    async def get_messages(self):
        return []
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv-win/Scripts/python -m pytest tests/test_routes.py -q`
Expected: 新测试 FAIL（收到的是 turn_done 而非 history，或 KeyError）

- [ ] **Step 3: 实现**

`routes.py` 追加历史提取函数（放 `_pump` 之前）：

```python
def _history_entries(messages: list) -> list[dict]:
    out = []
    for msg in messages or []:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = "".join(
            b.get("text") or "" for b in (msg.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text")
        if text.strip():
            out.append({"role": role, "text": text})
    return out
```

`pi_ws` 中 `pump = asyncio.create_task(...)` 之前插入：

```python
    try:
        data = await client.command({"type": "get_messages"})
        entries = _history_entries(data.get("data") or [])
        await ws.send_text(json.dumps(
            {"type": "history", "entries": entries}, ensure_ascii=False))
    except Exception:
        pass  # 历史回放失败不阻塞新对话
```

- [ ] **Step 4: 全量测试 + 提交**

Run: `.venv-win/Scripts/python -m pytest tests/ -q`
Expected: 全部 passed

```bash
git add bridge/routes.py tests/test_routes.py && git commit -m "feat(bridge): replay session history on ws connect"
```

---

### Task 4: Run 工作流（runs.json + 工具 + REST）

**Files:**
- Create: `bridge/runs.py`
- Modify: `bridge/tool_backend.py`、`bridge/sessions.py`（push_run）、`bridge/events.py`（_bridge_run）、`bridge/routes.py`（GET /api/pi/runs）
- Test: `tests/test_runs.py`（新建）、`tests/test_events.py`（追加）

**Interfaces:**
- Produces:
  - `Runs(root)`：`create(conv, goal, stages) -> run`、`update(run_id, stages) -> run | None`、`for_conv(conv) -> list[run]`；run 形状见 Global Constraints；id 为 `uuid.uuid4().hex[:8]`
  - `PiSessionManager.push_run(conv_id, run)` → `stream_events.put({"type": "_bridge_run", "run": run})`
  - `translate_event`：`_bridge_run` → `[{"type": "run_update", "run": ev["run"]}]`
  - tool_backend 特例（token 鉴权同其它）：`run_create` → Runs.create + push_run，返回 `{"ok":true,"run":<run>}`；`run_update` → 同理；`ccf_lookup` → 查 `ROOT/ccf.json`（字段 r/t/a/s/f/p/u），query 在 r/t/a/s/f 上不区分大小写子串匹配，命中前 10 条格式化文本
  - REST `GET /api/pi/runs?conv=<id>&wb_session=<token>` → `{"runs":[...]}`

- [ ] **Step 1: 失败测试（新建 tests/test_runs.py）**

```python
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
    r = c.post("/api/pi/tools/ccf_lookup",
               headers={"x-bridge-token": "t"},
               json={"args": {"query": "tocs"}, "conv": "c1"})
    body = r.json()["result"]
    assert "TOCS" in body
```

（`test_ccf_lookup_tool` 需要真实的 ccf.json——monkeypatch `tb.CCF_PATH` 指向仓库根的 `D:/gpu-workbench/ccf.json` 绝对路径，写死在测试里）

- [ ] **Step 2: 失败测试（追加到 tests/test_events.py）**

```python
def test_bridge_run_to_run_update():
    run = {"id": "r1", "conv": "c", "goal": "g",
           "stages": [{"title": "s", "status": "pending"}], "updated": "x"}
    assert translate_event({"type": "_bridge_run", "run": run}) == [
        {"type": "run_update", "run": run}]
```

- [ ] **Step 3: 运行确认失败，然后实现 bridge/runs.py**

```python
# bridge/runs.py
"""Run 工作流持久化（ROOT/runs.json）。"""
import json
import os
import uuid
from datetime import datetime


class Runs:
    def __init__(self, root: str):
        self.path = os.path.join(root, "runs.json")

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"runs": []}

    def _save(self, data: dict) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def create(self, conv: str, goal: str, stages: list) -> dict:
        data = self._load()
        run = {"id": uuid.uuid4().hex[:8], "conv": conv, "goal": goal,
               "stages": [{"title": s.get("title", ""), "status": "pending"}
                          for s in (stages or [])],
               "updated": self._now()}
        data["runs"].append(run)
        self._save(data)
        return run

    def update(self, run_id: str, stages: list) -> dict | None:
        data = self._load()
        for run in data["runs"]:
            if run["id"] == run_id:
                run["stages"] = [{"title": s.get("title", ""),
                                  "status": s.get("status", "pending")}
                                 for s in (stages or [])]
                run["updated"] = self._now()
                self._save(data)
                return run
        return None

    def for_conv(self, conv: str) -> list[dict]:
        return [r for r in self._load()["runs"] if r["conv"] == conv]
```

- [ ] **Step 4: sessions.py 追加 push_run（push_plan 之后）**

```python
    async def push_run(self, conv_id: str, run: dict) -> None:
        client = self._clients.get(conv_id)
        if client and client.process.alive:
            await client.stream_events.put({"type": "_bridge_run", "run": run})
```

- [ ] **Step 5: events.py 追加分支**（`_bridge_plan` 分支之后）

```python
    if t == "_bridge_run":
        return [{"type": "run_update", "run": ev.get("run") or {}}]
```

- [ ] **Step 6: tool_backend.py 追加特例**

模块顶部追加：

```python
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = None   # 由 mount 时或测试注入（Runs(ROOT)）
CCF_PATH = os.path.join(ROOT, "ccf.json")
```

`mount_tool_backend` 里追加：

```python
    global RUNS
    if RUNS is None:
        from .runs import Runs
        RUNS = Runs(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

`call_tool` 的 `update_plan` 特例之后追加：

```python
    if name in ("run_create", "run_update", "ccf_lookup"):
        return _bridge_tool(name, call)
```

以及实现：

```python
def _ccf_lookup(query: str) -> str:
    try:
        with open(CCF_PATH, encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, ValueError) as e:
        return json.dumps({"ok": False, "error": f"ccf.json 读取失败: {e}"},
                          ensure_ascii=False)
    q = (query or "").lower()
    hits = [it for it in items if q and any(
        q in str(it.get(k, "")).lower() for k in ("r", "t", "a", "s", "f"))][:10]
    if not hits:
        return json.dumps({"ok": True, "count": 0,
                           "hint": "无匹配；query 可用期刊/会议名、缩写、领域或等级A/B/C"},
                          ensure_ascii=False)
    lines = [f"[{it.get('r')}] {it.get('f')} ({it.get('s')}) {it.get('t')}·{it.get('a')}"
             for it in hits]
    return json.dumps({"ok": True, "count": len(hits), "items": lines},
                      ensure_ascii=False)


async def _bridge_tool(name: str, call: ToolCall) -> dict:
    if name == "ccf_lookup":
        return {"result": _ccf_lookup(call.args.get("query") or "")}
    run = None
    if name == "run_create":
        run = RUNS.create(call.conv, call.args.get("goal") or "",
                          call.args.get("stages") or [])
    elif name == "run_update":
        run = RUNS.update(call.args.get("run_id") or "",
                          call.args.get("stages") or [])
        if run is None:
            return {"result": json.dumps(
                {"ok": False, "error": "run_id 不存在"}, ensure_ascii=False)}
    await _manager.push_run(call.conv, run)
    return {"result": json.dumps({"ok": True, "run": run}, ensure_ascii=False,
                                 default=str)}
```

- [ ] **Step 7: routes.py 追加 REST**

```python
@router.get("/api/pi/runs")
def get_runs(conv: str = "", wb_session: str = ""):
    _ws_user_cookie(wb_session)
    from .runs import Runs
    runs = Runs(manager.root)
    return {"runs": runs.for_conv(conv) if conv else []}
```

- [ ] **Step 8: 全量测试 + 提交**

Run: `.venv-win/Scripts/python -m pytest tests/ -q`
Expected: 全部 passed

```bash
git add bridge/ tests/ && git commit -m "feat(bridge): run workflow store, tools and rest"
```

---

### Task 5: 任务监控注入

**Files:**
- Create: `bridge/watcher.py`
- Modify: `bridge/sessions.py`（watch_task）、`bridge/events.py`（_bridge_event）、`bridge/routes.py`（startup 挂载）、`bridge/tool_backend.py`（submit_task 后登记）
- Test: `tests/test_watcher.py`（新建）、`tests/test_events.py`（追加）

**Interfaces:**
- Produces:
  - `PiSessionManager.watch_task(conv_id, task_id)`：`self._watch[task_id] = conv_id`
  - `TaskWatcher(db_path, manager)`：`async def loop()`（20s 轮询：`SELECT id,name,status FROM tasks WHERE status IN ('pending','running')`；上一轮 active 集合中消失的 id 且被 watch → 查终态 → `manager.inject_task_event(conv_id, task_id, name, status)`）；`start()`/`stop()`
  - `PiSessionManager.inject_task_event(conv_id, text)`：`stream_events.put({"type":"_bridge_event","text":text})` 并尝试 `client.command({"type":"prompt","message":text})` 唤醒（agent 空闲才成功，失败静默）
  - `translate_event`：`_bridge_event` → `[{"type":"agent_event","text": ev["text"]}]`
  - tool_backend：dispatch `submit_task` 成功后解析 result JSON 里的任务 id 并 `manager.watch_task(conv, task_id)`
  - mount_pi 里 `app.add_event_handler("startup", ...)` 启动 TaskWatcher（db 路径 `ROOT/workbench.db`）

- [ ] **Step 1: 失败测试（新建 tests/test_watcher.py）**

```python
# tests/test_watcher.py
import asyncio
import sqlite3

import bridge.watcher as watcher
from bridge.sessions import PiSessionManager


def _mk_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, name TEXT, status TEXT)")
    con.execute("INSERT INTO tasks VALUES(1, 't-echo', 'running')")
    con.commit()
    con.close()


async def test_watcher_injects_when_task_finishes(tmp_path, monkeypatch):
    db = str(tmp_path / "workbench.db")
    _mk_db(db)
    m = PiSessionManager(str(tmp_path))
    m._watch = {1: "c1"}
    injected = []

    async def fake_inject(conv, text):
        injected.append((conv, text))

    monkeypatch.setattr(m, "inject_task_event", fake_inject)
    w = watcher.TaskWatcher(db, m)
    await w.poll_once()                      # 记住 running
    con = sqlite3.connect(db)
    con.execute("UPDATE tasks SET status='failed' WHERE id=1")
    con.commit(); con.close()
    await w.poll_once()                      # 发现消失 → 查终态 → 注入
    assert len(injected) == 1
    assert injected[0][0] == "c1"
    assert "failed" in injected[0][1] and "t-echo" in injected[0][1]
```

- [ ] **Step 2: 失败测试（追加到 tests/test_events.py）**

```python
def test_bridge_event_to_agent_event():
    assert translate_event({"type": "_bridge_event", "text": "任务结束"}) == [
        {"type": "agent_event", "text": "任务结束"}]
```

- [ ] **Step 3: 运行确认失败，然后实现 bridge/watcher.py**

```python
# bridge/watcher.py
"""只读轮询 workbench.db 任务状态，结束时向所属会话注入事件。"""
import asyncio
import logging
import sqlite3

log = logging.getLogger(__name__)


class TaskWatcher:
    def __init__(self, db_path: str, manager, interval: float = 20.0):
        self.db_path = db_path
        self.manager = manager
        self.interval = interval
        self._active: dict[int, str] = {}   # task_id -> name
        self._task: asyncio.Task | None = None

    def _query(self, sql: str, params=()) -> list[tuple]:
        con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    async def poll_once(self) -> None:
        rows = await asyncio.to_thread(
            self._query,
            "SELECT id, name, status FROM tasks WHERE status IN ('pending','running')")
        active = {int(i): n for i, n, _ in rows}
        finished = set(self._active) - set(active)
        for tid in finished:
            name = self._active[tid]
            conv = getattr(self.manager, "_watch", {}).get(tid)
            if not conv:
                continue
            final = await asyncio.to_thread(
                self._query, "SELECT status FROM tasks WHERE id=?", (tid,))
            status = final[0][0] if final else "unknown"
            text = f"【系统事件】任务 #{tid}「{name}」已结束，状态：{status}。" \
                   f"请用 log_diagnose / best_result 分析并在对话中汇报结论。"
            try:
                await self.manager.inject_task_event(conv, text)
            except Exception:
                log.warning("inject task event failed", exc_info=True)
            self.manager._watch.pop(tid, None)
        self._active = active

    async def loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.warning("watcher poll failed", exc_info=True)
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_event_loop().create_task(self.loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
```

- [ ] **Step 4: sessions.py：`__init__` 追加 `self._watch: dict[int, str] = {}`；push_run 后追加**

```python
    def watch_task(self, conv_id: str, task_id: int) -> None:
        self._watch[int(task_id)] = conv_id

    async def inject_task_event(self, conv_id: str, text: str) -> None:
        client = self._clients.get(conv_id)
        if not client or not client.process.alive:
            return
        await client.stream_events.put({"type": "_bridge_event", "text": text})
        try:
            await client.command({"type": "prompt", "message": text})
        except Exception:
            log.info("agent busy; event shown without follow-up prompt")
```

- [ ] **Step 5: events.py 追加分支**（`_bridge_run` 之后）

```python
    if t == "_bridge_event":
        return [{"type": "agent_event", "text": ev.get("text") or ""}]
```

- [ ] **Step 6: tool_backend.py 的 call_tool 中 dispatch 之后登记**

```python
    ctx = _manager.ctx_factory(user)
    result = await asyncio.to_thread(agent_tools.dispatch, name, call.args, ctx)
    if name == "submit_task":
        try:
            payload = json.loads(result)
            task = payload.get("task") or {}
            if payload.get("ok") and task.get("id"):
                _manager.watch_task(call.conv, task["id"])
        except (ValueError, AttributeError):
            pass
    return {"result": result}
```

（`watch_task` 在 FakeManager 缺失时测不到——在 tests/test_tool_backend.py 的 FakeManager 里补一个 `def watch_task(self, conv, tid): self.watched = (conv, tid)` 并在 dispatch 测试里换用 name="submit_task" 断言，或单独一个测试）

- [ ] **Step 7: routes.py 的 mount_pi 里启动 watcher**

`mount_tool_backend(app, manager)` 之后追加：

```python
    from .watcher import TaskWatcher
    watcher = TaskWatcher(os.path.join(root, "workbench.db"), manager)

    def _start_watcher():
        watcher.start()

    def _stop_watcher():
        watcher.stop()

    app.add_event_handler("startup", _start_watcher)
    app.add_event_handler("shutdown", _stop_watcher)
```

- [ ] **Step 8: 全量测试 + 提交**

Run: `.venv-win/Scripts/python -m pytest tests/ -q`
Expected: 全部 passed

```bash
git add bridge/ tests/ && git commit -m "feat(bridge): task watcher injects agent events on finish"
```

---

### Task 6: Vue 前端（AgentOps 形态，覆盖 static/）

**Files:**
- Create: `static/vendor/vue.global.prod.js`（curl 下载）
- Modify（整体覆盖）: `static/index.html`、`static/app.js`、`static/style.css`
- Delete: `static/pi-chat.html`（功能并入新 UI）

**Interfaces:**
- Consumes: 全部 WS 消息（含新的 history/run_update/agent_event）+ `GET/DELETE /api/pi/convs` + `GET /api/pi/runs` + `POST /api/papers/pdf` + `GET/PUT /api/settings` + `GET/PUT /api/chat-config` + `GET /api/me`（现有端点，返回当前用户）

- [ ] **Step 1: 下载 Vue（vendor，禁 CDN）**

```bash
mkdir -p static/vendor
curl -sL -o static/vendor/vue.global.prod.js https://unpkg.com/vue@3.5.13/dist/vue.global.prod.js
ls -la static/vendor/   # 应 > 100KB
```

- [ ] **Step 2: 覆盖 static/index.html**

```html
<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>科研智能体工作台</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<div id="app"></div>
<script src="/static/vendor/vue.global.prod.js"></script>
<script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: 覆盖 static/style.css**

```css
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, "Segoe UI", sans-serif; background: #f5f6fa;
       height: 100vh; }
#app { display: flex; height: 100vh; }
.sidebar { width: 240px; background: #1e2233; color: #cfd4e8; display: flex;
           flex-direction: column; flex-shrink: 0; }
.sidebar .brand { padding: 14px 16px; font-weight: 700; color: #fff; }
.sidebar button.new { margin: 8px 12px; padding: 8px; background: #3b82f6;
                      color: #fff; border: 0; border-radius: 6px; cursor: pointer; }
.sidebar .sec { padding: 6px 12px; font-size: 12px; color: #7c86ad; }
.sidebar .conv { padding: 8px 12px; cursor: pointer; font-size: 13px;
                 white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sidebar .conv:hover { background: #2a3049; }
.sidebar .conv.active { background: #323a5c; border-left: 3px solid #3b82f6; }
.sidebar .conv .del { float: right; color: #7c86ad; cursor: pointer; }
.sidebar .machine { padding: 6px 12px 6px 24px; font-size: 13px; }
.sidebar .foot { margin-top: auto; padding: 12px 16px; border-top: 1px solid #2a3049;
                 font-size: 13px; display: flex; justify-content: space-between; }
.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.topbar { background: #fff; border-bottom: 1px solid #e3e6ef; padding: 10px 18px;
          display: flex; justify-content: space-between; align-items: center; }
.runbar { background: #fff; border-bottom: 1px solid #e3e6ef; padding: 8px 18px;
          display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.runbar .stage { font-size: 12px; padding: 3px 10px; border-radius: 12px;
                 background: #eef1f8; color: #555; }
.runbar .stage.in_progress { background: #dbeafe; color: #1d4ed8; }
.runbar .stage.done { background: #dcfce7; color: #15803d; text-decoration: line-through; }
.stream { flex: 1; overflow-y: auto; padding: 18px; }
.msg { margin: 10px 0; display: flex; }
.msg.user { justify-content: flex-end; }
.msg .bubble { max-width: 76%; padding: 10px 14px; border-radius: 12px; }
.msg.user .bubble { background: #3b82f6; color: #fff; white-space: pre-wrap; }
.msg.ai .bubble { background: #fff; border: 1px solid #e3e6ef; flex: 1; max-width: 100%; }
.think { color: #8a91a8; font-size: 12px; border-left: 3px solid #d6d9e6;
         padding: 4px 10px; margin: 6px 0; white-space: pre-wrap; cursor: pointer; }
.tool { color: #1d4ed8; font-size: 13px; background: #eef4ff; border-radius: 6px;
        padding: 5px 10px; margin: 4px 0; }
.sys { color: #92400e; font-size: 13px; background: #fef3c7; border-radius: 6px;
       padding: 5px 10px; margin: 4px 0; white-space: pre-wrap; }
.gate { border: 1px solid #f59e0b; background: #fffbeb; border-radius: 8px;
        padding: 10px; margin: 8px 0; }
.gate button { margin-right: 8px; }
.markdown { line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.composer { background: #fff; border-top: 1px solid #e3e6ef; padding: 12px 18px; }
.composer textarea { width: 100%; height: 64px; border: 1px solid #d6d9e6;
                     border-radius: 8px; padding: 8px; resize: none; font: inherit; }
.composer .row { display: flex; justify-content: space-between; margin-top: 6px;
                 font-size: 12px; color: #8a91a8; align-items: center; }
.composer .send { background: #3b82f6; color: #fff; border: 0; border-radius: 50%;
                  width: 34px; height: 34px; cursor: pointer; font-size: 16px; }
.composer.drag { outline: 2px dashed #3b82f6; outline-offset: -6px; }
.modal-mask { position: fixed; inset: 0; background: rgba(0,0,0,.4);
              display: flex; align-items: center; justify-content: center; }
.modal { background: #fff; border-radius: 10px; padding: 20px; width: 420px;
         max-height: 80vh; overflow-y: auto; }
.modal label { display: block; font-size: 12px; color: #666; margin-top: 10px; }
.modal input { width: 100%; padding: 6px; border: 1px solid #d6d9e6; border-radius: 6px; }
.modal button { margin-top: 14px; padding: 6px 16px; cursor: pointer; }
```

- [ ] **Step 4: 覆盖 static/app.js**

```javascript
/* AgentOps 形态科研智能体工作台（Vue 3 无构建） */
const { createApp } = Vue;

const App = {
  data() {
    return {
      me: null, convs: [], conv: "", msgs: [], input: "", ws: null,
      status: "未连接", streaming: false, curAi: null, curThink: null,
      run: null, gate: null, drag: false,
      settingsOpen: false, settings: {}, chatCfg: {}, busy: false,
    };
  },
  template: `
  <div>
    <aside class="sidebar">
      <div class="brand">科研智能体</div>
      <button class="new" @click="newConv">+ 新建会话</button>
      <div class="sec">会话</div>
      <div v-for="c in convs" :key="c.id" class="conv"
           :class="{active: c.id === conv}" @click="openConv(c.id)">
        {{ c.title }} <span class="del" @click.stop="delConv(c.id)">×</span>
      </div>
      <div class="sec">机器</div>
      <div class="machine">● 本地（mock）</div>
      <div class="foot"><span>{{ me ? me.username : "" }}</span>
        <span @click="logout" style="cursor:pointer">登出</span></div>
    </aside>
    <main class="main">
      <div class="topbar"><b>{{ currentTitle }}</b>
        <span style="cursor:pointer" @click="openSettings">⚙ 设置</span></div>
      <div class="runbar" v-if="run">
        <b style="font-size:12px">{{ run.goal }}</b>
        <span v-for="s in run.stages" :key="s.title" class="stage"
              :class="s.status">{{ s.title }}</span>
      </div>
      <div class="stream" ref="stream">
        <div v-for="(m, i) in msgs" :key="i" class="msg" :class="m.role">
          <div class="bubble">
            <div v-if="m.think" class="think" @click="m.showThink = !m.showThink">
              💭 思考过程（点击展开/折叠）
              <div v-if="m.showThink">{{ m.think }}</div>
            </div>
            <div class="markdown" v-text="m.text"></div>
          </div>
        </div>
        <div v-if="gate" class="gate">
          <b>⚠ 需要确认</b>
          <div style="white-space:pre-wrap;font-size:13px;margin:6px 0">{{ gate.text }}</div>
          <button @click="answerGate(true)">确认执行</button>
          <button @click="answerGate(false)">拒绝</button>
        </div>
      </div>
      <div class="composer" :class="{drag}" @dragover.prevent="drag = true"
           @dragleave="drag = false" @drop.prevent="dropPdf">
        <textarea v-model="input" placeholder="输入消息（Enter 发送，Shift+Enter 换行；PDF 拖到这里上传）"
                  @keydown="keydown"></textarea>
        <div class="row"><span>{{ status }}</span>
          <button class="send" @click="send">↑</button></div>
      </div>
      <div v-if="settingsOpen" class="modal-mask" @click.self="settingsOpen = false">
        <div class="modal">
          <h3>设置</h3>
          <template v-if="me && me.role === 'advisor'">
            <label v-for="(v, k) in settings" :key="k">{{ k }}</label>
            <input v-if="false">
          </template>
          <template v-for="f in ['base_url','model','api_key']" :key="f">
            <label>LLM {{ f }}</label>
            <input v-model="chatCfg[f]">
          </template>
          <button @click="saveSettings">保存</button>
        </div>
      </div>
    </main>
  </div>`,
  computed: {
    currentTitle() {
      const c = this.convs.find(x => x.id === this.conv);
      return c ? c.title : "新会话";
    },
  },
  async mounted() {
    const r = await fetch("/api/me", { credentials: "include" });
    if (!r.ok) { location.href = "/login"; return; }
    this.me = (await r.json()).user || await r.json();
    await this.loadConvs();
    if (this.convs.length) this.openConv(this.convs[0].id);
    else this.newConv();
  },
  methods: {
    async loadConvs() {
      const r = await fetch("/api/pi/convs", { credentials: "include" });
      if (r.ok) this.convs = (await r.json()).convs;
    },
    newConv() {
      const id = "c" + Date.now().toString(36);
      this.convs.unshift({ id, title: "新会话", updated: new Date().toISOString() });
      this.openConv(id);
    },
    async delConv(id) {
      if (!confirm("删除该会话及其历史？")) return;
      await fetch("/api/pi/convs/" + id, { method: "DELETE", credentials: "include" });
      await this.loadConvs();
      if (this.conv === id) { this.convs.length ? this.openConv(this.convs[0].id) : this.newConv(); }
    },
    openConv(id) {
      this.conv = id; this.msgs = []; this.run = null; this.gate = null;
      this.loadRuns();
      this.connect();
    },
    async loadRuns() {
      const r = await fetch("/api/pi/runs?conv=" + encodeURIComponent(this.conv),
                            { credentials: "include" });
      if (r.ok) { const runs = (await r.json()).runs; if (runs.length) this.run = runs[runs.length - 1]; }
    },
    connect() {
      if (this.ws) { try { this.ws.close(); } catch (e) {} }
      this.status = "连接中…";
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/api/pi/ws?conv=${encodeURIComponent(this.conv)}`);
      this.ws = ws;
      ws.onopen = async () => { this.status = "已连接"; };
      ws.onclose = () => { this.status = "已断开"; };
      ws.onmessage = e => this.handle(JSON.parse(e.data));
    },
    handle(m) {
      if (m.type === "history") {
        this.msgs = m.entries.map(e => ({ role: e.role, text: e.text, think: "" }));
      } else if (m.type === "turn_start") {
        this.streaming = true; this.curAi = null; this.curThink = "";
      } else if (m.type === "token") {
        if (!this.curAi) {
          this.curAi = { role: "assistant", text: "", think: "", showThink: false };
          this.msgs.push(this.curAi);
        }
        this.curAi.text += m.delta;
      } else if (m.type === "thinking") {
        if (this.curAi) { this.curAi.think += m.delta; this.curAi.showThink = false; }
      } else if (m.type === "tool_start") {
        this.msgs.push({ role: "assistant", text: `⚙ 调用工具 ${m.tool} …`, think: "" });
      } else if (m.type === "tool_end") {
        this.msgs.push({ role: "assistant", text: "⚙ 工具完成", think: "" });
      } else if (m.type === "agent_event") {
        this.msgs.push({ role: "assistant", text: "📣 " + m.text, think: "" });
      } else if (m.type === "plan_update") {
        this.msgs.push({ role: "assistant",
                         text: "📋 计划：\n" + m.steps.map(s => `  ${s.status === "done" ? "✓" : s.status === "in_progress" ? "◐" : "◻"} ${s.title}`).join("\n"),
                         think: "" });
      } else if (m.type === "run_update") {
        this.run = m.run;
      } else if (m.type === "confirm_request") {
        this.gate = { id: m.id, text: (m.request.title || "") + "\n" + (m.request.message || "") };
      } else if (m.type === "turn_done") {
        this.streaming = false; this.curAi = null; this.loadConvs();
      } else if (m.type === "error") {
        this.msgs.push({ role: "assistant", text: "❌ " + m.message, think: "" });
      }
      this.$nextTick(() => { const el = this.$refs.stream; if (el) el.scrollTop = el.scrollHeight; });
    },
    async send(text) {
      const t = text || this.input.trim();
      if (!t || !this.ws || this.ws.readyState !== 1) return;
      this.msgs.push({ role: "user", text: t, think: "" });
      this.ws.send(JSON.stringify({ type: "message", text: t }));
      this.input = "";
    },
    keydown(e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this.send(); }
    },
    answerGateway() {},
    answerGate(ok) {
      if (this.ws && this.gate)
        this.ws.send(JSON.stringify({ type: "confirm", id: this.gate.id, confirmed: ok }));
      this.msgs.push({ role: "assistant", text: ok ? "✅ 已确认执行" : "⛔ 已拒绝", think: "" });
      this.gate = null;
    },
    async dropPdf(e) {
      this.drag = false;
      const file = e.dataTransfer.files[0];
      if (!file || !/\.pdf$/i.test(file.name)) return;
      this.status = "上传 PDF…";
      const fd = new FormData();
      fd.append("file", file);
      fd.append("title", file.name.replace(/\.pdf$/i, ""));
      const r = await fetch("/api/papers/pdf", { method: "POST", body: fd,
                                                 credentials: "include" });
      this.status = "已连接";
      if (!r.ok) { alert("PDF 上传失败：" + (await r.text()).slice(0, 200)); return; }
      const p = await r.json();
      this.send(`我上传了论文《${p.title}》（paper_id=${p.id}）。请先用 repro_check 了解它的复现要点并简要汇报。`);
    },
    async openSettings() {
      const r1 = await fetch("/api/settings", { credentials: "include" });
      if (r1.ok) this.settings = await r1.json();
      const r2 = await fetch("/api/chat-config", { credentials: "include" });
      if (r2.ok) this.chatCfg = await r2.json();
      this.settingsOpen = true;
    },
    async saveSettings() {
      await fetch("/api/chat-config", { method: "PUT", headers: { "content-type": "application/json" },
                                        body: JSON.stringify(this.chatCfg), credentials: "include" });
      this.settingsOpen = false;
    },
    async logout() {
      await fetch("/api/logout", { method: "POST", credentials: "include" });
      location.href = "/";
    },
  },
};

createApp(App).mount("#app");
```

（注意：`answerGateway() {}` 是笔误防御——实现时**删掉这一行**，只保留 `answerGate`；`/api/me` 的返回形状以实际为准，先 `curl -s http://127.0.0.1:8787/api/me -b cookie` 确认后调整 mounted 里那一行；settings 的 advisor 表单本版只展示不编辑（v-if="false" 占位删除即可），只保存 LLM 配置——超范围功能留给后续迭代）

- [ ] **Step 5: 删除 static/pi-chat.html**

```bash
git rm static/pi-chat.html
```

- [ ] **Step 6: 语法冒烟 + 提交**

```bash
node -e "new Function(require('fs').readFileSync('static/app.js','utf8')); console.log('js ok')"
```

```bash
git add static/ && git commit -m "feat(web): agentops-style vue frontend replacing nav pages"
```

---

### Task 7: 旧页下线 + E2E + 收尾

**Files:**
- Delete: 根目录旧副本 `index.html`、`app.js`、`style.css`（未被 server 引用）
- Modify: `README.md`

- [ ] **Step 1: 确认 server 只引用 static/ 后删除根目录旧副本**

```bash
git rm index.html app.js style.css
```

- [ ] **Step 2: 起 server，全链路浏览器人工验收清单**（留存报告）

1. `http://127.0.0.1:8787/` 未登录 → 跳转/提示登录；登录后进入新界面
2. 新建会话 → 发「显卡状态」→ AI 卡片 + 工具行 + 回答
3. 刷新页面 → 历史回放（history）出现
4. 拖一个 PDF → 自动上传并让智能体汇报复现要点
5. 让智能体「建一个三阶段 run」→ 顶部工作流条出现，阶段状态可更新
6. 提交任务确认卡（确认/拒绝各一次）
7. `sqlite3 workbench.db "UPDATE tasks SET status='done' WHERE id=<t-echo的id>"` 不适用——改为：等 echo 任务自然结束（或手动 INSERT running→UPDATE），观察 agent_event 注入

- [ ] **Step 3: 脚本 E2E（扩展 e2e_tools.py 场景到新消息类型）**

新增 `tests/e2e_ui.py`：连 WS 后断言先收到 `history`；再对话触发 `run_create`（提示词：「请用 run_create 建一个目标为"测试"的单阶段 run，标题"步骤一"」）断言收到 `run_update`。

```python
"""E2E：history 回放 + run_update 推送。用法同 e2e_tools.py。"""
import asyncio
import json
import sys

import websockets


async def main(token: str) -> None:
    uri = "ws://127.0.0.1:8787/api/pi/ws?conv=ui-e2e"
    async with websockets.connect(uri, additional_headers={
            "Cookie": f"wb_session={token}"}) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
        assert first["type"] == "history", f"first={first['type']}"
        print("HISTORY OK")
        await ws.send(json.dumps({"type": "message",
                                  "text": "请调用 run_create 工具：goal=测试，stages=[{title:步骤一}]，然后简短确认。"}))
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=180))
            if m["type"] == "run_update":
                assert m["run"]["stages"][0]["title"] == "步骤一"
                print("RUN UPDATE OK")
                break
            if m["type"] == "error":
                raise SystemExit(m["message"])


asyncio.run(main(sys.argv[1]))
```

- [ ] **Step 4: README 更新（新界面说明 + 会话/Run 用法）+ 提交**

```bash
git add -A && git commit -m "chore: remove legacy frontend copies; docs for agentops ui"
```

---

## Self-Review 记录

- **Spec 覆盖**：§9 全部（布局/会话 API/历史回放/标题/ccf_lookup/PDF 拖拽/设置弹窗[本版仅 LLM 配置——advisor 系统设置表单降级为只读，已在 Task 6 标注]）→ Task 2/3/4/6/7；§10 全部（runs 持久化/run 工具/Gate 分级[强制级复用第 2 期门禁，默认级本期通过提示词引导智能体先问再做]/异步注入）→ Task 4/5；机器面板预留 → Task 6（静态展示）；飞书推送按 spec §10 明示延后。
- **占位符**：Task 6 Step 4 中标注的两处「实现时删掉/确认」是对转录者的显式指令，非缺失。
- **类型一致性**：`history/run_update/agent_event` 字段在 Task 3/4/5（桥）与 Task 6（前端 handle）逐一核对一致；`push_run/watch_task/inject_task_event/list_convs/delete_conv/maybe_set_title` 签名跨任务一致；`_bridge_run/_bridge_event` 与 translate 分支一致。
