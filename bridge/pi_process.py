# bridge/pi_process.py
"""Pi RPC 子进程管理。"""
import asyncio
import json
import logging
import os
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
            if os.name == "nt":
                # terminate() 只杀 pi.cmd 外壳，node 子进程会存活并占住 stdout
                # 管道导致 wait()/close() 挂住——必须整树杀掉
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(self.proc.pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
            else:
                self.proc.terminate()
            await self.proc.wait()
