# 科研执行智能体 · 第 2 期（工具 + 确认门禁）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pi 智能体获得 12 个科研工具（11 个复用现有 `agent/tools.py` + 新增 `update_plan`），写操作经网页确认门禁，网页端渲染计划进度卡。

**Architecture:** 桥内新增 localhost-only 工具后端（token 鉴权），直接调 `agent.tools.dispatch(name, args, ctx)` 复用全部业务逻辑；Pi 扩展（TS，装在 `~/.pi/agent/extensions/research/`）在启动时从桥拉取工具 schema 动态注册，每个工具是调桥 HTTP 的薄壳；扩展的 `tool_call` 钩子拦截危险工具/命令，经 `ctx.ui.confirm` → RPC `extension_ui_request` → 桥 → 网页弹窗（第 1 期通路已建好）。

**Tech Stack:** 同第 1 期（FastAPI + Pi 1.3.0 + GLM）；扩展用纯 TS（jiti 加载，无编译、无 npm 依赖，Node ≥ 18 自带 fetch）。

**Spec:** `docs/superpowers/specs/2026-10-17-pi-research-agent-design.md` §3.2、§3.3（本计划覆盖其 §7 第 2 期；submit_task 本期只入本地队列，多服务器路由是第 3 期）

## Global Constraints

- 工作目录 `D:\gpu-workbench`（bash 路径 `/d/gpu-workbench`，命令均在仓库根执行）
- Python 一律 `.venv-win/Scripts/python`；测试 `.venv-win/Scripts/python -m pytest tests/ -v`
- 工具后端鉴权：请求头 `X-Bridge-Token` == manager.bridge_token（mount 时 `secrets.token_urlsafe(24)` 生成）；conv 必须已通过 WS 连接（有 user 记录），否则 403
- Pi spawn 环境变量：`ZHIPU_API_KEY`、`PI_BRIDGE_URL`（默认 `http://127.0.0.1:8787`，读 `WORKBENCH_PORT`）、`PI_BRIDGE_TOKEN`、`PI_CONV`（= conv_id）
- 桥→网页新增消息类型：`{"type":"plan_update","steps":[{"title":str,"status":"pending|in_progress|done"}]}`
- 工具 schema 单一事实来源：`agent/tools.py::openai_tools()`（扩展不得手写副本）
- 危险工具名单（需确认）：`stop_task`、`submit_task`、`save_report`；`bash` 命中正则 `(rm\s+-rf|git\s+push\s+.*--force|git\s+reset\s+--hard|shutdown|reboot)` 亦需确认，180 秒超时视为拒绝
- 网页 WS 协议、既有消息类型名（token/thinking/turn_* 等）不得改动
- 每任务结束 git commit

---

### Task 1: PiSessionManager 扩展（token / conv→user / plan 推送）

**Files:**
- Modify: `bridge/sessions.py`
- Modify: `bridge/events.py`（加 `_bridge_plan` 分支）
- Test: `tests/test_sessions.py`（追加）、`tests/test_events.py`（追加）

**Interfaces:**
- Produces（后续任务依赖，签名精确如下）:
  - `PiSessionManager.bridge_token: str | None`、`bridge_url: str | None`、`ctx_factory: Callable | None`（属性，由 mount_pi 赋值）
  - `user_for(conv_id: str) -> dict | None`
  - `async get(conv_id: str, user: dict | None = None) -> PiClient`（签名变更：记录 user）
  - `async push_plan(conv_id: str, steps: list) -> None`（存 `self._plans[conv_id]` 并向存活 client 的 `stream_events` 放 `{"type": "_bridge_plan", "steps": steps}`）
  - `_pi_env(conv_id)` 取代 `_zhipu_env`：额外含 `PI_BRIDGE_URL`/`PI_BRIDGE_TOKEN`/`PI_CONV`
  - `translate_event` 对 `{"type":"_bridge_plan","steps":S}` 输出 `[{"type":"plan_update","steps":S}]`

