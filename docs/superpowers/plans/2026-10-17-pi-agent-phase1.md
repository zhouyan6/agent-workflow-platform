# 科研执行智能体 · 第 1 期（Pi 骨架）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 本地（Windows）跑通「浏览器 ↔ FastAPI 桥 ↔ Pi（RPC 模式，GLM）」的流式对话，含会话持久化与恢复。

**Architecture:** FastAPI 桥（`bridge/` 新包，挂进现有 server.py）spawn `pi --mode rpc` 子进程，用 JSONL 协议收发命令/事件；事件翻译成网页 WebSocket 消息流。前端第 1 期用独立最小页 `static/pi-chat.html`，不动现有 app.js。

**Tech Stack:** Python 3.13（`.venv-win`）、FastAPI/uvicorn（已有）、Node ≥ 20 + `@earendil-works/pi-coding-agent`（Pi 0.99.x）、智谱 GLM（OpenAI 兼容）。

**Spec:** `docs/superpowers/specs/2026-10-17-pi-research-agent-design.md`（本计划覆盖其 §7 第 1 期）

## Global Constraints

- 工作目录 `D:\gpu-workbench`（bash 路径 `/d/gpu-workbench`，下文命令均在仓库根执行）
- Python 一律用 `.venv-win/Scripts/python`；测试用 pytest（`requirements-dev.txt`）
- Pi RPC JSONL 只以 `\n` 分行，容忍行尾 `\r`，**不得**用按 Unicode 分隔符切分的 reader
- 会话 cookie 名 `wb_session`，鉴权复用 server.py 的 `resolve_token`（注入，不得循环 import server）
- 网页→桥 WebSocket 消息类型：`{type:"message",text}` / `{type:"abort"}` / `{type:"confirm",id,confirmed}`
- 桥→网页消息类型：`token` / `thinking` / `turn_start` / `turn_done` / `tool_start` / `tool_end` / `confirm_request` / `error`（字段见 Task 5，后续任务不得改名）
- GLM 密钥从现有 `config.json` 的 `api_key` 读，经环境变量 `ZHIPU_API_KEY` 传给 Pi，不写入任何新文件
- 每个任务结束必须 git commit

---

### Task 0: 版本控制与依赖基线

**Files:**
- Create: `.gitignore`
- Create: `requirements-dev.txt`

**Interfaces:**
- Produces: git 仓库、可运行的 pytest 环境（后续所有任务依赖）

- [ ] **Step 1: 确认 git 可用并初始化仓库**

```bash
git --version
cd /d/gpu-workbench && git init
```

Expected: git 版本号 ≥ 2.30；`Initialized empty Git repository`

- [ ] **Step 2: 写 .gitignore**

```gitignore
.venv/
.venv-win/
__pycache__/
*.pyc
logs/
papers_pdf/
artifacts/
wb/
local-launcher/
node_modules/
workbench.db
workbench.db-shm
workbench.db-wal
workbench.db.bak-v31
sessions/
pi-sessions/
*.zip
config.json
ssh-config.laptop.example
```

（注意：`config.json` 含密钥不得入库）

- [ ] **Step 3: 写 requirements-dev.txt**

```
pytest
pytest-asyncio
```

- [ ] **Step 4: 补全运行依赖并安装测试依赖（此前 pip install 被中断过）**

```bash
.venv-win/Scripts/python -m pip install -q -r requirements.txt -r requirements-dev.txt
.venv-win/Scripts/python -c "import fastapi, uvicorn, httpx; print('deps ok')"
```

Expected: `deps ok`

- [ ] **Step 5: 写 pytest.ini（asyncio 模式）**

