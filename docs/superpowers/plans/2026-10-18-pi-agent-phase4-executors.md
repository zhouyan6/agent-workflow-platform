# 科研执行智能体 · 第 4 期（多服务器执行器 + 飞书）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 智能体可查询/使用多台训练服务器（servers.json 注册，SSH 裸机为主力、workbench REST 备用），提交训练自动远端 git pull，远端任务结束自动注入分析 + 飞书推送，UI 机器面板显示真实列表。

**Architecture:** 新增 `bridge/executors.py`（SSHRunner 命令抽象 + SSHExecutor/WorkbenchExecutor + 注册表），tool_backend 追加 5 个桥生工具（list_servers/gpu_status_remote/submit_train/log_tail/stop_train，后两个进确认门禁），watcher 扩展远端 pid 轮询，mount_pi 增加飞书 notify 注入，前端机器面板接 `GET /api/pi/servers`。

**Tech Stack:** 既有栈；SSH 用系统 `ssh`（BatchMode），HTTP 用 httpx。

**Spec:** `docs/superpowers/specs/2026-10-17-pi-research-agent-design.md` §3.5（2026-10-18 改写版）、§3.5b

## Global Constraints

- 工作目录 `D:\gpu-workbench`；Python `.venv-win/Scripts/python`；测试 `.venv-win/Scripts/python -m pytest tests/ -q`（当前基线 49 passed）
- SSH 一律 `BatchMode=yes + ConnectTimeout=8`（绝不挂起等密码）；所有远端命令经可注入的 `runner.run(cmd, timeout) -> (code, output)`，单测用 FakeRunner，不碰真网络
- servers.json 不存在时回退 `[{"name":"local","type":"local"}]`；文件本身 gitignore（含内网信息）
- 新工具 schema 进 `BRIDGE_TOOLS`（单一事实来源）；`submit_train`/`stop_train` 加入扩展 `CONFIRM_TOOLS`（强制确认）
- 既有本地 11 工具与消息类型不得改动；新 REST `GET /api/pi/servers` 用 cookie/query `wb_session` 鉴权（同 convs 路由）
- 飞书只推任务结果（结束/失败），确认待办不推；经 `mount_pi(..., notify=...)` 注入的异步函数调用
- 每任务 git commit

---

### Task 1: 执行器（executors.py + FakeRunner 单测）

**Files:**
- Create: `bridge/executors.py`
- Test: `tests/test_executors.py`
- Modify: `.gitignore`（加 `servers.json`）

**Interfaces:**
- Produces:
  - `SSHRunner(host, port, user)`：`async run(cmd, timeout=30) -> tuple[int, str]`（stdout+stderr 合并）
  - `SSHExecutor(cfg, runner=None)`：`gpu_status()/submit(command, gpu=None)/logs(ref, lines=80)/stop(pid)/alive(pid)`，全部返回 `{"ok": bool, ...}` dict
  - `WorkbenchExecutor(cfg, client=None)`：同上五方法（ref 为远端任务 id；login 用 POST /api/login 拿 token 后 Bearer 调用）
  - `load_servers(root) -> list[dict]`（读 ROOT/servers.json，缺省 local 一条）
  - `make_executor(cfg, runner=None)`：按 cfg["type"] 工厂（local 返回 None）

- [ ] **Step 1: .gitignore 追加一行 `servers.json`**

- [ ] **Step 2: 写失败测试 tests/test_executors.py**