- [ ] **Step 1: 写失败测试（追加到 tests/test_sessions.py 末尾）**

```python
class _FakeClient:
    def __init__(self):
        from bridge.pi_process import PiProcess
        self.process = PiProcess([], cwd=".")
        self.stream_events = __import__("asyncio").Queue()


async def test_push_plan_stores_and_emits():
    import asyncio
    from bridge.sessions import PiSessionManager
    m = PiSessionManager(".")
    m._clients["c1"] = _FakeClient()
    await m.push_plan("c1", [{"title": "步骤一", "status": "done"}])
    assert m._plans["c1"] == [{"title": "步骤一", "status": "done"}]
    ev = await asyncio.wait_for(m._clients["c1"].stream_events.get(), timeout=1)
    assert ev == {"type": "_bridge_plan", "steps": [{"title": "步骤一", "status": "done"}]}


async def test_push_plan_without_client_just_stores():
    from bridge.sessions import PiSessionManager
    m = PiSessionManager(".")
    await m.push_plan("ghost", [{"title": "x", "status": "pending"}])  # 不抛异常
    assert m._plans["ghost"] == [{"title": "x", "status": "pending"}]


def test_user_for_and_pi_env():
    import os
    from bridge.sessions import PiSessionManager
    m = PiSessionManager(".")
    assert m.user_for("nobody") is None
    m._users["c1"] = {"username": "u", "role": "advisor"}
    assert m.user_for("c1") == {"username": "u", "role": "advisor"}
    m.bridge_token = "tok"
    m.bridge_url = "http://127.0.0.1:8787"
    (tmp := m._pi_env("c1"))
    assert tmp["PI_CONV"] == "c1"
    assert tmp["PI_BRIDGE_TOKEN"] == "tok"
    assert tmp["PI_BRIDGE_URL"] == "http://127.0.0.1:8787"
```

（`test_push_plan_stores_and_emits` 里 `_FakeClient.process.alive` 为 False，覆盖「client 不存活时也不抛异常」的分支；真正存活的推送路径由 Task 5 E2E 验证）

- [ ] **Step 2: 写失败测试（追加到 tests/test_events.py 末尾）**

```python
def test_bridge_plan_to_plan_update():
    ev = {"type": "_bridge_plan", "steps": [{"title": "s", "status": "done"}]}
    assert translate_event(ev) == [
        {"type": "plan_update", "steps": [{"title": "s", "status": "done"}]}]
```

- [ ] **Step 3: 运行确认失败**

Run: `.venv-win/Scripts/python -m pytest tests/test_sessions.py tests/test_events.py -v`
Expected: 新增 4 个测试 FAIL（AttributeError: push_plan / user_for / _pi_env / 断言不等）

- [ ] **Step 4: 修改 bridge/sessions.py**

`__init__` 里追加三个属性和两个字典：

```python
        self.bridge_token: str | None = None    # 由 mount_pi 赋值
        self.bridge_url: str | None = None      # 由 mount_pi 赋值
        self.ctx_factory = None                 # 由 mount_pi 赋值（server._agent_ctx）
        self._users: dict[str, dict] = {}
        self._plans: dict[str, list] = {}
```

`_zhipu_env` 整体替换为 `_pi_env`（保留读 config.json 的逻辑）：

```python
    def _pi_env(self, conv_id: str) -> dict:
        env: dict = {}
        cfg_path = os.path.join(self.root, "config.json")
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("api_key"):
                env["ZHIPU_API_KEY"] = cfg["api_key"]
        except (OSError, ValueError):
            pass
        if self.bridge_url:
            env["PI_BRIDGE_URL"] = self.bridge_url
        if self.bridge_token:
            env["PI_BRIDGE_TOKEN"] = self.bridge_token
        env["PI_CONV"] = conv_id
        return env
```

`get` 签名与开头改为：

```python
    async def get(self, conv_id: str, user: dict | None = None) -> PiClient:
        if user is not None:
            self._users[conv_id] = user
        client = self._clients.get(conv_id)
        if client and client.process.alive:
            return client
        proc = PiProcess(self._spawn_args(conv_id), cwd=self.root,
                         env=self._pi_env(conv_id))
```