```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

- [ ] **Step 6: 首次提交**

```bash
git add -A
git commit -m "chore: init repo with gitignore and dev requirements"
```

---

### Task 1: 安装 Pi 并配置 GLM provider

**Files:**
- Create: `C:\Users\ZhouYan\.pi\agent\models.json`（用户级配置，不入库）

**Interfaces:**
- Produces: `pi` 命令可用；provider `zhipu` / model `glm-4.7` 可被 `--provider zhipu --model glm-4.7` 选中

- [ ] **Step 1: 确认 Node ≥ 20**

```bash
node --version
```

Expected: v20 或更高。若未装：去 https://nodejs.org 装 LTS 后重试。

- [ ] **Step 2: 全局安装 Pi**

```bash
npm install -g --ignore-scripts @earendil-works/pi-coding-agent
pi --version
```

Expected: 版本号（0.99.x）。记下实际版本号到提交信息。

- [ ] **Step 3: 写 models.json**

写入 `C:\Users\ZhouYan\.pi\agent\models.json`（目录不存在则先创建；若文件已存在，把 `providers.zhipu` 合并进现有 JSON，不动其它 provider）：

```json
{
  "providers": {
    "zhipu": {
      "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
      "api": "openai-completions",
      "apiKey": "${ZHIPU_API_KEY}",
      "compat": { "thinkingFormat": "zai" },
      "models": [
        { "id": "glm-4.7", "contextWindow": 200000, "maxTokens": 16384 }
      ]
    }
  }
}
```

- [ ] **Step 4: 冒烟测试（print 模式，验证密钥与连通）**

```bash
export ZHIPU_API_KEY=$(python -c "import json;print(json.load(open('config.json'))['api_key'])")
pi -p "只回复两个字：正常" --provider zhipu --model glm-4.7
```

Expected: 输出含「正常」。若报模型不可用，检查 config.json 的 api_key 非空、base_url 与 models.json 一致。

- [ ] **Step 5: 提交（本任务无仓库内文件变更，提交一个记录性的空提交）**

```bash
git commit --allow-empty -m "chore: install pi <实际版本> and configure zhipu glm provider"
```

---

### Task 2: JSONL 行解析（TDD）

**Files:**
- Create: `bridge/__init__.py`（空文件）
- Create: `bridge/pi_process.py`
- Test: `tests/test_parse_line.py`

**Interfaces:**
- Produces: `bridge/pi_process.parse_line(line: str) -> dict | None`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_parse_line.py
import json

from bridge.pi_process import parse_line


def test_basic_line():
    assert parse_line('{"type": "response"}\n') == {"type": "response"}


def test_crlf_stripped():
    assert parse_line('{"a": 1}\r\n') == {"a": 1}


def test_blank_and_whitespace_are_none():
    assert parse_line("") is None
    assert parse_line("   \n") is None


def test_garbage_is_none_not_raise():
    assert parse_line("not json at all") is None


def test_unicode_sep_inside_string_is_kept():
    # U+2028/U+2029 是合法 JSON 字符串内容，绝不能当行分隔符
    payload = json.dumps({"x": "ab"})
    assert parse_line(payload) == {"x": "ab"}
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv-win/Scripts/python -m pytest tests/test_parse_line.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'bridge'`

- [ ] **Step 3: 最小实现**

```python
# bridge/__init__.py
```

```python
# bridge/pi_process.py
"""Pi RPC 子进程管理。"""
import json
import logging
import shutil

log = logging.getLogger(__name__)

# Windows 上 npm 全局命令是 pi.cmd，CreateProcess 需要 .cmd 后缀的完整路径
PI_BIN = shutil.which("pi") or "pi"


def parse_line(line: str) -> dict | None:
    """解析一行 JSONL。空行/非 JSON 返回 None（记警告），绝不抛异常。"""
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        log.warning("pi rpc: 丢弃非 JSON 行: %.200s", line)
        return None
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv-win/Scripts/python -m pytest tests/test_parse_line.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add bridge/ tests/test_parse_line.py
git commit -m "feat(bridge): jsonl line parser for pi rpc"
```

---

### Task 3: PiProcess 子进程封装

**Files:**
- Modify: `bridge/pi_process.py`
- Test: `tests/test_pi_process.py`

**Interfaces:**
- Produces:
  - `PiProcess(args: list[str], cwd: str, env: dict | None)`，方法 `start() / send(obj) / stop()`，属性 `events: asyncio.Queue[dict]`、`alive: bool`
  - 进程退出后向 `events` 放入 `{"type": "_process_exit", "code": <int>}` 哨兵

- [ ] **Step 1: 写失败测试（用 FakeProc 驱动 pump，不真 spawn）**