```python
# tests/test_executors.py
import asyncio
import json

from bridge.executors import SSHExecutor, WorkbenchExecutor, load_servers, make_executor


class FakeRunner:
    def __init__(self, script=None):
        self.script = script or {}   # cmd 子串 -> (code, out)
        self.calls = []

    async def run(self, cmd, timeout=30):
        self.calls.append(cmd)
        for key, val in self.script.items():
            if key in cmd:
                return val
        return (0, "")

CFG = {"name": "inspur", "type": "ssh", "host": "127.0.0.1", "port": 15000,
       "user": "niexin", "repo_path": "~/exp", "log_dir": "logs/"}


def test_load_servers_default(tmp_path):
    assert load_servers(str(tmp_path)) == [{"name": "local", "type": "local"}]


def test_load_servers_file(tmp_path):
    (tmp_path / "servers.json").write_text(
        json.dumps([{"name": "s1", "type": "ssh", "host": "h", "port": 22,
                     "user": "u", "repo_path": "~/r", "log_dir": "logs/"}]),
        encoding="utf-8")
    assert load_servers(str(tmp_path))[0]["name"] == "s1"


def test_make_executor_factory():
    assert make_executor({"name": "local", "type": "local"}) is None
    e = make_executor(CFG, runner=FakeRunner())
    assert isinstance(e, SSHExecutor)


async def test_ssh_gpu_status_parses_csv():
    r = FakeRunner({"nvidia-smi": (0, "0, RTX 4090, 5, 100, 24576\n1, A100, 90, 70000, 81920")})
    e = SSHExecutor(CFG, runner=r)
    out = await e.gpu_status()
    assert out["ok"] is True
    assert out["gpus"][0] == {"index": "0", "name": "RTX 4090",
                              "util": "5%", "mem": "100/24576MiB"}
    assert out["gpus"][1]["util"] == "90%"


async def test_ssh_gpu_status_error():
    r = FakeRunner({"nvidia-smi": (1, "ssh: connect refused")})
    out = await SSHExecutor(CFG, runner=r).gpu_status()
    assert out["ok"] is False and "refused" in out["error"]


async def test_ssh_submit_parses_pid_and_pulls():
    r = FakeRunner({"echo PID=$!": (0, "PID=12345")})
    e = SSHExecutor(CFG, runner=r)
    out = await e.submit("python train.py", gpu="3")
    assert out["ok"] is True and out["pid"] == 12345
    assert "agent-" in out["log"] and out["log"].startswith("logs/")
    cmd = r.calls[0]
    assert "git pull" in cmd and "CUDA_VISIBLE_DEVICES=3" in cmd and "nohup" in cmd


async def test_ssh_submit_git_pull_failure():
    r = FakeRunner({"git pull": (1, "CONFLICT (content): Merge conflict")})
    out = await SSHExecutor(CFG, runner=r).submit("python train.py")
    assert out["ok"] is False and "CONFLICT" in out["error"]


async def test_ssh_logs_and_stop_and_alive():
    r = FakeRunner({"tail": (0, "epoch 1 loss 1.5\nNaN appeared"),
                    "kill": (0, ""), "ps -p": (0, "")})
    e = SSHExecutor(CFG, runner=r)
    logs = await e.logs("logs/agent-x.log", lines=2)
    assert logs["ok"] and "NaN appeared" in logs["text"]
    assert (await e.stop(999))["ok"] is True
    assert (await e.alive(999))["ok"] is True
    r2 = FakeRunner({"ps -p": (1, "")})
    assert (await SSHExecutor(CFG, runner=r2).alive(999))["ok"] is False


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def post(self, url, **kw):
        self.calls.append(("POST", url))
        if url.endswith("/api/login"):
            return _Resp(200, {"token": "tk", "username": "u"})
        return _Resp(200, {"id": 7})

    async def get(self, url, **kw):
        self.calls.append(("GET", url))
        return _Resp(200, {"gpus": [{"index": 0}]})

    async def aclose(self):
        pass


class _Resp:
    def __init__(self, code, data):
        self.status_code = code
        self._data = data

    def json(self):
        return self._data


async def test_workbench_executor(monkeypatch):
    cfg = {"name": "gpu2", "type": "workbench", "base_url": "http://127.0.0.1:8788",
           "username": "niexin", "password_env": "WB_TEST_PW"}
    monkeypatch.setenv("WB_TEST_PW", "pw")
    http = FakeHttp()
    e = WorkbenchExecutor(cfg, client=http)
    gpu = await e.gpu_status()
    assert gpu["ok"] and "gpus" in gpu
    sub = await e.submit("python train.py", gpu=None, name="t")
    assert sub["ok"] and sub["task_id"] == 7
```

（WorkbenchExecutor.login 惰性：首次调用时 POST 登录并存 `self._token`，后续请求带 `Authorization: Bearer`）

- [ ] **Step 3: 运行确认失败**

Run: `.venv-win/Scripts/python -m pytest tests/test_executors.py -q`
Expected: FAIL，`No module named 'bridge.executors'`

- [ ] **Step 4: 实现 bridge/executors.py**