（其余不变）`shutdown` 后追加两个方法：

```python
    def user_for(self, conv_id: str) -> dict | None:
        return self._users.get(conv_id)

    async def push_plan(self, conv_id: str, steps: list) -> None:
        self._plans[conv_id] = steps
        client = self._clients.get(conv_id)
        if client and client.process.alive:
            await client.stream_events.put(
                {"type": "_bridge_plan", "steps": steps})
```

- [ ] **Step 5: bridge/events.py 的 translate_event 追加分支**（放在 `_process_exit` 分支之前）

```python
    if t == "_bridge_plan":
        return [{"type": "plan_update", "steps": ev.get("steps") or []}]
```

- [ ] **Step 6: 运行确认通过 + 全量回归**

Run: `.venv-win/Scripts/python -m pytest tests/ -q`
Expected: 全部 passed（含既有 26 个；`tests/test_sessions.py::test_zhipu_env_reads_config` 和 `test_zhipu_env_empty_without_config` 调用的是旧方法名，改为对 `_pi_env("c")` 断言 `ZHIPU_API_KEY` 键）

（同步修改那两个旧测试：`m._zhipu_env()` → `m._pi_env("c")`）

- [ ] **Step 7: 提交**

```bash
git add bridge/sessions.py bridge/events.py tests/test_sessions.py tests/test_events.py
git commit -m "feat(bridge): manager carries bridge token, conv users, plan push"
```

---

### Task 2: 工具后端端点 + mount_pi 接线

**Files:**
- Create: `bridge/tool_backend.py`
- Modify: `bridge/routes.py`（mount_pi 签名、pi_ws 传 user）
- Modify: `server.py`（mount_pi 调用传 ctx_factory）
- Test: `tests/test_tool_backend.py`、`tests/test_routes.py`（FakeManager 适配）

**Interfaces:**
- Consumes: Task 1 的 manager 属性/方法；`agent.tools.openai_tools()`、`agent.tools.dispatch(name, args, ctx)`
- Produces:
  - `mount_pi(app, root, resolve_token_fn, ctx_factory=None)`（新增第 4 参）
  - `GET /api/pi/tools/schema` → `{"tools": openai_tools()}`（X-Bridge-Token 鉴权）
  - `POST /api/pi/tools/{name}` body `{"args": {...}, "conv": "..."}` → `{"result": "<agent.tools.dispatch 返回的 JSON 字符串>"}`；name == `update_plan` 时走 `manager.push_plan` 返回 `{"result": "{\"ok\": true}"}`
  - `bridge.tool_backend.mount_tool_backend(app, manager)`（模块级 `_manager` 由它设置）

- [ ] **Step 1: 写失败测试 tests/test_tool_backend.py**

```python
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

    def user_for(self, conv_id):
        return self._users.get(conv_id)

    async def push_plan(self, conv_id, steps):
        self.plans[conv_id] = steps


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
```

（ctx_factory 的注入在 `tb._manager.ctx_factory = lambda user: {"ctx": True}` — 写进 `test_call_tool_dispatches_and_wraps_result` 的 `_client` 之后：`tb._manager.ctx_factory = lambda user: {"ctx": True}`）

- [ ] **Step 2: 运行确认失败**

Run: `.venv-win/Scripts/python -m pytest tests/test_tool_backend.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'bridge.tool_backend'`

- [ ] **Step 3: 实现 bridge/tool_backend.py**