```python
# tests/test_pi_process.py
import asyncio

from bridge.pi_process import PiProcess, parse_line  # parse_line 已由 Task 2 覆盖


def test_alive_false_before_start():
    p = PiProcess(["--mode", "rpc"], cwd=".")
    assert p.alive is False


def test_parse_line_reexported():
    assert parse_line('{"ok": 1}') == {"ok": 1}
```

（真正的进程行为在 Task 9 E2E 验证；本任务单测只锁 `alive` 语义与导入面）

- [ ] **Step 2: 运行确认失败**

Run: `.venv-win/Scripts/python -m pytest tests/test_pi_process.py -v`
Expected: FAIL，`ImportError: cannot import name 'PiProcess'`

- [ ] **Step 3: 实现 PiProcess**

在 `bridge/pi_process.py` 追加：

```python
import asyncio
import os


class PiProcess:
    """`pi --mode rpc` 子进程：stdin 写 JSON 命令，stdout 读 JSONL 事件。

    事件放入 self.events（dict）；进程退出时放入哨兵
    {"type": "_process_exit", "code": returncode}。
    """

    def __init__(self, args: list[str], cwd: str, env: dict | None = None):
        self.args = args
        self.cwd = cwd
        self.env = env
        self.proc: asyncio.subprocess.Process | None = None
        self.events: asyncio.Queue[dict] = asyncio.Queue()

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self) -> None:
        env = {**os.environ, **(self.env or {})}
        self.proc = await asyncio.create_subprocess_exec(
            PI_BIN, *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd, env=env,
        )
        asyncio.create_task(self._pump_stdout())
        asyncio.create_task(self._drain_stderr())

    async def _pump_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            raw = await self.proc.stdout.readline()
            if not raw:
                break
            obj = parse_line(raw.decode("utf-8", "replace"))
            if obj is not None:
                await self.events.put(obj)
        await self.events.put(
            {"type": "_process_exit", "code": self.proc.returncode})

    async def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            raw = await self.proc.stderr.readline()
            if not raw:
                break
            log.warning("pi stderr: %s", raw.decode("utf-8", "replace").rstrip())

    async def send(self, obj: dict) -> None:
        if not self.alive:
            raise RuntimeError("pi process is not running")
        assert self.proc and self.proc.stdin
        self.proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def stop(self) -> None:
        if self.alive:
            assert self.proc
            self.proc.terminate()
            await self.proc.wait()
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv-win/Scripts/python -m pytest tests/ -v`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add bridge/pi_process.py tests/test_pi_process.py
git commit -m "feat(bridge): pi rpc subprocess wrapper"
```

---

### Task 4: PiClient RPC 命令封装（TDD）

**Files:**
- Create: `bridge/pi_client.py`
- Test: `tests/test_pi_client.py`

**Interfaces:**
- Consumes: `PiProcess`（Task 3：`events` / `send` / `alive` / `stop`）
- Produces: `PiClient(process)`：
  - `stream_events: asyncio.Queue[dict]`（非 response 事件）
  - `async command(payload: dict, timeout: float = 120) -> dict`（返回完整 response 对象）
  - `async prompt(message, streaming_behavior=None) -> dict`（返回 `data`）
  - `async get_state() -> dict`、`async abort() -> dict`、`async switch_session(path) -> dict`
  - `async ui_response(resp: dict) -> None`（原样发 stdin，不加 id）
  - `async close() -> None`

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv-win/Scripts/python -m pytest tests/test_pi_client.py -v`
Expected: FAIL，`ModuleNotFoundError`（No module named 'bridge.pi_client'）

- [ ] **Step 3: 实现**

```python
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
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv-win/Scripts/python -m pytest tests/test_pi_client.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add bridge/pi_client.py tests/test_pi_client.py
git commit -m "feat(bridge): rpc command client with id correlation"
```

---

### Task 5: 事件翻译（TDD）

