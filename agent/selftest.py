"""Offline checks for agent sessions, tools, and the tool-calling loop."""
import asyncio
import json
import os
import tempfile

from db import DB
from agent import artifacts, loop, routes, store, tools


def _db():
    return DB(os.path.join(tempfile.mkdtemp(), "agent.db"))


def _ctx(db, user, snapshot=None, path_allowed=None, proc_allowed=None, best_log=None,
         session_id=None):
    return tools.ToolCtx(
        user=user,
        db=db,
        snapshot=snapshot or (lambda: {"mock": True, "gpus": [
            {"index": 0, "util": 0, "mem_used": 10, "mem_total": 80000, "procs": []},
            {"index": 1, "util": 90, "mem_used": 40000, "mem_total": 80000,
             "procs": [{"pid": 9, "name": "python", "mem": 40000, "user": "zhouyan", "cmd": "train"}]},
        ]}),
        path_allowed=path_allowed or (lambda path: False),
        proc_allowed=proc_allowed or (lambda pid: False),
        best_log=best_log or (lambda pid: None),
        session_id=session_id,
    )


def test_session_isolation():
    db = _db()
    student = {"username": "zhouyan", "role": "student"}
    advisor = {"username": "niexin", "role": "advisor"}
    sid = store.create_session(db, student, "新对话")
    store.rename_session(db, sid, "看 GPU")
    assert store.get_session(db, sid, student)["title"] == "看 GPU"
    assert store.get_session(db, sid, {"username": "lihan", "role": "student"}) is None
    assert store.get_session(db, sid, advisor)["owner"] == "zhouyan"
    store.add_message(db, sid, "user", "你好")
    assert store.list_messages(db, sid)[0]["content"] == "你好"
    store.delete_session(db, sid)
    assert store.get_session(db, sid, advisor) is None
    print("session isolation ok")


def test_tools():
    db = _db()
    student = {"username": "zhouyan", "role": "student"}
    ctx = _ctx(db, student)
    gpu = json.loads(tools.dispatch("gpu_status", {}, ctx))
    assert gpu["ok"] and gpu["idle_gpus"] == [0]

    db.execute(
        "INSERT INTO tasks(name, command, gpus_needed, status, owner, log_file, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("exp-a", "python train.py", 1, "running", "zhouyan", "", 1))
    db.execute(
        "INSERT INTO tasks(name, command, gpus_needed, status, owner, created_at) "
        "VALUES(?,?,?,?,?,?)",
        ("secret", "python other.py", 1, "running", "lihan", 1))
    queued = json.loads(tools.dispatch("task_queue", {}, ctx))
    assert [t["name"] for t in queued["tasks"]] == ["exp-a"]
    assert queued["tasks"][0]["source"] == "workbench"

    ctx.external_tasks = lambda: [{
        "id": -42, "source": "external", "name": "train.py", "status": "running",
        "owner": "zhouyan", "gpu_assigned": "0", "pid": 42,
        "command": "python train.py", "log_file": None, "exit_code": None,
    }]
    with_ext = json.loads(tools.dispatch("task_queue", {}, ctx))
    assert [t["name"] for t in with_ext["tasks"]] == ["train.py", "exp-a"]
    assert with_ext["tasks"][0]["source"] == "external"
    assert with_ext["tasks"][0]["pid"] == 42
    pending = json.loads(tools.dispatch("task_queue", {"status": "pending"}, ctx))
    assert pending["tasks"] == []
    ctx.external_tasks = None

    log = os.path.join(tempfile.mkdtemp(), "train.log")
    with open(log, "w", encoding="utf-8") as f:
        f.write("epoch 1 loss 0.4\nTraceback (most recent call last):\nRuntimeError: CUDA out of memory\n")
    db.execute("UPDATE tasks SET log_file=? WHERE name='exp-a'", (log,))
    diag = json.loads(tools.dispatch("log_diagnose", {"task_id": 1}, ctx))
    assert diag["ok"]
    assert "OOM" in diag["signals"] and "Python异常" in diag["signals"]

    denied = json.loads(tools.dispatch("log_diagnose", {"path": log}, ctx))
    assert denied["ok"] is False

    db.execute(
        "INSERT INTO papers(title, source_type, owner, card_json, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?)",
        ("FedPaper", "pdf", "zhouyan", json.dumps({"文献信息": {"标题": "FedPaper", "摘要": "联邦"}}), 1, 1))
    db.execute(
        "INSERT INTO papers(title, source_type, owner, card_json, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?)",
        ("Hidden", "pdf", "lihan", json.dumps({"文献信息": {"标题": "Hidden"}}), 1, 1))
    found = json.loads(tools.dispatch("paper_lookup", {"query": "Fed"}, ctx))
    assert [p["title"] for p in found["papers"]] == ["FedPaper"]
    hidden = json.loads(tools.dispatch("paper_lookup", {"query": "Hidden"}, ctx))
    assert hidden["papers"] == []
    print("tools ok")


def test_sse():
    state = loop.new_stream_state()
    assert loop.consume_sse_line(state, 'data: {"choices":[{"delta":{"content":"你"}}]}') == "你"
    assert loop.consume_sse_line(state, 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"gpu_status","arguments":""}}]}}]}') is None
    assert loop.consume_sse_line(state, 'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{}"}}]}}]}') is None
    text, calls = loop.finalize_state(state)
    assert text == "你"
    assert calls[0]["function"]["name"] == "gpu_status"
    assert calls[0]["function"]["arguments"] == "{}"
    assert loop.consume_sse_line(state, "data: [DONE]") == "done"
    print("sse ok")


class FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, obj):
        self.sent.append(obj)


def test_turn():
    db = _db()
    user = {"username": "zhouyan", "role": "student"}
    sid = store.create_session(db, user)
    seen = {"n": 0, "saw_tool": False}

    async def fake(messages, schema, on_token):
        seen["n"] += 1
        if seen["n"] == 1:
            return "", [{
                "id": "c1", "type": "function",
                "function": {"name": "gpu_status", "arguments": "{}"},
            }]
        seen["saw_tool"] = any(m.get("role") == "tool" for m in messages)
        await on_token("GPU0 空闲")
        return "GPU0 空闲", []

    loop.complete_stream = fake
    ws = FakeWS()
    deps = routes.Deps(db, lambda u, sid=None: _ctx(db, u, session_id=sid))

    async def main():
        await routes.handle_turn(ws, user, {"session_id": sid, "content": "哪张卡空着"}, deps)

    asyncio.run(main())
    events = [m["event"] for m in ws.sent]
    assert events[0] == "tool_start"
    assert "tool_end" in events and events[-1] == "done"
    assert seen["saw_tool"]
    roles = [m["role"] for m in store.list_messages(db, sid)]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert store.get_session(db, sid, user)["title"] == "哪张卡空着"
    print("turn ok")


def test_artifacts_and_confirm():
    saved = artifacts.write_text("zhouyan", 7, "复盘.md", "# 复盘\n一切正常")
    assert saved["name"] == "复盘.md"
    assert artifacts.list_files("lihan", 7) == []
    assert "一切正常" in artifacts.read_text(artifacts.resolve("zhouyan", 7, "复盘.md"))
    assert artifacts.safe_name("../secret.md") == "secret.md"
    kept = artifacts.resolve("zhouyan", 7, "复盘.md")
    assert kept.startswith(artifacts.session_dir("zhouyan", 7) + os.sep)
    try:
        artifacts.resolve("zhouyan", 7, "../../etc/passwd")
        raise AssertionError("traversal resolve succeeded")
    except ValueError:
        pass
    artifacts.remove_session("zhouyan", 7)
    assert artifacts.list_files("zhouyan", 7) == []

    db = _db()
    user = {"username": "zhouyan", "role": "student"}
    ctx = _ctx(db, user, session_id=7)
    stopped = []
    ctx.stop_task = lambda tid: stopped.append(tid) or {"task_id": tid, "status": "cancelled"}
    state = {"n": 0}

    async def fake(messages, schema, on_token):
        state["n"] += 1
        if state["n"] == 1:
            return "", [{
                "id": "c9", "type": "function",
                "function": {"name": "stop_task", "arguments": '{"task_id": 3}'},
            }]
        await on_token("已取消")
        return "已取消", []

    events = []

    async def emit(event, **data):
        events.append(event)

    async def deny(_cid):
        return False

    loop.complete_stream = fake
    asyncio.run(loop.run_turn(
        [{"role": "user", "content": "停掉 3"}], ctx, emit, [], confirm=deny))
    assert "confirm" in events and stopped == []

    state["n"] = 0
    events.clear()

    async def allow(_cid):
        return True

    asyncio.run(loop.run_turn(
        [{"role": "user", "content": "停掉 3"}], ctx, emit, [], confirm=allow))
    assert stopped == [3]

    report = json.loads(tools.dispatch(
        "save_report", {"name": "note.md", "content": "hello"}, ctx))
    assert report["ok"] and report["saved"] == "note.md"
    assert artifacts.list_files("zhouyan", 7)[0]["name"] == "note.md"
    artifacts.remove_session("zhouyan", 7)
    print("artifacts and confirm ok")