```python
# bridge/tool_backend.py
"""localhost-only 工具后端：Pi 扩展的薄壳工具经此调用 agent.tools 业务逻辑。"""
import asyncio
import json

from agent import tools as agent_tools
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

router = APIRouter()
_manager = None  # 由 mount_tool_backend 注入


def mount_tool_backend(app, manager) -> None:
    global _manager
    _manager = manager
    app.include_router(router)


def _check(token: str) -> None:
    if not _manager or not _manager.bridge_token or token != _manager.bridge_token:
        raise HTTPException(403, "bridge token 无效")


class ToolCall(BaseModel):
    args: dict = {}
    conv: str = "default"


@router.get("/api/pi/tools/schema")
def tools_schema(x_bridge_token: str = Header(default="")):
    _check(x_bridge_token)
    return {"tools": agent_tools.openai_tools()}


@router.post("/api/pi/tools/{name}")
async def call_tool(name: str, call: ToolCall,
                    x_bridge_token: str = Header(default="")):
    _check(x_bridge_token)
    user = _manager.user_for(call.conv)
    if user is None:
        raise HTTPException(403, f"会话 {call.conv} 未连接或已过期")
    if name == "update_plan":
        steps = call.args.get("steps") or []
        await _manager.push_plan(call.conv, steps)
        return {"result": json.dumps({"ok": True}, ensure_ascii=False)}
    if _manager.ctx_factory is None:
        raise HTTPException(503, "ctx_factory 未配置（server 未接入）")
    ctx = _manager.ctx_factory(user)
    result = await asyncio.to_thread(agent_tools.dispatch, name, call.args, ctx)
    return {"result": result}
```

- [ ] **Step 4: 修改 bridge/routes.py**

import 区追加：

```python
import os
import secrets
```

`mount_pi` 整体替换为：

```python
def mount_pi(app, root, resolve_token_fn, ctx_factory=None) -> None:
    """挂载 /api/pi/ws 与工具后端。延迟 import 避免与 server.py 循环依赖。"""
    global manager, _resolve_token
    from .sessions import PiSessionManager
    from .tool_backend import mount_tool_backend
    manager = PiSessionManager(root)
    manager.bridge_token = secrets.token_urlsafe(24)
    manager.bridge_url = f"http://127.0.0.1:{os.environ.get('WORKBENCH_PORT', '8787')}"
    manager.ctx_factory = ctx_factory
    _resolve_token = resolve_token_fn
    app.include_router(router)
    mount_tool_backend(app, manager)
```

`pi_ws` 中 `client = await manager.get(conv)` 改为：

```python
    client = await manager.get(conv, user)
```

- [ ] **Step 5: 修改 server.py 挂载调用**

`mount_pi(app, ROOT, resolve_token)` 改为：

```python
mount_pi(app, ROOT, resolve_token, ctx_factory=_agent_ctx)
```

- [ ] **Step 6: 适配 tests/test_routes.py**

`FakeManager.get` 签名改为 `async def get(self, conv_id, user=None): return self.client`（routes 现在传 user）。

- [ ] **Step 7: 运行确认通过 + 全量**

Run: `.venv-win/Scripts/python -m pytest tests/ -q && WORKBENCH_MOCK=1 .venv-win/Scripts/python -c "import server; print('ok')"`
Expected: 全部 passed；`ok`

- [ ] **Step 8: 提交**

```bash
git add bridge/tool_backend.py bridge/routes.py server.py tests/
git commit -m "feat(bridge): localhost tool backend reusing agent.tools.dispatch"
```

---

### Task 3: Pi 扩展（动态工具注册 + 确认门禁）

**Files:**
- Create: `pi-extension/research/index.ts`（源码入库）
- 安装（不入库）：拷贝到 `C:\Users\ZhouYan\.pi\agent\extensions\research\index.ts`

**Interfaces:**
- Consumes: Task 2 的 `GET /api/pi/tools/schema`、`POST /api/pi/tools/{name}`；环境变量 `PI_BRIDGE_URL`/`PI_BRIDGE_TOKEN`/`PI_CONV`
- Produces: Pi 会话内 12 个工具（11 个动态 + `update_plan`）+ 危险操作确认钩子

- [ ] **Step 1: 写 pi-extension/research/index.ts**