**Files:**
- Create: `bridge/events.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Produces: `translate_event(ev: dict) -> list[dict]`，输出即 Global Constraints 定义的桥→网页消息（后续 Task 7/8 依赖字段名，不得改动）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_events.py
from bridge.events import translate_event


def test_text_delta_to_token():
    ev = {"type": "message_update",
          "assistantMessageEvent": {"type": "text_delta", "delta": "你好"}}
    assert translate_event(ev) == [{"type": "token", "delta": "你好"}]


def test_thinking_delta():
    ev = {"type": "message_update",
          "assistantMessageEvent": {"type": "thinking_delta", "delta": "想想"}}
    assert translate_event(ev) == [{"type": "thinking", "delta": "想想"}]


def test_other_message_update_ignored():
    ev = {"type": "message_update",
          "assistantMessageEvent": {"type": "text_start", "contentIndex": 0}}
    assert translate_event(ev) == []


def test_lifecycle():
    assert translate_event({"type": "agent_start"}) == [{"type": "turn_start"}]
    assert translate_event({"type": "agent_settled"}) == [{"type": "turn_done"}]


def test_tool_events():
    assert translate_event({"type": "tool_execution_start", "toolCallId": "t1",
                            "toolName": "read"}) == [
        {"type": "tool_start", "toolCallId": "t1", "tool": "read"}]
    assert translate_event({"type": "tool_execution_end", "toolCallId": "t1"}) == [
        {"type": "tool_end", "toolCallId": "t1"}]


def test_confirm_request_passthrough_with_fallback_fields():
    ev = {"type": "extension_ui_request", "id": "d9", "title": "确认",
          "message": "提交训练？"}
    out = translate_event(ev)
    assert out[0]["type"] == "confirm_request"
    assert out[0]["id"] == "d9"
    assert out[0]["request"]["title"] == "确认"
    assert out[0]["request"]["message"] == "提交训练？"


def test_confirm_request_missing_fields_get_defaults():
    ev = {"type": "extension_ui_request", "id": "d2"}
    out = translate_event(ev)[0]
    assert out["request"]["title"] == "确认操作"
    assert out["request"]["message"] == ""


def test_process_exit_to_error():
    out = translate_event({"type": "_process_exit", "code": 1})
    assert out == [{"type": "error", "message": "Pi 引擎进程退出"}]


def test_unknown_event_empty():
    assert translate_event({"type": "queue_update"}) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv-win/Scripts/python -m pytest tests/test_events.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
# bridge/events.py
"""Pi RPC 事件 → 网页 WebSocket 消息。"""


def _dialog(ev: dict) -> dict:
    req = ev.get("request") if isinstance(ev.get("request"), dict) else ev
    return {
        "title": req.get("title") or "确认操作",
        "message": req.get("message") or req.get("prompt") or "",
    }


def translate_event(ev: dict) -> list[dict]:
    t = ev.get("type")
    if t == "message_update":
        ame = ev.get("assistantMessageEvent") or {}
        k = ame.get("type")
        if k == "text_delta":
            return [{"type": "token", "delta": ame.get("delta") or ""}]
        if k == "thinking_delta":
            return [{"type": "thinking", "delta": ame.get("delta") or ""}]
        return []
    if t == "agent_start":
        return [{"type": "turn_start"}]
    if t == "agent_settled":
        return [{"type": "turn_done"}]
    if t == "tool_execution_start":
        return [{"type": "tool_start", "toolCallId": ev.get("toolCallId"),
                 "tool": ev.get("toolName")}]
    if t == "tool_execution_end":
        return [{"type": "tool_end", "toolCallId": ev.get("toolCallId")}]
    if t == "extension_ui_request":
        return [{"type": "confirm_request", "id": ev.get("id"),
                 "request": _dialog(ev)}]
    if t == "_process_exit":
        return [{"type": "error", "message": "Pi 引擎进程退出"}]
    return []
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv-win/Scripts/python -m pytest tests/test_events.py -v`
Expected: 10 passed

- [ ] **Step 5: 提交**

```bash
git add bridge/events.py tests/test_events.py
git commit -m "feat(bridge): translate pi events to web ws messages"
```

---

### Task 6: 会话管理器（TDD）

**Files:**
- Create: `bridge/sessions.py`
- Test: `tests/test_sessions.py`

