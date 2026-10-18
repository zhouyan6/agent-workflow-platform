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
        self.bridge_token: str | None = None    # 由 mount_pi 赋值
        self.bridge_url: str | None = None      # 由 mount_pi 赋值
        self.ctx_factory = None                 # 由 mount_pi 赋值（server._agent_ctx）
        self._users: dict[str, dict] = {}
        self._plans: dict[str, list] = {}

    # ---- conv_id → session 文件 映射（Pi 文件名是 时间戳_uuid.jsonl，
    # 不含会话名，无法按名匹配，必须由桥自己记录）----

    def _index_path(self) -> str:
        return os.path.join(self.sessions_dir, "index.json")

    def _load_index(self) -> dict:
        try:
            with open(self._index_path(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_index(self, idx: dict) -> None:
        tmp = self._index_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self._index_path())

    @staticmethod
    def _norm_entry(conv_id: str, val) -> dict:
        if isinstance(val, dict):
            return {"path": val.get("path", ""), "title": val.get("title") or conv_id,
                    "updated": val.get("updated", "")}
        return {"path": str(val), "title": conv_id, "updated": ""}

    def _conv_entries(self) -> dict[str, dict]:
        return {cid: self._norm_entry(cid, v)
                for cid, v in self._load_index().items()}

    def _resume_path(self, conv_id: str) -> str | None:
        path = self._conv_entries().get(conv_id, {}).get("path")
        return path if path and os.path.isfile(path) else None

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

    def _spawn_args(self, conv_id: str) -> list[str]:
        # 不传 --provider/--model 时 Pi 会连默认 Anthropic（403），必须显式指定
        return ["--mode", "rpc", "--session-dir", self.sessions_dir,
                "--provider", "zhipu", "--model", "glm-4.7", "-n", conv_id]

    async def get(self, conv_id: str, user: dict | None = None) -> PiClient:
        if user is not None:
            self._users[conv_id] = user
        client = self._clients.get(conv_id)
        if client and client.process.alive:
            return client
        proc = PiProcess(self._spawn_args(conv_id), cwd=self.root,
                         env=self._pi_env(conv_id))
        await proc.start()
        client = PiClient(proc)
        self._clients[conv_id] = client
        resume = self._resume_path(conv_id)
        if resume:
            try:
                await client.switch_session(resume)
                log.info("resumed pi session %s", resume)
            except Exception:
                log.warning("switch_session failed, continuing fresh",
                            exc_info=True)
        # 记录本会话实际的 session 文件路径（新会话或恢复后均以 get_state 为准）
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
        return client

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

    async def shutdown(self) -> None:
        for client in self._clients.values():
            try:
                await client.close()
            except Exception:
                log.warning("client close failed", exc_info=True)
        self._clients.clear()

    def user_for(self, conv_id: str) -> dict | None:
        return self._users.get(conv_id)

    async def push_plan(self, conv_id: str, steps: list) -> None:
        self._plans[conv_id] = steps
        client = self._clients.get(conv_id)
        if client and client.process.alive:
            await client.stream_events.put(
                {"type": "_bridge_plan", "steps": steps})