```typescript
// Pi 扩展：科研工具薄壳 + 确认门禁。
// 工具 schema 启动时从桥拉取（单一事实来源 agent/tools.py::openai_tools），
// 每个工具把参数 POST 到桥的 /api/pi/tools/{name}，业务逻辑全部在 Python 侧。
// 环境变量：PI_BRIDGE_URL / PI_BRIDGE_TOKEN / PI_CONV（由 bridge/sessions.py 注入；
// 手动跑 pi 命令时没有这些变量，扩展自动不加载。）
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const CONFIRM_TOOLS = new Set(["stop_task", "submit_task", "save_report"]);
const DANGEROUS_BASH =
  /(rm\s+-rf|git\s+push\s+.*--force|git\s+reset\s+--hard|shutdown|reboot)/i;

interface FnDef {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

function bridgeCfg() {
  const url = process.env.PI_BRIDGE_URL;
  const token = process.env.PI_BRIDGE_TOKEN;
  const conv = process.env.PI_CONV || "default";
  if (!url || !token) return null;
  return { url, token, conv };
}

async function callBridge(name: string, args: Record<string, unknown>) {
  const cfg = bridgeCfg();
  if (!cfg) throw new Error("bridge env missing");
  const res = await fetch(`${cfg.url}/api/pi/tools/${name}`, {
    method: "POST",
    headers: { "content-type": "application/json", "x-bridge-token": cfg.token },
    body: JSON.stringify({ args, conv: cfg.conv }),
  });
  if (!res.ok) throw new Error(`bridge ${name} HTTP ${res.status}`);
  const data = (await res.json()) as { result: string };
  return data.result;
}

function resultOf(text: string, isError: boolean) {
  return { content: [{ type: "text" as const, text }], details: {}, isError };
}

export default async function (pi: ExtensionAPI) {
  const cfg = bridgeCfg();
  if (!cfg) return; // 手动 pi CLI 会话：不注入科研工具

  // 1) 动态拉取并注册 11 个科研工具
  let defs: FnDef[] = [];
  try {
    const res = await fetch(`${cfg.url}/api/pi/tools/schema`, {
      headers: { "x-bridge-token": cfg.token },
    });
    if (res.ok) {
      const data = (await res.json()) as { tools: { function: FnDef }[] };
      defs = data.tools.map((t) => t.function);
    }
  } catch {
    // 桥暂不可达（如服务器重启中）：不阻塞会话，本轮无科研工具
  }
  for (const f of defs) {
    pi.registerTool({
      name: f.name,
      label: f.name,
      description: f.description,
      promptSnippet: `${f.name}: ${f.description.split("。")[0]}。`,
      parameters: f.parameters as never,
      async execute(_toolCallId, args) {
        try {
          const text = await callBridge(f.name, (args || {}) as Record<string, unknown>);
          let isError = false;
          try { isError = JSON.parse(text).ok === false; } catch { /* 非 JSON 结果 */ }
          return resultOf(text, isError);
        } catch (e) {
          return resultOf(`工具 ${f.name} 调用失败: ${e}`, true);
        }
      },
    });
  }

  // 2) update_plan：维护计划进度卡
  pi.registerTool({
    name: "update_plan",
    label: "Update plan",
    description:
      "更新当前任务的计划/待办清单（网页端渲染为进度卡）。开始多步任务时先建计划；每完成一步把对应 step 的 status 改为 done；正在做的改为 in_progress。",
    promptSnippet:
      "update_plan: 多步任务开始前建立计划，之后随进度更新 step 状态（pending/in_progress/done）。",
    parameters: {
      type: "object",
      properties: {
        steps: {
          type: "array",
          items: {
            type: "object",
            properties: {
              title: { type: "string" },
              status: { type: "string", enum: ["pending", "in_progress", "done"] },
            },
            required: ["title", "status"],
          },
        },
      },
      required: ["steps"],
    } as never,
    async execute(_toolCallId, args) {
      try {
        await callBridge("update_plan", (args || {}) as Record<string, unknown>);
        return resultOf("计划已更新", false);
      } catch (e) {
        return resultOf(`update_plan 失败: ${e}`, true);
      }
    },
  });

  // 3) 确认门禁：危险工具/命令先经网页弹窗
  pi.on("tool_call", async (event, ctx) => {
    const name = event.toolName;
    let what = "";
    if (CONFIRM_TOOLS.has(name)) {
      what = `工具 ${name}\n参数: ${JSON.stringify(event.input ?? {})}`;
    } else if (name === "bash") {
      const cmd = String((event.input as { command?: string })?.command ?? "");
      if (DANGEROUS_BASH.test(cmd)) what = `危险命令: ${cmd}`;
    }
    if (!what) return;
    const ok = await ctx.ui.confirm("确认操作", `${what}\n\n允许执行吗？`, {
      timeout: 180_000,
    });
    if (!ok) return { block: true, reason: "用户拒绝了该操作（或 180 秒超时未确认）" };
  });
}
```

