# bridge/routes.py
"""Pi 桥的 WebSocket 端点。"""
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import events as ev

router = APIRouter()
manager = None          # 由 mount_pi 注入（PiSessionManager）
_resolve_token = None   # 由 mount_pi 注入（server.resolve_token）


def mount_pi(app, root, resolve_token_fn) -> None:
    """挂载 /api/pi/ws。延迟 import 避免与 server.py 循环依赖。"""
    global manager, _resolve_token
    from .sessions import PiSessionManager
    manager = PiSessionManager(root)
    _resolve_token = resolve_token_fn
    app.include_router(router)


async def _pump(client, ws) -> None:
    while True:
        obj = await client.stream_events.get()
        for msg in ev.translate_event(obj):
            await ws.send_text(json.dumps(msg, ensure_ascii=False))


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
    client = await manager.get(conv)
    pump = asyncio.create_task(_pump(client, ws))
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            mtype = msg.get("type")
            if mtype == "message":
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
