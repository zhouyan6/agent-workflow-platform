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

    def _resume_path(self, conv_id: str) -> str | None:
        path = self._load_index().get(conv_id)
        return path if path and os.path.isfile(path) else None

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
        # 不传 --provider/--model 时 Pi 会连默认 Anthropic（403），必须显式指定
        return ["--mode", "rpc", "--session-dir", self.sessions_dir,
                "--provider", "zhipu", "--model", "glm-4.7", "-n", conv_id]

    async def get(self, conv_id: str) -> PiClient:
        client = self._clients.get(conv_id)
        if client and client.process.alive:
            return client
        proc = PiProcess(self._spawn_args(conv_id), cwd=self.root,
                         env=self._zhipu_env())
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
                idx[conv_id] = path
                self._save_index(idx)
        except Exception:
            log.warning("record session path failed", exc_info=True)
        return client

    async def shutdown(self) -> None:
        for client in self._clients.values():
            try:
                await client.close()
            except Exception:
                log.warning("client close failed", exc_info=True)
        self._clients.clear()