- [ ] **Step 2: 安装到全局扩展目录**

```bash
mkdir -p "C:\Users\ZhouYan\.pi\agent\extensions\research"
cp pi-extension/research/index.ts "C:\Users\ZhouYan\.pi\agent\extensions\research\index.ts"
```

- [ ] **Step 3: 语法冒烟（扩展能否被 Pi 加载，不依赖桥）**

```bash
cd /tmp && PI_BRIDGE_URL= PI_BRIDGE_TOKEN= pi -p "回复 ok" --provider zhipu --model glm-4.7 2>&1 | tail -3
```

（在 /tmp 下跑避免项目信任问题；环境变量为空时扩展自动跳过加载。Expected: 输出 ok，无 extension_error）

- [ ] **Step 4: 提交**

```bash
git add pi-extension/research/index.ts
git commit -m "feat(pi-ext): research tools thin shell with confirm gate"
```

---

### Task 4: 前端计划卡与工具渲染

**Files:**
- Modify: `static/pi-chat.html`

**Interfaces:**
- Consumes: `{"type":"plan_update","steps":[{"title","status"}]}`、既有 tool_start/tool_end

- [ ] **Step 1: header 下方加计划容器**

在 `<div id="log"></div>` 之前插入：

```html
<div id="plan" style="display:none; margin:8px 16px; padding:8px 12px;
     background:#fff8e1; border:1px solid #ffe082; border-radius:8px;
     font-size:13px;"></div>
```

- [ ] **Step 2: handle() 里加 plan_update 分支，并加渲染函数**

`handle` 的 `else if (m.type === "error")` 之前插入：

```javascript
  } else if (m.type === "plan_update") {
    renderPlan(m.steps);
```

脚本区追加：

```javascript
function renderPlan(steps) {
  const el = $("plan");
  if (!steps.length) { el.style.display = "none"; return; }
  el.style.display = "block";
  const icon = { pending: "◻", in_progress: "◐", done: "✓" };
  el.innerHTML = "<b>任务计划</b><br>" + steps.map(s =>
    `${icon[s.status] || "◻"} ${s.status === "done" ? "<s>" : ""}${s.title}${s.status === "done" ? "</s>" : ""}`
  ).join("<br>");
}
```

- [ ] **Step 3: 提交**

```bash
git add static/pi-chat.html
git commit -m "feat(web): plan progress card in pi chat"
```

---

### Task 5: 端到端验收（工具链 + 确认门禁）

