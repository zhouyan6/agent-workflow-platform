"""HTTP and WebSocket endpoints for the research agent."""
import asyncio
import json
import os

from fastapi import Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

import chat
from agent import artifacts, jobs, library, loop, store, tools


class SessionIn(BaseModel):
    title: str = "新对话"


class JobIn(BaseModel):
    enabled: bool = False
    cron: str = "0 22 * * *"
    timezone: str = "Asia/Shanghai"


class CaseIn(BaseModel):
    title: str = ""
    experiment: str = ""
    symptom: str = ""
    cause: str = ""
    action: str = ""
    status: str = "open"
    source_file: str = ""


class MergeIn(BaseModel):
    ids: list[int]


class ProfileIn(BaseModel):
    prompt: str = ""
    skills: list[str] | None = None


class Deps:
    def __init__(self, db, ctx_factory):
        self.db = db
        self.ctx_factory = ctx_factory
        self.locks = {}

    def lock(self, session_id):
        if session_id not in self.locks:
            self.locks[session_id] = asyncio.Lock()
        return self.locks[session_id]


async def _wait_confirm(ws, confirm_id, timeout=180):
    """Read the socket until the matching confirm message, or time out as cancel."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remain = deadline - asyncio.get_running_loop().time()
        if remain <= 0:
            return False
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=remain)
        except asyncio.TimeoutError:
            return False
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if str(payload.get("confirm_id") or "") == str(confirm_id):
            return bool(payload.get("approved"))


def _persist_steps(db, session_id, steps):
    for step in steps:
        store.add_message(
            db, session_id, step["role"],
            content=step.get("content") or "",
            tool_name=step.get("tool_name"),
            tool_call_id=step.get("tool_call_id"),
            tool_calls=step.get("tool_calls"),
        )


async def handle_turn(ws, user, payload, deps):
    try:
        sid = int(payload.get("session_id") or 0)
    except (TypeError, ValueError):
        await ws.send_json({"event": "error", "message": "对话不存在"})
        return
    content = (payload.get("content") or "").strip()
    if not content:
        await ws.send_json({"event": "error", "message": "请输入内容"})
        return
    if len(content) > 8000:
        await ws.send_json({"event": "error", "message": "内容过长，请控制在 8000 字以内"})
        return
    sess = store.get_session(deps.db, sid, user)
    if not sess:
        await ws.send_json({"event": "error", "message": "对话不存在"})
        return
    lock = deps.lock(sid)
    if lock.locked():
        await ws.send_json({"event": "error", "message": "上一条还在生成"})
        return
    async with lock:
        if (sess.get("title") or "新对话") == "新对话":
            store.rename_session(deps.db, sid, content[:40])
        store.add_message(deps.db, sid, "user", content)
        run_id = store.start_run(deps.db, sid, user["username"])
        rows = store.list_messages(deps.db, sid)
        history = [{"role": "system", "content": tools.system_prompt(deps.db, user["username"])}]
        history.extend(store.to_model_messages(rows))
        sink = []

        async def emit(event, **data):
            await ws.send_json({"event": event, **data})

        try:
            await loop.run_turn(
                history, deps.ctx_factory(user, sid), emit, sink,
                confirm=lambda cid: _wait_confirm(ws, cid))
        except WebSocketDisconnect:
            _persist_steps(deps.db, sid, sink)
            store.finish_run(deps.db, run_id, "error", "disconnected")
            raise
        except chat.ChatError as e:
            _persist_steps(deps.db, sid, sink)
            store.add_message(deps.db, sid, "assistant", "错误: " + str(e))
            store.finish_run(deps.db, run_id, "error", str(e))
            await ws.send_json({"event": "error", "message": str(e), "session_id": sid})
            return
        except Exception as e:
            _persist_steps(deps.db, sid, sink)
            msg = f"助手出错: {type(e).__name__}"
            store.add_message(deps.db, sid, "assistant", msg)
            store.finish_run(deps.db, run_id, "error", str(e)[:300])
            await ws.send_json({"event": "error", "message": msg, "session_id": sid})
            return
        _persist_steps(deps.db, sid, sink)
        store.finish_run(deps.db, run_id, "done")
        await ws.send_json({"event": "done", "session_id": sid})


def mount_agent(app, *, db, get_current_user, resolve_token, ctx_factory):
    deps = Deps(db, ctx_factory)
    store.seed_skills(db, tools.BUILTIN_SKILLS)
    jobs.ensure_default_jobs(db)

    @app.get("/api/agent/sessions")
    async def list_agent_sessions(user=Depends(get_current_user)):
        return store.list_sessions(db, user)

    @app.post("/api/agent/sessions")
    async def create_agent_session(body: SessionIn, user=Depends(get_current_user)):
        sid = store.create_session(db, user, body.title)
        return store.get_session(db, sid, user)

    @app.delete("/api/agent/sessions/{session_id}")
    async def delete_agent_session(session_id: int, user=Depends(get_current_user)):
        row = store.get_session(db, session_id, user)
        if not row:
            raise HTTPException(404, "对话不存在")
        artifacts.remove_session(row["owner"], session_id)
        store.delete_session(db, session_id)
        return {"ok": True}

    @app.get("/api/agent/sessions/{session_id}/messages")
    async def agent_messages(session_id: int, user=Depends(get_current_user)):
        row = store.get_session(db, session_id, user)
        if not row:
            raise HTTPException(404, "对话不存在")
        return store.list_messages(db, session_id)

    @app.get("/api/agent/sessions/{session_id}/artifacts")
    async def list_artifacts(session_id: int, user=Depends(get_current_user)):
        row = store.get_session(db, session_id, user)
        if not row:
            raise HTTPException(404, "对话不存在")
        return artifacts.list_files(row["owner"], session_id)

    @app.get("/api/agent/sessions/{session_id}/artifacts/{name}")
    async def get_artifact(session_id: int, name: str, download: int = 0,
                           user=Depends(get_current_user)):
        row = store.get_session(db, session_id, user)
        if not row:
            raise HTTPException(404, "对话不存在")
        try:
            path = artifacts.resolve(row["owner"], session_id, name)
        except ValueError as e:
            raise HTTPException(404, str(e))
        if download:
            return FileResponse(path, filename=os.path.basename(path),
                                media_type="text/markdown")
        return {"name": os.path.basename(path), "content": artifacts.read_text(path)}

    @app.delete("/api/agent/sessions/{session_id}/artifacts/{name}")
    async def delete_artifact(session_id: int, name: str, user=Depends(get_current_user)):
        row = store.get_session(db, session_id, user)
        if not row:
            raise HTTPException(404, "对话不存在")
        try:
            artifacts.delete_file(row["owner"], session_id, name)
        except ValueError as e:
            raise HTTPException(404, str(e))
        return {"ok": True}

    @app.get("/api/agent/jobs")
    async def agent_jobs(user=Depends(get_current_user)):
        jobs.ensure_default_jobs(db)
        return jobs.list_jobs(db, user)

    @app.put("/api/agent/jobs/{job_id}")
    async def put_agent_job(job_id: int, body: JobIn, user=Depends(get_current_user)):
        try:
            return jobs.update_job(db, user, job_id, body.enabled, body.cron, body.timezone)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/agent/jobs/{job_id}/run")
    async def run_agent_job(job_id: int, user=Depends(get_current_user)):
        row = db.query_one("SELECT id FROM agent_jobs WHERE id=? AND owner=?",
                           (job_id, user["username"]))
        if not row:
            raise HTTPException(404, "任务不存在")
        try:
            return await jobs.run_job(db, job_id, manual=True)
        except Exception as e:
            raise HTTPException(400, str(e)[:200])

    @app.get("/api/agent/cases")
    async def agent_cases(user=Depends(get_current_user)):
        return library.list_cases(db, user)

    @app.post("/api/agent/cases")
    async def create_agent_case(body: CaseIn, user=Depends(get_current_user)):
        return library.save_case(db, user, body.model_dump())

    @app.put("/api/agent/cases/{case_id}")
    async def put_agent_case(case_id: int, body: CaseIn, user=Depends(get_current_user)):
        try:
            return library.save_case(db, user, body.model_dump(), case_id)
        except ValueError as e:
            raise HTTPException(404, str(e))

    @app.delete("/api/agent/cases/{case_id}")
    async def delete_agent_case(case_id: int, user=Depends(get_current_user)):
        try:
            library.delete_case(db, user, case_id)
        except ValueError as e:
            raise HTTPException(404, str(e))
        return {"ok": True}

    @app.post("/api/agent/cases/{case_id}/split")
    async def split_agent_case(case_id: int, user=Depends(get_current_user)):
        try:
            return library.split_case(db, user, case_id)
        except ValueError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/agent/cases/merge")
    async def merge_agent_cases(body: MergeIn, user=Depends(get_current_user)):
        if len(body.ids) != 2:
            raise HTTPException(400, "请选择两个案例")
        try:
            return library.merge_cases(db, user, body.ids[0], body.ids[1])
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.get("/api/agent/profile")
    async def agent_profile(user=Depends(get_current_user)):
        return library.get_profile(db, user)

    @app.put("/api/agent/profile")
    async def put_agent_profile(body: ProfileIn, user=Depends(get_current_user)):
        return library.save_profile(db, user, body.prompt, body.skills)

    @app.get("/api/agent/skills")
    async def agent_skills(user=Depends(get_current_user)):
        return store.list_skills(db)

    @app.websocket("/api/agent/ws")
    async def agent_ws(ws: WebSocket):
        await ws.accept()
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=10)
        except (asyncio.TimeoutError, WebSocketDisconnect):
            await ws.close()
            return
        try:
            first = json.loads(raw)
        except json.JSONDecodeError:
            await ws.send_json({"event": "error", "message": "消息格式错误"})
            await ws.close()
            return
        token = (first.get("auth") or "").strip() or ws.cookies.get("wb_session", "")
        user = resolve_token(token)
        if not user:
            await ws.send_json({"event": "error", "message": "未登录或会话已过期,请重新登录"})
            await ws.close()
            return
        await ws.send_json({"event": "ready"})
        if first.get("session_id") and first.get("content"):
            await handle_turn(ws, user, first, deps)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send_json({"event": "error", "message": "消息格式错误"})
                    continue
                if "content" not in payload and "session_id" not in payload:
                    continue
                await handle_turn(ws, user, payload, deps)
        except WebSocketDisconnect:
            return