def test_best_result_flexible():
    db = _db()
    user = {"username": "zhouyan", "role": "student"}
    log = os.path.join(tempfile.mkdtemp(), "train.log")
    with open(log, "w", encoding="utf-8") as f:
        f.write("Round 1: val accuracy=0.40\n")
        f.write("Round 2: val accuracy=0.81\n")
        f.write("Round 1: val score=10\n")
        f.write("Round 2: val loss=2.0\n")
        f.write("Round 3: val loss=0.4\n")
    ctx = _ctx(db, user, path_allowed=lambda path: True)
    vague = json.loads(tools.dispatch("best_result", {"path": log, "hint": "最高得分"}, ctx))
    assert vague["ok"] and vague["rows"][0]["headline"]["metric"] == "accuracy"
    assert vague["rows"][0]["headline"]["best"] == 0.81
    named = json.loads(tools.dispatch("best_result", {"path": log, "hint": "最高准确率"}, ctx))
    assert named["rows"][0]["headline"]["metric"] == "accuracy"
    loss = next(x for x in named["rows"][0]["others"] if x["metric"] == "loss")
    assert loss["higher_is_better"] is False and loss["best"] == 0.4
    print("best result ok")


def test_review_and_cases():
    from datetime import datetime
    from agent import jobs, library
    sat = datetime(2026, 9, 26, 22, 0)  # Saturday
    assert jobs.cron_matches("0 22 * * *", sat)
    assert not jobs.cron_matches("0 21 * * *", sat)
    assert jobs.cron_matches("0 22 * * 6", sat)
    assert not jobs.cron_matches("0 22 * * 1", sat)
    try:
        jobs.validate_cron("not a cron")
        raise AssertionError("bad cron accepted")
    except ValueError:
        pass
    assert jobs.is_due(
        {"enabled": 1, "cron": "0 22 * * *", "timezone": "Asia/Shanghai", "last_run_at": None}, sat)
    assert not jobs.is_due(
        {"enabled": 0, "cron": "0 22 * * *", "timezone": "Asia/Shanghai", "last_run_at": None}, sat)

    db = _db()
    db.execute(
        "INSERT INTO users(username, role, pw_hash, created_at) VALUES(?,?,?,?)",
        ("zhouyan", "student", "x", 1))
    jobs.ensure_default_jobs(db)
    job = jobs.list_jobs(db, {"username": "zhouyan"})[0]
    assert job["enabled"] == 0 and job["cron"] == "0 22 * * *"

    log = os.path.join(tempfile.mkdtemp(), "bad.log")
    with open(log, "w", encoding="utf-8") as f:
        f.write("loss nan\n")
    db.execute(
        "INSERT INTO tasks(name, command, gpus_needed, status, owner, log_file, finished_at, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("bad-run", "python train.py", 1, "failed", "zhouyan", log, 100, 1))
    db.execute(
        "INSERT INTO tasks(name, command, gpus_needed, status, owner, finished_at, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("ok-run", "python train.py", 1, "done", "zhouyan", 100, 1))
    found = jobs.anomalies_since(db, "zhouyan", 0)
    assert [i["name"] for i in found] == ["bad-run"]
    assert "NaN" in found[0]["signals"]

    user = {"username": "zhouyan"}
    a = library.save_case(db, user, {"title": "A", "symptom": "爆显存", "action": "减 batch"})
    b = library.save_case(db, user, {"title": "B", "symptom": "NaN", "cause": "lr 太大"})
    merged = library.merge_cases(db, user, a["id"], b["id"])
    assert "爆显存" in merged["symptom"] and "NaN" in merged["symptom"]
    assert library._case(db, user, b["id"]) is None
    assert library._case(db, {"username": "lihan"}, merged["id"]) is None
    created = library.split_case(db, user, merged["id"])["created"]
    assert created["title"].endswith("（拆分）")
    assert created["action"] == "减 batch"

    library.save_profile(db, user, "回答用短句", ["gpu_status"])
    names = [t["function"]["name"] for t in tools.openai_tools(library.enabled_skills(db, "zhouyan"))]
    assert names == ["gpu_status"]
    prompt = tools.system_prompt(db, "zhouyan")
    assert "回答用短句" in prompt and "paper_lookup" not in prompt
    ctx = _ctx(db, user)
    ctx.enabled_skills = {"gpu_status"}
    denied = json.loads(tools.dispatch("paper_lookup", {"query": ""}, ctx))
    assert denied["ok"] is False
    print("review and cases ok")


def test_app_imports():
    from fastapi.testclient import TestClient
    import server
    with TestClient(server.app) as client:
        assert client.get("/api/agent/sessions").status_code == 401
        assert client.get("/api/agent/skills").status_code == 401
    print("app import ok")


if __name__ == "__main__":
    test_session_isolation()
    test_tools()
    test_sse()
    test_turn()
    test_artifacts_and_confirm()
    test_best_result_flexible()
    test_review_and_cases()
    test_app_imports()
    print("all ok")
