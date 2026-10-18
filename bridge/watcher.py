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
            self._task = asyncio.create_task(self.loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