**Interfaces:**
- Consumes: `PiProcess`（Task 3）、`PiClient`（Task 4）
- Produces: `PiSessionManager(root: str)`：
  - `find_session_file(sessions_dir: str, conv_id: str) -> str | None`（staticmethod，按文件名含 conv_id 取 mtime 最新）
  - `async get(conv_id: str) -> PiClient`（存活复用；否则 spawn `--mode rpc --session-dir <root>/pi-sessions -n <conv_id>`，env 注入 `ZHIPU_API_KEY`（读 root/config.json 的 api_key），有历史文件则 `switch_session` 恢复，恢复失败降级为新会话）
  - `async shutdown() -> None`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_sessions.py
import json
import os
import time

from bridge.sessions import PiSessionManager


def test_find_session_file_picks_newest_match(tmp_path):
    old = tmp_path / "convA-2026-10-01.jsonl"
    new = tmp_path / "convA-2026-10-16.jsonl"
    other = tmp_path / "convB-2026-10-16.jsonl"
    for f in (old, new, other):
        f.write_text("{}")
    back = time.time() - 10000
    os.utime(old, (back, back))
    assert PiSessionManager.find_session_file(str(tmp_path), "convA") == str(new)


def test_find_session_file_none(tmp_path):
    assert PiSessionManager.find_session_file(str(tmp_path), "nope") is None
    assert PiSessionManager.find_session_file(str(tmp_path / "missing"), "x") is None


def test_zhipu_env_reads_config(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"api_key": "sk-test", "model": "glm-4.7"}), encoding="utf-8")
    m = PiSessionManager(str(tmp_path))
    assert m._zhipu_env() == {"ZHIPU_API_KEY": "sk-test"}


def test_zhipu_env_empty_without_config(tmp_path):
    m = PiSessionManager(str(tmp_path))
    assert m._zhipu_env() == {}


def test_spawn_args():
    m = PiSessionManager(".")
    args = m._spawn_args("conv1")
    assert args[:2] == ["--mode", "rpc"]
    assert "--session-dir" in args
    i = args.index("-n")
    assert args[i + 1] == "conv1"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv-win/Scripts/python -m pytest tests/test_sessions.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现**

```python
# bridge/sessions.py
"""网页会话 ↔ Pi 子进程的管理。"""
import json
import logging
import os

from .pi_client import PiClient
from .pi_process import PiProcess

log = logging.getLogger(__name__)


class PiSessionManager:
    def __init__(self, root: str, sessions_subdir: str = "pi-sessions"):
        self.root = root
        self.sessions_dir = os.path.join(root, sessions_subdir)
        os.makedirs(self.sessions_dir, exist_ok=True)
        self._clients: dict[str, PiClient] = {}

    def _zhipu_env(self) -> dict:
        cfg_path = os.path.join(self.root, "config.json")
        try:
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            return {}
        key = cfg.get("api_key") or ""
        return {"ZHIPU_API_KEY": key} if key else {}

    def _spawn_args(self, conv_id: str) -> list[str]:
        return ["--mode", "rpc", "--session-dir", self.sessions_dir, "-n", conv_id]

    @staticmethod
    def find_session_file(sessions_dir: str, conv_id: str) -> str | None:
        if not os.path.isdir(sessions_dir):
            return None
        best, best_mt = None, -1.0
        for name in os.listdir(sessions_dir):
            path = os.path.join(sessions_dir, name)
            if conv_id in name and os.path.isfile(path):
                mt = os.path.getmtime(path)
                if mt > best_mt:
                    best, best_mt = path, mt
        return best

    async def get(self, conv_id: str) -> PiClient:
        client = self._clients.get(conv_id)
        if client and client.process.alive:
            return client
        proc = PiProcess(self._spawn_args(conv_id), cwd=self.root,
                         env=self._zhipu_env())
        await proc.start()
        client = PiClient(proc)
        self._clients[conv_id] = client
        session_file = self.find_session_file(self.sessions_dir, conv_id)
        if session_file:
            try:
                await client.switch_session(session_file)
                log.info("resumed pi session %s", session_file)
            except Exception:
                log.warning("switch_session failed, continuing fresh",
                            exc_info=True)
        return client

    async def shutdown(self) -> None:
        for client in self._clients.values():
            try:
                await client.close()
            except Exception:
                log.warning("client close failed", exc_info=True)
        self._clients.clear()
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv-win/Scripts/python -m pytest tests/test_sessions.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add bridge/sessions.py tests/test_sessions.py
git commit -m "feat(bridge): pi session manager with resume"
```