```python
# bridge/executors.py
"""多服务器执行器：SSH 裸机（主力）与远端 workbench REST（备用）。"""
import asyncio
import json
import logging
import os
from datetime import datetime

import httpx

log = logging.getLogger(__name__)


def load_servers(root: str) -> list[dict]:
    path = os.path.join(root, "servers.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            return data
    except (OSError, ValueError):
        pass
    return [{"name": "local", "type": "local"}]


def make_executor(cfg: dict, runner=None):
    if cfg.get("type") == "ssh":
        return SSHExecutor(cfg, runner=runner)
    if cfg.get("type") == "workbench":
        return WorkbenchExecutor(cfg)
    return None  # local：走 agent.tools 本地工具


class SSHRunner:
    """所有远端命令经此执行（单测注入 FakeRunner）。BatchMode 绝不挂起等密码。"""

    def __init__(self, host: str, port: int, user: str):
        self.host, self.port, self.user = host, port, user

    async def run(self, cmd: str, timeout: float = 30) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            "ssh", "-p", str(self.port),
            "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=accept-new",
            f"{self.user}@{self.host}", cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return 124, f"ssh 命令超时（{timeout}s）: {cmd[:80]}"
        return proc.returncode or 0, out.decode("utf-8", "replace")


class SSHExecutor:
    def __init__(self, cfg: dict, runner=None):
        self.cfg = cfg
        self.runner = runner or SSHRunner(
            cfg["host"], cfg.get("port", 22), cfg["user"])

    async def gpu_status(self) -> dict:
        code, out = await self.runner.run(
            "nvidia-smi --query-gpu=index,name,utilization.gpu,"
            "memory.used,memory.total --format=csv,noheader,nounits")
        if code:
            return {"ok": False, "error": out.strip() or f"exit {code}"}
        gpus = []
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5:
                gpus.append({"index": parts[0], "name": parts[1],
                             "util": parts[2] + "%",
                             "mem": f"{parts[3]}/{parts[4]}MiB"})
        return {"ok": True, "server": self.cfg["name"], "gpus": gpus}

    async def submit(self, command: str, gpu: str | None = None) -> dict:
        repo = self.cfg["repo_path"]
        log_dir = self.cfg.get("log_dir", "logs/")
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        rel_log = f"{log_dir}agent-{ts}.log"
        prefix = f"CUDA_VISIBLE_DEVICES={gpu} " if gpu else ""
        cmd = (f"cd {repo} && git pull --ff-only && mkdir -p {log_dir} && "
               f"{prefix}nohup {command} > {rel_log} 2>&1 & echo PID=$!")
        code, out = await self.runner.run(cmd, timeout=120)
        if code:
            return {"ok": False, "server": self.cfg["name"],
                    "error": f"提交失败（多为 git pull 冲突）:\n{out.strip()[:500]}"}
        pid = None
        for line in out.splitlines():
            if line.startswith("PID="):
                pid = int(line[4:].strip())
        if pid is None:
            return {"ok": False, "server": self.cfg["name"],
                    "error": f"未解析到 PID: {out.strip()[:200]}"}
        return {"ok": True, "server": self.cfg["name"], "pid": pid, "log": rel_log}

    async def logs(self, ref: str, lines: int = 80) -> dict:
        code, out = await self.runner.run(f"tail -n {int(lines)} {ref}")
        if code:
            return {"ok": False, "error": out.strip()[:300]}
        return {"ok": True, "server": self.cfg["name"], "text": out}

    async def stop(self, pid: int) -> dict:
        code, out = await self.runner.run(f"kill {int(pid)}")
        return {"ok": code == 0, "server": self.cfg["name"],
                "error": out.strip()[:200] if code else ""}

    async def alive(self, pid: int) -> dict:
        code, _ = await self.runner.run(f"ps -p {int(pid)}")
        return {"ok": code == 0}


class WorkbenchExecutor:
    def __init__(self, cfg: dict, client=None):
        self.cfg = cfg
        self._token: str | None = None
        self._client = client or httpx.AsyncClient(base_url=cfg["base_url"], timeout=15)

    async def _login(self) -> None:
        if self._token:
            return
        pw = os.environ.get(self.cfg.get("password_env", ""), "")
        r = await self._client.post("/api/login", json={
            "username": self.cfg.get("username", ""), "password": pw})
        if r.status_code != 200:
            raise RuntimeError(f"远端登录失败 {r.status_code}")
        self._token = r.json()["token"]

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    async def gpu_status(self) -> dict:
        try:
            await self._login()
            r = await self._client.get("/api/gpu", headers=self._h())
            data = r.json()
            return {"ok": True, "server": self.cfg["name"], "gpus": data.get("gpus", data)}
        except Exception as e:
            return {"ok": False, "error": str(e)[:300]}

    async def submit(self, command: str, gpu: str | None = None,
                     name: str = "agent-task") -> dict:
        try:
            await self._login()
            body = {"name": name, "command": command, "gpus_needed": 1}
            if gpu:
                body["gpus_pinned"] = gpu
            r = await self._client.post("/api/tasks", json=body, headers=self._h())
            if r.status_code != 200:
                return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
            return {"ok": True, "server": self.cfg["name"],
                    "task_id": r.json()["id"]}
        except Exception as e:
            return {"ok": False, "error": str(e)[:300]}

    async def logs(self, ref: str, lines: int = 80) -> dict:
        try:
            await self._login()
            r = await self._client.get(f"/api/tasks/{ref}/log",
                                       params={"lines": lines}, headers=self._h())
            if r.status_code != 200:
                return {"ok": False, "error": f"HTTP {r.status_code}"}
            return {"ok": True, "server": self.cfg["name"], "text": r.text}
        except Exception as e:
            return {"ok": False, "error": str(e)[:300]}

    async def stop(self, pid) -> dict:
        try:
            await self._login()
            r = await self._client.post(f"/api/tasks/{pid}/stop", headers=self._h())
            return {"ok": r.status_code == 200, "server": self.cfg["name"]}
        except Exception as e:
            return {"ok": False, "error": str(e)[:300]}

    async def alive(self, ref) -> dict:
        try:
            await self._login()
            r = await self._client.get("/api/tasks", headers=self._h())
            for t in r.json().get("tasks", r.json() if isinstance(r.json(), list) else []):
                if str(t.get("id")) == str(ref):
                    return {"ok": t.get("status") in ("pending", "running")}
            return {"ok": False}
        except Exception:
            return {"ok": False}
```

- [ ] **Step 5: 运行确认通过 + 提交**

Run: `.venv-win/Scripts/python -m pytest tests/ -q`
Expected: 全部 passed（49 + 新增）

```bash
git add bridge/executors.py tests/test_executors.py .gitignore
git commit -m "feat(bridge): ssh and workbench executors with injectable runner"
```

---

### Task 2: 服务器工具（5 个桥生工具 + /api/pi/servers + 确认门禁）