**Files:**
- Create: `tests/e2e_tools.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: 前面全部任务；真实 Pi + GLM + 本地 mock GPU + 本地 SQLite

- [ ] **Step 1: 写 tests/e2e_tools.py**

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


async def turn(ws, text, auto_confirm=False, expect_tool=None):
    await ws.send(json.dumps({"type": "message", "text": text}))
    got, tools, confirmed = "", [], False
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=180))
        t = m["type"]
        if t == "token":
            got += m["delta"]
        elif t == "tool_start":
            tools.append(m.get("tool"))
        elif t == "confirm_request" and auto_confirm and not confirmed:
            confirmed = True
            await ws.send(json.dumps(
                {"type": "confirm", "id": m["id"], "confirmed": True}))
        elif t == "error":
            raise SystemExit(f"error: {m['message']}")
        elif t == "turn_done":
            return got, tools


async def main(token: str) -> None:
    async with websockets.connect(BASE, additional_headers={
            "Cookie": f"wb_session={token}"}) as ws:
        got, tools = await turn(
            ws, "请调用 gpu_status 工具查看显卡，然后用一句话总结空闲情况。")
        print("turn1 tools:", tools)
        print("turn1 reply:", got[:120])
        assert "gpu_status" in tools, "GLM 未调用 gpu_status"
        print("TOOL E2E OK")

        got, tools = await turn(
            ws, "请提交一个测试任务：名字 t-echo，命令 echo hello，需要 1 张 GPU。",
            auto_confirm=True)
        print("turn2 tools:", tools)
        print("turn2 reply:", got[:120])
        assert "submit_task" in tools, "GLM 未调用 submit_task"
        print("CONFIRM-GATE E2E OK")


asyncio.run(main(sys.argv[1]))
```

- [ ] **Step 2: 重启 server 并运行**

```bash
# 若旧 server 还在跑先停掉
WORKBENCH_MOCK=1 .venv-win/Scripts/python server.py &   # 后台
sleep 6
TOKEN=$(curl -s -X POST http://127.0.0.1:8787/api/login -H "Content-Type: application/json" \
  -d '{"username":"niexin","password":"niexin"}' | python -c "import json,sys;print(json.load(sys.stdin)['token'])")
PYTHONIOENCODING=utf-8 .venv-win/Scripts/python -u tests/e2e_tools.py "$TOKEN"
```

Expected: `TOOL E2E OK` 与 `CONFIRM-GATE E2E OK`。
若 gpu_status 未被调用：多为扩展没加载，检查 `C:\Users\ZhouYan\.pi\agent\extensions\research\index.ts` 存在、server 日志里 Pi stderr 无 extension_error。

- [ ] **Step 3: 浏览器人工验收**

浏览器开 `/static/pi-chat.html`，conv 用 `tools-ui`：
1. 问「调用 gpu_status 看看显卡」→ 出现 ⚙ 工具卡 + 回答
2. 问「提交任务：名字 t-hello，命令 echo hi，1 张卡」→ 弹确认框；点「确认」→ 工具执行；再问一次点「拒绝」→ 回复包含「拒绝」字样且任务未入队
3. 问「帮我规划：复现 X 论文的三个步骤」并要求用 update_plan → 页面顶部出现黄色计划卡

- [ ] **Step 4: README 追加 + 提交**

README「本地开发」节追加一行：

```markdown
- Pi 扩展：改 `pi-extension/research/index.ts` 后需重新拷贝到
  `C:\Users\ZhouYan\.pi\agent\extensions\research\index.ts` 并重连会话生效
```

```bash
git add tests/e2e_tools.py README.md
git commit -m "test: e2e for tools and confirm gate; docs"
```

---

## Self-Review 记录

- **Spec 覆盖**：§3.2 十个工具 → Task 2/3（submit_train 的多服务器路由按 spec 属第 3 期，本期 submit_task 入本地队列）；§3.3 确认门禁 → Task 3 Step 1.3 + Task 5；计划卡（§3.2 update_plan）→ Task 1/4；`服务器注册表`（§3.5）明确不在本期。
- **占位符**：无 TBD；Task 5 Step 3 的人工验收是验收清单不是实现占位。
- **类型一致性**：`push_plan(conv_id, steps)` / `user_for` / `_pi_env(conv_id)` 在 Task 1/2 间一致；`plan_update` 消息字段与 Task 4 渲染一致；`x-bridge-token` 头在 Task 2/3 一致；FakeManager.get 双参签名与 routes.py 调用一致。
- **已知风险**（E2E 兜底）：registerTool 接受纯 JSON Schema 参数（typebox 兼容）是文档隐含行为，Task 5 Step 2 的排错说明覆盖了失败情形。
