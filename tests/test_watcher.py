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