**Files:**
- Modify: `bridge/tool_backend.py`（BRIDGE_TOOLS 追加 + `_remote_tool`）
- Modify: `bridge/routes.py`（GET /api/pi/servers + mount 时加载 SERVERS）
- Modify: `pi-extension/research/index.ts`（CONFIRM_TOOLS 加 submit_train/stop_train）+ 重新拷贝到 `~\.pi\agent\extensions\research\`
- Test: `tests/test_remote_tools.py`

**Interfaces:**
- Consumes: Task 1 的 `load_servers/make_executor`
- Produces:
  - `tool_backend.SERVERS: list[dict]`（mount 时 `load_servers(ROOT)`）、`tool_backend._remote_tool(name, call) -> dict`
  - tool_backend `call_tool` 在 run_create 分支后追加：
    `if name in ("list_servers", "gpu_status_remote", "submit_train", "log_tail", "stop_train"): return await _remote_tool(name, call)`
  - `log_tail` 拉回日志后：写临时文件 → `experiments.parse_scores` + `pick_score_series` + `series_best` 摘要 + 正则 `r"(NaN|Inf[^a-z]|OOM|out of memory|Traceback|CUDA error)"`（re.I）命中行前缀 ⚠
  - `submit_train` 成功后 `await _manager.watch_remote(call.conv, cfg, out)`（out 含 pid/log；Task 3 定义）
  - REST：`GET /api/pi/servers?wb_session=` → `{"servers":[{"name","type","note","ok":bool|null}]}`（local 恒 ok=True；远端用 executor.alive 探测太重——健康检查统一跑一条 `SSHRunner.run("echo ok", timeout=10)`，workbench 跑 login+gpu）

- [ ] **Step 1: 写失败测试 tests/test_remote_tools.py**

```python
# tests/test_remote_tools.py
import json

import bridge.tool_backend as tb
from fastapi import FastAPI
from fastapi.testclient import TestClient

SERVERS = [
    {"name": "local", "type": "local"},
    {"name": "inspur", "type": "ssh", "host": "h", "port": 22, "user": "u",
     "repo_path": "~/r", "log_dir": "logs/"},
]


class FakeExecutor:
    def __init__(self, cfg):
        self.cfg = cfg

    async def gpu_status(self):
        return {"ok": True, "server": self.cfg["name"],
                "gpus": [{"index": "0", "name": "T", "util": "1%", "mem": "1/2MiB"}]}

    async def submit(self, command, gpu=None, name=None):
        return {"ok": True, "server": self.cfg["name"], "pid": 42,
                "log": "logs/agent-x.log"}

    async def logs(self, ref, lines=80):
        return {"ok": True, "text": "epoch 1 acc 0.5\nNaN appeared"}

    async def stop(self, pid):
        return {"ok": True, "server": self.cfg["name"]}


class FakeManager:
    bridge_token = "t"
    ctx_factory = None

    def user_for(self, c):
        return {"username": "u"}

    watched = []

    async def watch_remote(self, conv, cfg, out):
        self.watched.append((conv, cfg["name"], out["pid"]))


def _client(monkeypatch):
    app = FastAPI()
    tb._manager = FakeManager()
    tb.mount_tool_backend(app, tb._manager)
    monkeypatch.setattr(tb, "SERVERS", SERVERS)
    monkeypatch.setattr(tb, "make_executor", lambda cfg: FakeExecutor(cfg))
    return TestClient(app), tb._manager


def _call(c, name, args):
    return c.post(f"/api/pi/tools/{name}", headers={"x-bridge-token": "t"},
                  json={"args": args, "conv": "c1"}).json()["result"]


def test_list_servers(monkeypatch):
    c, _ = _client(monkeypatch)
    out = json.loads(_call(c, "list_servers", {}))
    assert out["ok"] and [s["name"] for s in out["servers"]] == ["local", "inspur"]


def test_gpu_status_remote_all_and_one(monkeypatch):
    c, _ = _client(monkeypatch)
    out = json.loads(_call(c, "gpu_status_remote", {}))
    assert out["ok"] and len(out["results"]) == 1   # 只有 ssh/workbench 参与
    out = json.loads(_call(c, "gpu_status_remote", {"server": "inspur"}))
    assert out["ok"] and out["results"][0]["gpus"][0]["util"] == "1%"
    out = json.loads(_call(c, "gpu_status_remote", {"server": "nope"}))
    assert out["ok"] is False


def test_submit_train_registers_watch(monkeypatch):
    c, m = _client(monkeypatch)
    out = json.loads(_call(c, "submit_train",
                           {"server": "inspur", "command": "python train.py"}))
    assert out["ok"] and out["pid"] == 42
    assert m.watched == [("c1", "inspur", 42)]


def test_log_tail_marks_and_scores(monkeypatch, tmp_path):
    c, _ = _client(monkeypatch)
    monkeypatch.setattr(tb, "_TMP_DIR", str(tmp_path))
    out = json.loads(_call(c, "log_tail",
                           {"server": "inspur", "ref": "logs/agent-x.log"}))
    assert out["ok"]
    assert "⚠" in out["text"]
    assert "NaN appeared" in out["text"]
```

（`_TMP_DIR` 缺省 `tempfile.gettempdir()`，测试注入）

- [ ] **Step 2: 运行确认失败**

Run: `.venv-win/Scripts/python -m pytest tests/test_remote_tools.py -q`
Expected: FAIL（SERVERS/_remote_tool 不存在）

- [ ] **Step 3: 实现 tool_backend 扩展**

模块级（CCF_PATH 之后）：

```python
SERVERS = None       # mount 时 load_servers(ROOT)
_TMP_DIR = None      # 测试注入临时目录


def _tmp_dir() -> str:
    import tempfile
    return _TMP_DIR or tempfile.gettempdir()
```

`mount_tool_backend` 里追加：

```python
    global SERVERS
    if SERVERS is None:
        from .executors import load_servers
        SERVERS = load_servers(ROOT)