---

### Task 7: WebSocket 端点并挂进 server.py（TDD）

**Files:**
- Create: `bridge/routes.py`
- Modify: `server.py`（两处：import 区加一行；`mount_agent(...)` 调用块之后挂载）
- Test: `tests/test_routes.py`

**Interfaces:**
- Consumes: `PiSessionManager`（Task 6）、`translate_event`（Task 5）、`resolve_token`（server.py 已有，签名 `resolve_token(token: str) -> dict | None`，返回 `{"username", "role"}`）
- Produces: `mount_pi(app, root, resolve_token_fn)`；WS 端点 `/api/pi/ws?conv=<id>`（协议见 Global Constraints）

- [ ] **Step 1: 写失败测试**

```python
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
```

（TestClient 对 close code 的断言不可靠，用约定：未登录时先发一条 error 消息再由服务端 close）

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv-win/Scripts/python -m pytest tests/test_routes.py -v`
Expected: FAIL，`ModuleNotFoundError`（No module named 'bridge.routes'）

- [ ] **Step 3: 实现 routes.py**

```python
# bridge/routes.py
"""Pi 桥的 WebSocket 端点。"""
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import events as ev

router = APIRouter()
manager = None          # 由 mount_pi 注入（PiSessionManager）
_resolve_token = None   # 由 mount_pi 注入（server.resolve_token）


def mount_pi(app, root, resolve_token_fn) -> None:
    """挂载 /api/pi/ws。延迟 import 避免与 server.py 循环依赖。"""
    global manager, _resolve_token
    from .sessions import PiSessionManager
    manager = PiSessionManager(root)
    _resolve_token = resolve_token_fn
    app.include_router(router)


async def _pump(client, ws) -> None:
    while True:
        obj = await client.stream_events.get()
        for msg in ev.translate_event(obj):
            await ws.send_text(json.dumps(msg, ensure_ascii=False))


