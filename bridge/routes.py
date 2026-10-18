# bridge/routes.py
"""Pi 桥的 WebSocket 端点。"""
import asyncio
import json
import logging
import os
import secrets

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from . import events as ev

log = logging.getLogger(__name__)
router = APIRouter()
manager = None          # 由 mount_pi 注入（PiSessionManager）
_resolve_token = None   # 由 mount_pi 注入（server.resolve_token）


def mount_pi(app, root, resolve_token_fn, ctx_factory=None) -> None:
    """挂载 /api/pi/ws 与工具后端。延迟 import 避免与 server.py 循环依赖。"""
    global manager, _resolve_token
    from .sessions import PiSessionManager
    from .tool_backend import mount_tool_backend
    manager = PiSessionManager(root)
    manager.bridge_token = secrets.token_urlsafe(24)
    manager.bridge_url = f"http://127.0.0.1:{os.environ.get('WORKBENCH_PORT', '8787')}"
    manager.ctx_factory = ctx_factory
    _resolve_token = resolve_token_fn
    app.include_router(router)
    mount_tool_backend(app, manager)

    from .watcher import TaskWatcher
    watcher = TaskWatcher(os.path.join(root, "workbench.db"), manager)

    async def _start_watcher():
        watcher.start()

    async def _stop_watcher():
        watcher.stop()

    app.on_event("startup")(_start_watcher)
    app.on_event("shutdown")(_stop_watcher)


def _history_entries(messages: list) -> list[dict]:
    out = []
    for msg in messages or []:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = "".join(
            b.get("text") or "" for b in (msg.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text")
        if text.strip():
            out.append({"role": role, "text": text})
    return out


async def _pump(client, ws) -> None:
    while True:
        obj = await client.stream_events.get()
        for msg in ev.translate_event(obj):
            await ws.send_text(json.dumps(msg, ensure_ascii=False))


def _ws_user_cookie(token: str):
    user = _resolve_token(token) if _resolve_token else None
    if not user:
        raise HTTPException(401, "未登录")
    return user


@router.get("/api/pi/convs")
def list_convs(request: Request, wb_session: str = ""):
    # 查询参数 wb_session 与 cookie 两种方式都支持
    _ws_user_cookie(wb_session or request.cookies.get("wb_session", ""))
    return {"convs": manager.list_convs()}


@router.delete("/api/pi/convs/{conv_id}")
async def delete_conv(conv_id: str, request: Request, wb_session: str = ""):
    _ws_user_cookie(wb_session or request.cookies.get("wb_session", ""))
    await manager.aclose_client(conv_id)
    return {"ok": manager.delete_conv(conv_id)}


@router.get("/api/pi/runs")
def get_runs(request: Request, conv: str = "", wb_session: str = ""):
    # 查询参数 wb_session 与 cookie 两种方式都支持（同 /api/pi/convs）
    _ws_user_cookie(wb_session or request.cookies.get("wb_session", ""))
    from .runs import Runs
    runs = Runs(manager.root)
    return {"runs": runs.for_conv(conv) if conv else []}


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
    client = await manager.get(conv, user)
    try:
        data = await client.command({"type": "get_messages"})
        payload = data.get("data") or {}
        # 实测 Pi 1.3.0 返回 {"messages": [...]}，兼容裸数组
        messages = (payload.get("messages") if isinstance(payload, dict)
                    else payload) or []
        entries = _history_entries(messages)
        await ws.send_text(json.dumps(
            {"type": "history", "entries": entries}, ensure_ascii=False))
    except Exception:
        pass  # 历史回放失败不阻塞新对话
    pump = asyncio.create_task(_pump(client, ws))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            mtype = msg.get("type")
            if mtype == "message":
                try:
                    manager.maybe_set_title(conv, msg.get("text", ""))
                except Exception:
                    log.warning("set title failed", exc_info=True)
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