```

（测试用 monkeypatch.setattr 覆盖，不受 None 守卫影响）

`BRIDGE_TOOLS` 列表追加五个 schema（description 面向模型，参数精炼）：

```python
BRIDGE_TOOLS += [
    {"type": "function", "function": {
        "name": "list_servers",
        "description": "列出可用的训练服务器（名称、类型、说明）。跑实验前先看。",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "gpu_status_remote",
        "description": "查看远程服务器显卡利用率/显存。server 省略则查所有远程机器。",
        "parameters": {"type": "object", "properties": {
            "server": {"type": "string", "description": "servers.json 里的名称"},
        }},
    }},
    {"type": "function", "function": {
        "name": "submit_train",
        "description": "向远程服务器提交训练：自动 git pull 后 nohup 启动，日志写入 logs/agent-*.log。需要用户确认。",
        "parameters": {"type": "object", "properties": {
            "server": {"type": "string"},
            "command": {"type": "string", "description": "训练命令，如 python train.py --lr 1e-3"},
            "gpu": {"type": "string", "description": "CUDA_VISIBLE_DEVICES 值，如 0 或 0,1；省略则不设"},
            "name": {"type": "string", "description": "任务名（workbench 型用）"},
        }, "required": ["server", "command"]},
    }},
    {"type": "function", "function": {
        "name": "log_tail",
        "description": "拉取远程任务的日志尾部（自动标注 NaN/OOM/Traceback 并给出最优指标摘要）。ref 用 submit_train 返回的 log 路径。",
        "parameters": {"type": "object", "properties": {
            "server": {"type": "string"},
            "ref": {"type": "string", "description": "日志路径或任务 id"},
            "lines": {"type": "integer", "description": "尾部行数，默认 80"},
        }, "required": ["server", "ref"]},
    }},
    {"type": "function", "function": {
        "name": "stop_train",
        "description": "停止远程服务器上的训练进程。ref 用 submit_train 返回的 pid。需要用户确认。",
        "parameters": {"type": "object", "properties": {
            "server": {"type": "string"},
            "ref": {"type": "string"},
        }, "required": ["server", "ref"]},
    }},
]
```

`call_tool` 的 run 分支后追加路由，并实现：

```python
    if name in ("list_servers", "gpu_status_remote", "submit_train",
                "log_tail", "stop_train"):
        return await _remote_tool(name, call)
```

```python
import re

_MARK = re.compile(r"(NaN|Inf[^a-z]|OOM|out of memory|Traceback|CUDA error)", re.I)


async def _remote_tool(name: str, call: ToolCall) -> dict:
    from .executors import make_executor
    servers = SERVERS or []
    if name == "list_servers":
        return {"result": json.dumps(
            {"ok": True, "servers": [
                {"name": s.get("name"), "type": s.get("type"),
                 "note": s.get("note", "")} for s in servers]},
            ensure_ascii=False)}

    def find(server):
        for s in servers:
            if s.get("name") == server:
                return s
        return None

    if name == "gpu_status_remote":
        targets = ([find(call.args["server"])] if call.args.get("server")
                   else [s for s in servers if s.get("type") in ("ssh", "workbench")])
        if not targets or targets[0] is None:
            return {"result": json.dumps(
                {"ok": False, "error": f"未知服务器 {call.args.get('server')}"}
                , ensure_ascii=False)}
        results = []
        for cfg in targets:
            ex = make_executor(cfg)
            results.append(await (ex.gpu_status() if ex else {"ok": False}))
        return {"result": json.dumps({"ok": True, "results": results},
                                     ensure_ascii=False, default=str)}

    cfg = find(call.args.get("server", ""))
    ex = make_executor(cfg) if cfg else None
    if ex is None:
        return {"result": json.dumps(
            {"ok": False, "error": f"服务器不存在或类型不支持: {call.args.get('server')}"}
            , ensure_ascii=False)}

    if name == "submit_train":
        out = await ex.submit(call.args["command"], gpu=call.args.get("gpu"),
                              name=call.args.get("name"))
        if out.get("ok") and hasattr(_manager, "watch_remote"):
            await _manager.watch_remote(call.conv, cfg, out)
        return {"result": json.dumps(out, ensure_ascii=False, default=str)}

    if name == "log_tail":
        out = await ex.logs(call.args["ref"], lines=int(call.args.get("lines") or 80))
        if not out.get("ok"):
            return {"result": json.dumps(out, ensure_ascii=False)}
        text = out.get("text", "")
        marked = "\n".join(
            ("⚠ " + l if _MARK.search(l) else l) for l in text.splitlines())
        summary = _score_summary(text)
        return {"result": json.dumps(
            {"ok": True, "server": cfg["name"], "text": marked, "scores": summary},
            ensure_ascii=False)}

    if name == "stop_train":
        out = await ex.stop(call.args["ref"])
        return {"result": json.dumps(out, ensure_ascii=False, default=str)}
    return {"result": json.dumps({"ok": False, "error": "unknown"}, ensure_ascii=False)}


