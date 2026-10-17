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