@router.websocket("/api/pi/ws")
async def pi_ws(ws: WebSocket):
    await ws.accept()
    token = ws.cookies.get("wb_session", "")
    user = _resolve_token(token) if _resolve_token else None
    if not user:
        await ws.send_text(json.dumps(
            {"type": "error", "message": "未登录"}, ensure_ascii=False))
        await ws.close(code=4401)
        return
    conv = ws.query_params.get("conv") or "default"
    client = await manager.get(conv)
    pump = asyncio.create_task(_pump(client, ws))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            mtype = msg.get("type")
            if mtype == "message":
                state = await client.get_state()
                sb = "followUp" if state.get("isStreaming") else None
                await client.prompt(msg.get("text", ""), sb)
            elif mtype == "abort":
                await client.abort()
            elif mtype == "confirm":
                await client.ui_response({
                    "type": "extension_ui_response",
                    "id": msg.get("id"),
                    "confirmed": bool(msg.get("confirmed")),
                })
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv-win/Scripts/python -m pytest tests/test_routes.py -v`
Expected: 2 passed

- [ ] **Step 5: 挂进 server.py**

在 `server.py` import 区（`from agent.routes import mount_agent` 附近，约第 25 行）加：

```python
from bridge.routes import mount_pi
```

在 `mount_agent(...)` 调用块结束后（约第 1997 行，`app.mount("/static", ...)` 之前）加：

```python
mount_pi(app, ROOT, resolve_token)
```

- [ ] **Step 6: 全量测试 + 启动冒烟**

```bash
.venv-win/Scripts/python -m pytest tests/ -v
WORKBENCH_MOCK=1 .venv-win/Scripts/python -c "import server; print('server imports ok')"
```

Expected: 全部 passed；`server imports ok`（若 import 失败查循环依赖——`bridge` 包内不得 import `server`）

- [ ] **Step 7: 提交**

```bash
git add bridge/routes.py server.py tests/test_routes.py
git commit -m "feat(bridge): websocket endpoint mounted into server"
```

---

### Task 8: 前端最小对话页

**Files:**
- Create: `static/pi-chat.html`（自包含，无构建、无依赖）

**Interfaces:**
- Consumes: `/api/pi/ws?conv=<id>` 协议（Global Constraints）
- Produces: 浏览器可用页面 `/static/pi-chat.html`

- [ ] **Step 1: 写页面**

```html
<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Pi 智能体（第 1 期骨架）</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; display: flex;
         flex-direction: column; height: 100vh; background: #fafafa; }
  header { padding: 10px 16px; background: #1a1a2e; color: #fff;
           display: flex; gap: 12px; align-items: center; }
  header input { flex: 0 0 220px; }
  #log { flex: 1; overflow-y: auto; padding: 16px; }
  .msg { margin: 8px 0; padding: 8px 12px; border-radius: 8px; max-width: 80%;
         white-space: pre-wrap; word-break: break-word; }
  .user { background: #dcedc8; margin-left: auto; }
  .bot  { background: #fff; border: 1px solid #e0e0e0; }
  .think { color: #888; font-size: 12px; border-left: 3px solid #ccc;
           padding-left: 8px; margin: 4px 0; white-space: pre-wrap; }
  .tool { color: #1565c0; font-size: 12px; }
  .err { color: #c62828; }
  footer { display: flex; gap: 8px; padding: 12px; background: #fff;
           border-top: 1px solid #ddd; }
  footer textarea { flex: 1; height: 44px; resize: none; }
  button { padding: 8px 16px; cursor: pointer; }
  #confirm { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.4); }
  #confirm .box { background: #fff; margin: 20% auto; padding: 20px;
                  width: 320px; border-radius: 8px; text-align: center; }
</style>
</head>
<body>
<header>
  <b>Pi 智能体</b>
  <span id="status" style="color:#aaa">未连接</span>
  <input id="conv" placeholder="会话名" value="default">
  <button onclick="connect()">连接</button>
  <button onclick="abortTurn()">中断</button>
</header>
<div id="log"></div>
<footer>
  <textarea id="input" placeholder="输入消息，Ctrl+Enter 发送"></textarea>
  <button onclick="send()">发送</button>
</footer>
<div id="confirm"><div class="box">
  <h3 id="c-title"></h3><p id="c-msg"></p>
  <button onclick="answerConfirm(true)">确认</button>
  <button onclick="answerConfirm(false)">拒绝</button>
</div></div>
<script>
let ws = null, pendingConfirmId = null, botEl = null, thinkEl = null;

const $ = id => document.getElementById(id);

function addDiv(cls, text) {
  const d = document.createElement("div");
  d.className = "msg " + cls;
  d.textContent = text;
  $("log").appendChild(d);
  $("log").scrollTop = 1e9;
  return d;
}

function connect() {
  if (ws) { try { ws.close(); } catch (e) {} }
  $("status").textContent = "连接中…";
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/api/pi/ws?conv=${encodeURIComponent($("conv").value.trim() || "default")}`);
  ws.onopen = () => $("status").textContent = "已连接";
  ws.onclose = () => $("status").textContent = "已断开";
  ws.onerror = () => $("status").textContent = "连接失败（先到主站登录）";
  ws.onmessage = e => handle(JSON.parse(e.data));
}

function handle(m) {
  if (m.type === "token") {
    if (!botEl) botEl = addDiv("bot", "");
    botEl.textContent += m.delta;
  } else if (m.type === "thinking") {
    if (!thinkEl) thinkEl = addDiv("think", "");
    thinkEl.textContent += m.delta;
  } else if (m.type === "turn_start") {
    botEl = null; thinkEl = null;
  } else if (m.type === "turn_done") {
    botEl = null; thinkEl = null;
  } else if (m.type === "tool_start") {
    addDiv("tool", `⚙ 调用工具 ${m.tool} …`);
  } else if (m.type === "tool_end") {
    addDiv("tool", `⚙ 工具完成`);
  } else if (m.type === "confirm_request") {
    pendingConfirmId = m.id;
    $("c-title").textContent = m.request.title;
    $("c-msg").textContent = m.request.message;
    $("confirm").style.display = "block";
  } else if (m.type === "error") {
    addDiv("err", m.message);
  }
}

function send() {
  const text = $("input").value.trim();
  if (!text || !ws || ws.readyState !== 1) return;
  addDiv("user", text);
  ws.send(JSON.stringify({ type: "message", text }));
  $("input").value = "";
}

function abortTurn() {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "abort" }));
}

function answerConfirm(ok) {
  $("confirm").style.display = "none";
  if (ws && pendingConfirmId)
    ws.send(JSON.stringify({ type: "confirm", id: pendingConfirmId, confirmed: ok }));
  pendingConfirmId = null;
}

$("input").addEventListener("keydown", e => {
  if (e.key === "Enter" && e.ctrlKey) { e.preventDefault(); send(); }
});
</script>
</body>
</html>
```

- [ ] **Step 2: 语法自检 + 提交**

浏览器打开前先确认静态目录被挂载（server.py 已有 `/static`）。人工打开页面留到 Task 9。

```bash
git add static/pi-chat.html
git commit -m "feat(web): minimal pi chat page"
```

---

### Task 9: 端到端验收

**Files:**
- Modify: `README.md`（若无则创建，追加「本地开发」一节）

**Interfaces:**
- Consumes: 前面全部任务

- [ ] **Step 1: 启动服务**

```bash
WORKBENCH_MOCK=1 .venv-win/Scripts/python server.py
```

Expected: uvicorn 监听 0.0.0.0:8787，无 traceback。

- [ ] **Step 2: 登录取 cookie**

```bash
curl -s -c /tmp/wb.cookies -X POST http://127.0.0.1:8787/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"niexin","password":"<初始密码=用户名>"}'
```

Expected: 返回用户信息。若密码错误：本地这份 workbench.db 是实验室拷贝，用你知道的账号密码。

- [ ] **Step 3: 浏览器验收**

浏览器开 `http://127.0.0.1:8787/` 登录后，新标签页开 `http://127.0.0.1:8787/static/pi-chat.html`，依次验证：

1. 点「连接」→ 状态变「已连接」，后台 spawn 了 pi 子进程（任务管理器可见 `node ... pi`）
2. 发「你好，用一句话介绍你自己」→ 收到 GLM 流式回复（token 逐字出现）
3. 等 `turn_done` 后再发第二条 → 上下文延续（它记得第一条）
4. 改会话名为 `test2` 重连 → 新会话；改回 `default` 重连 → 恢复历史对话
5. 点「中断」→ 当前回合停止
6. 重启 server.py → 重连 `default` → 对话历史仍在（pi-sessions/ 有文件）

- [ ] **Step 4: 记录并提交**

在 README.md 追加：

```markdown
## 本地开发（第 1 期：Pi 骨架）

- 依赖：`.venv-win`（Python 3.13）+ Node ≥ 20 + 全局 `@earendil-works/pi-coding-agent`
- 启动：`WORKBENCH_MOCK=1 .venv-win/Scripts/python server.py`
- 页面：登录主站后开 `/static/pi-chat.html`
- 测试：`.venv-win/Scripts/python -m pytest tests/ -v`
- LLM 密钥：读 `config.json` 的 `api_key`，经 `ZHIPU_API_KEY` 环境变量注入 Pi
```

```bash
git add README.md
git commit -m "docs: local dev setup for phase 1 pi skeleton"
```

---

## Self-Review 记录

- **Spec 覆盖**：本计划只覆盖 spec §7 第 1 期（骨架）；工具/门禁（第 2 期）、执行器（第 3 期）、记忆/计划卡（第 4 期）、旧路由清理与前端整合（第 5 期）留待后续计划——spec §3.3 确认门禁的**桥侧通路**（confirm 消息往返）已在本期 Task 5/7/8 打通，扩展侧钩子在第 2 期。
- **占位符**：Task 9 Step 2 的密码是有意留白（用户私有凭据），非实现细节缺失。
- **类型一致性**：`translate_event` 输出字段名与 Task 8 `handle()` 消费的字段名逐一核对一致；`PiClient` 方法名在 Task 4/6/7 间一致。