def _score_summary(text: str) -> str:
    """日志文本 → 最优指标摘要（复用 experiments 解析）。"""
    import os
    import experiments as exp
    p = os.path.join(_tmp_dir(), f"wb-logtail-{os.getpid()}.log")
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        scores = exp.parse_scores(p)
        if not scores:
            return "（未解析到指标行）"
        key, s = exp.pick_score_series(scores)
        best = exp.series_best(s)
        return f"{key} 最好: {best}"
    except Exception as e:
        return f"（指标解析失败: {e}）"
    finally:
        try:
            os.remove(p)
        except OSError:
            pass
```

（若 `pick_score_series` 返回形状与上面假设不符——实现时以 `experiments.py:158` 实际签名为准调整这两行，测试 `test_log_tail_marks_and_scores` 只断言 ok/⚠/原文，不锁摘要格式）

- [ ] **Step 4: routes.py 加 GET /api/pi/servers**

`mount_pi` 里 `mount_tool_backend(app, manager)` 后追加：

```python
    from .executors import load_servers, make_executor, SSHRunner
    routes_servers = load_servers(root)
```

端点（放 convs 路由后）：

```python
@router.get("/api/pi/servers")
async def list_servers(wb_session: str = ""):
    _ws_user_cookie(wb_session)
    out = []
    for cfg in routes_servers:
        ok = None
        if cfg.get("type") == "local":
            ok = True
        elif cfg.get("type") == "ssh":
            try:
                r = SSHRunner(cfg["host"], cfg.get("port", 22), cfg["user"])
                code, _ = await asyncio.wait_for(r.run("echo ok", timeout=10), 10)
                ok = code == 0
            except Exception:
                ok = False
        elif cfg.get("type") == "workbench":
            ex = make_executor(cfg)
            ok = (await ex.gpu_status()).get("ok", False)
        out.append({"name": cfg.get("name"), "type": cfg.get("type"),
                    "note": cfg.get("note", ""), "ok": ok})
    return {"servers": out}
```

（模块级 `routes_servers = []` 初始化，mount 时填充）

- [ ] **Step 5: 扩展确认名单 + 重装**

`pi-extension/research/index.ts` 第一行常量改为：

```typescript
const CONFIRM_TOOLS = new Set(["stop_task", "submit_task", "save_report",
                               "submit_train", "stop_train"]);
```

```bash
cp pi-extension/research/index.ts "C:\Users\ZhouYan\.pi\agent\extensions\research\index.ts"
```

- [ ] **Step 6: 全量测试 + 冒烟 + 提交**

Run: `.venv-win/Scripts/python -m pytest tests/ -q && WORKBENCH_MOCK=1 .venv-win/Scripts/python -c "import server; print('ok')"`

```bash
git add bridge/ pi-extension/research/index.ts tests/test_remote_tools.py
git commit -m "feat(bridge): remote server tools with confirm gate and servers api"
```

---

### Task 3: 远程任务监控（watch_remote + RemoteWatcher）

**Files:**
- Modify: `bridge/sessions.py`（`watch_remote`/`_remote_watch`/`notify_fn`）
- Modify: `bridge/watcher.py`（RemoteWatcher 循环）
- Modify: `bridge/routes.py`（mount_pi 建 RemoteWatcher 与 shutdown 停止）
- Test: `tests/test_watcher.py`（追加）

**Interfaces:**
- Produces:
  - `PiSessionManager.notify_fn: Callable | None`（mount_pi 赋值；签名 `async (kind: str, text: str)`）
  - `async watch_remote(conv_id, cfg, out)`：登记 `self._remote_watch.append({"conv","cfg","pid":out["pid"],"log":out["log"],"server":out["server"]})`
  - `RemoteWatcher(manager, servers, interval=30)`：`loop()`（对每个登记项：`make_executor(cfg)`（带真实 SSHRunner）→ `alive(pid)`，False 时 `logs(log, lines=120)` → `inject_task_event(conv, text)` → `notify_fn("remote_task", ...)` → 移除登记）；`start()/stop()` 同 TaskWatcher（async handler 内 create_task）
  - 本地 TaskWatcher 注入处同时调用 `manager.notify_fn("task", text)`（若非 None，try/except 包裹）

- [ ] **Step 1: 失败测试（追加到 tests/test_watcher.py）**

```python
async def test_remote_watcher_reports_finish(tmp_path, monkeypatch):
    import bridge.watcher as w
    from bridge.sessions import PiSessionManager

    m = PiSessionManager(str(tmp_path))
    m._remote_watch.append({"conv": "c1", "pid": 5, "log": "logs/a.log",
                            "server": "inspur",
                            "cfg": {"name": "inspur", "type": "ssh", "host": "h",
                                    "port": 22, "user": "u", "repo_path": "~/r",
                                    "log_dir": "logs/"}})
    injected, notified = [], []

    async def fake_inject(conv, text):
        injected.append((conv, text))

    async def fake_notify(kind, text):
        notified.append((kind, text))

    monkeypatch.setattr(m, "inject_task_event", fake_inject)
    m.notify_fn = fake_notify

    class FakeEx:
        async def alive(self, pid):
            return {"ok": False}

        async def logs(self, ref, lines=80):
            return {"ok": True, "text": "final loss 0.1"}

    import bridge.executors as ex_mod
    monkeypatch.setattr(ex_mod, "make_executor", lambda cfg: FakeEx())
    rw = w.RemoteWatcher(m)
    await rw.poll_once()
    assert injected and "final loss" in injected[0][1]
    assert notified and notified[0][0] == "remote_task"
    assert m._remote_watch == []          # 已移除
    await rw.poll_once()                   # 空转不报错
```

- [ ] **Step 2: 运行确认失败，然后实现**

sessions.py `__init__` 追加：

```python
        self.notify_fn = None               # 由 mount_pi 注入（飞书）
        self._remote_watch: list[dict] = []
```

`inject_task_event` 后追加：

```python
    async def watch_remote(self, conv_id: str, cfg: dict, out: dict) -> None:
        self._remote_watch.append({
            "conv": conv_id, "cfg": cfg, "pid": out.get("pid"),
            "log": out.get("log"), "server": out.get("server")})
```

watcher.py 追加 RemoteWatcher：

```python
class RemoteWatcher:
    """轮询桥登记的远端 pid；结束时拉日志注入会话并飞书通知。"""

    def __init__(self, manager, interval: float = 30.0):
        self.manager = manager
        self.interval = interval
        self._task: asyncio.Task | None = None

    async def poll_once(self) -> None:
        from .executors import make_executor
        for item in list(self.manager._remote_watch):
            ex = make_executor(item["cfg"])
            if ex is None:
                self.manager._remote_watch.remove(item)
                continue
            try:
                alive = await ex.alive(item["pid"])
            except Exception:
                alive = {"ok": True}   # 查询失败按存活处理，下轮再查
            if alive.get("ok"):
                continue
            text = ""
            try:
                logs = await ex.logs(item["log"], lines=120)
                text = logs.get("text", "")
            except Exception:
                pass
            msg = (f"【系统事件】远程任务结束：{item['server']} pid={item['pid']}。\n"
                   f"日志尾部：\n{text[-1500:]}")
            try:
                await self.manager.inject_task_event(item["conv"], msg)
            except Exception:
                log.warning("remote inject failed", exc_info=True)
            if self.manager.notify_fn:
                try:
                    await self.manager.notify_fn("remote_task", msg)
                except Exception:
                    log.warning("feishu notify failed", exc_info=True)
            self.manager._remote_watch.remove(item)

    async def loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.warning("remote watcher poll failed", exc_info=True)
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
```

本地 TaskWatcher 的 `inject_task_event` 调用后追加飞书（poll_once 中注入成功处）：

```python
            if self.manager.notify_fn:
                try:
                    await self.manager.notify_fn("task", text)
                except Exception:
                    log.warning("feishu notify failed", exc_info=True)
```

routes.py 的 mount_pi：watcher 挂载块追加：

```python
    from .watcher import RemoteWatcher
    remote_watcher = RemoteWatcher(manager)

    async def _start_rw():
        remote_watcher.start()

    async def _stop_rw():
        remote_watcher.stop()

    app.on_event("startup")(_start_rw)
    app.on_event("shutdown")(_stop_rw)
```

- [ ] **Step 3: 全量测试 + 提交**

Run: `.venv-win/Scripts/python -m pytest tests/ -q`
Expected: 全部 passed

```bash
git add bridge/ tests/test_watcher.py
git commit -m "feat(bridge): remote pid watcher with session inject and notify hook"
```

---

### Task 4: 飞书注入（server 侧）

**Files:**
- Modify: `server.py`（mount_pi 调用传 notify）
- Test: 既有 test_watcher 的 fake_notify 已覆盖调用点；本任务无新单测（feishu.notify 是现成代码）

**Interfaces:**
- Produces: `mount_pi(app, ROOT, resolve_token, ctx_factory=_agent_ctx, notify=None)`；notify 为 `lambda kind, text: feishu.notify(db, kind, text)`（feishu.notify 自查 settings 开关与 webhook，无需桥侧判断）

- [ ] **Step 1: server.py 挂载调用改为**

```python
mount_pi(app, ROOT, resolve_token, ctx_factory=_agent_ctx,
         notify=lambda kind, text: feishu.notify(db, kind, text))
```

（feishu 已在 server.py 顶部 import；mount_pi 的 notify 参数存入 `manager.notify_fn`）

- [ ] **Step 2: routes.py 的 mount_pi 签名与赋值**

```python
def mount_pi(app, root, resolve_token_fn, ctx_factory=None, notify=None) -> None:
    ...
    manager.notify_fn = notify
```

- [ ] **Step 3: 冒烟 + 提交**

```bash
WORKBENCH_MOCK=1 .venv-win/Scripts/python -c "import server; print('ok')" && .venv-win/Scripts/python -m pytest tests/ -q
```

```bash
git add server.py bridge/routes.py
git commit -m "feat: wire feishu notify into bridge watchers"
```

---

### Task 5: UI 机器面板 + README + 验收

**Files:**
- Modify: `static/app.js`（机器面板接真数据）
- Modify: `README.md`
- Create: `tests/e2e_remote.py`（FakeRunner 链路）

**Interfaces:**
- Consumes: `GET /api/pi/servers`

- [ ] **Step 1: app.js 机器面板**

data 加 `servers: []`；mounted 里 `loadConvs` 后调：

```javascript
    async loadServers() {
      const r = await fetch("/api/pi/servers", { credentials: "include" });
      if (r.ok) this.servers = (await r.json()).servers;
    },
```

模板机器区替换为：

```html
      <div class="sec">机器</div>
      <div v-for="s in servers" :key="s.name" class="machine">
        {{ s.ok ? "●" : "○" }} {{ s.name }}<span v-if="s.note" style="color:#7c86ad"> · {{ s.note }}</span>
      </div>
```

- [ ] **Step 2: e2e_remote.py（不起 server，直连 tool_backend 的 FakeRunner 链路）**

```python
"""远程工具链冒烟（FakeRunner，无网络）：gpu_status_remote/submit_train/log_tail。
用法：.venv-win/Scripts/python tests/e2e_remote.py"""
import asyncio
import json
import sys
import tempfile
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bridge.tool_backend as tb
from bridge.executors import SSHExecutor


class R:
    async def run(self, cmd, timeout=30):
        if "nvidia-smi" in cmd:
            return 0, "0, RTX 4090, 7, 3000, 24576"
        if "echo PID=$!" in cmd:
            return 0, "PID=777"
        if "tail" in cmd:
            return 0, "epoch 3 acc 0.91\nTraceback (most recent call last):"
        return 0, ""


async def main():
    cfg = {"name": "t-ssh", "type": "ssh", "host": "h", "port": 22, "user": "u",
           "repo_path": "~/r", "log_dir": "logs/"}
    tb.SERVERS = [cfg]
    tb._TMP_DIR = tempfile.mkdtemp()

    ex = SSHExecutor(cfg, runner=R())
    orig_make = tb.make_executor if hasattr(tb, "make_executor") else None
    tb.make_executor = lambda c, runner=None: ex

    gpu = json.loads((await tb._remote_tool("gpu_status_remote",
        type("C", (), {"args": {}, "conv": "c"})())).__getitem__("result"))
    assert gpu["ok"] and gpu["results"][0]["gpus"][0]["util"] == "7%"
    sub = json.loads((await tb._remote_tool("submit_train",
        type("C", (), {"args": {"server": "t-ssh", "command": "x"}, "conv": "c"})()))["result"])
    assert sub["ok"] and sub["pid"] == 777
    log = json.loads((await tb._remote_tool("log_tail",
        type("C", (), {"args": {"server": "t-ssh", "ref": "logs/a.log"}, "conv": "c"})()))["result"])
    assert log["ok"] and "⚠" in log["text"]
    print("REMOTE TOOLS E2E OK")


asyncio.run(main())
```

（`_manager` 需要一个带 `user_for`/`watch_remote` 的假对象——直接 `tb._manager = type("M", (), {"user_for": lambda s, c: {"username": "u"}, "watch_remote": None})()`；submit_train 分支里 `hasattr(_manager, "watch_remote")` 为 True 时会调用，赋一个 async 假函数 `async def wr(*a): pass` 挂到类上）

Run: `.venv-win/Scripts/python -u tests/e2e_remote.py` → `REMOTE TOOLS E2E OK`

- [ ] **Step 3: README 更新（servers.json 配置示例、SSH 免密一次性配置、隧道注意事项）+ 真机验收清单**

README 追加：

```markdown
## 多服务器（第 4 期）

- `servers.json`（仓库根，已 gitignore）：
  ```json
  [{"name": "local", "type": "local"},
   {"name": "inspur", "type": "ssh", "host": "127.0.0.1", "port": 15000,
    "user": "niexin", "repo_path": "~/PcmDiffFL", "log_dir": "logs/"}]
  ```
- SSH 免密（一次性）：`ssh-keygen -t ed25519` 后把 `~/.ssh/id_ed25519.pub` 追加到服务器 `~/.ssh/authorized_keys`
- 注意：school-tunnel 若把远端 8787 转到本地 8787 会与本服务冲突，SSH 走 15000 即可
- 智能体用法：对话「看下 inspur 的卡」「在 inspur 上跑 python train.py」（提交前自动 git pull，需确认）
- 任务结束自动汇报 + 飞书推送（settings 里配 webhook）

### 真机验收（开隧道后手动）
1. 「列出服务器」→ 侧栏 ● inspur
2. 「看下 inspur 显卡」→ 真实卡况
3. 「在 inspur 上跑 sleep 60」→ 确认卡 → 返回 pid/log
4. 「看下它的日志」→ 拉回尾部
5. 等待/kill 远端进程 → 会话收到系统事件并分析
```

- [ ] **Step 4: 提交**

```bash
git add static/app.js tests/e2e_remote.py README.md
git commit -m "feat(web): live server panel; remote tools e2e and docs"
```

---

## Self-Review 记录

- **Spec 覆盖**：§3.5 全部（注册表/SSH 主力/workbench 备用/统一接口/5 工具/确认门禁）→ Task 1/2；§3.5b（RemoteWatcher/飞书只推结果）→ Task 3/4；UI 机器面板 → Task 5。
- **占位符**：Task 2 中「pick_score_series 以实际签名为准调整」是显式的实现期核对指令（附了精确行号），非缺失；真机验收是用户侧清单。
- **类型一致性**：`runner.run -> (code, output)`、执行器五方法返回 `{"ok":...}`、`watch_remote(conv, cfg, out)`、`notify_fn(kind, text)` 跨任务一致；`make_executor(cfg)` 在 executors 定义、tool_backend/routes/watcher 处的调用（含测试 monkeypatch 目标）一致。
- **已知风险**：WorkbenchExecutor 的 /api/gpu、/api/tasks 响应形状按本仓库 server 实现 assumed，跨版本远端可能有差异——alive/logs 的解析已做宽松处理，真机验收兜底。
