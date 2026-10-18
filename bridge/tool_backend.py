# bridge/tool_backend.py
"""localhost-only 工具后端：Pi 扩展的薄壳工具经此调用 agent.tools 业务逻辑。"""
import asyncio
import json
import os

from agent import tools as agent_tools
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

router = APIRouter()
_manager = None  # 由 mount_tool_backend 注入

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = None   # 由 mount 时或测试注入（Runs(ROOT)）
CCF_PATH = os.path.join(ROOT, "ccf.json")


def mount_tool_backend(app, manager) -> None:
    global _manager, RUNS
    _manager = manager
    if RUNS is None:
        from .runs import Runs
        RUNS = Runs(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    app.include_router(router)


def _check(token: str) -> None:
    if not _manager or not _manager.bridge_token or token != _manager.bridge_token:
        raise HTTPException(403, "bridge token 无效")


class ToolCall(BaseModel):
    args: dict = {}
    conv: str = "default"


@router.get("/api/pi/tools/schema")
def tools_schema(x_bridge_token: str = Header(default="")):
    _check(x_bridge_token)
    return {"tools": agent_tools.openai_tools()}


@router.post("/api/pi/tools/{name}")
async def call_tool(name: str, call: ToolCall,
                    x_bridge_token: str = Header(default="")):
    _check(x_bridge_token)
    user = _manager.user_for(call.conv)
    if user is None:
        raise HTTPException(403, f"会话 {call.conv} 未连接或已过期")
    if name == "update_plan":
        steps = call.args.get("steps") or []
        await _manager.push_plan(call.conv, steps)
        return {"result": json.dumps({"ok": True}, ensure_ascii=False)}
    if name in ("run_create", "run_update", "ccf_lookup"):
        return await _bridge_tool(name, call)
    if _manager.ctx_factory is None:
        raise HTTPException(503, "ctx_factory 未配置（server 未接入）")
    ctx = _manager.ctx_factory(user)
    result = await asyncio.to_thread(agent_tools.dispatch, name, call.args, ctx)
    if name == "submit_task":
        try:
            payload = json.loads(result)
            task = payload.get("task") or {}
            if payload.get("ok") and task.get("id"):
                _manager.watch_task(call.conv, task["id"])
        except (ValueError, AttributeError):
            pass
    return {"result": result}


def _ccf_lookup(query: str) -> str:
    try:
        with open(CCF_PATH, encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, ValueError) as e:
        return json.dumps({"ok": False, "error": f"ccf.json 读取失败: {e}"},
                          ensure_ascii=False)
    q = (query or "").lower()
    hits = [it for it in items if q and any(
        q in str(it.get(k, "")).lower() for k in ("r", "t", "a", "s", "f"))][:10]
    if not hits:
        return json.dumps({"ok": True, "count": 0,
                           "hint": "无匹配；query 可用期刊/会议名、缩写、领域或等级A/B/C"},
                          ensure_ascii=False)
    lines = [f"[{it.get('r')}] {it.get('f')} ({it.get('s')}) {it.get('t')}·{it.get('a')}"
             for it in hits]
    return json.dumps({"ok": True, "count": len(hits), "items": lines},
                      ensure_ascii=False)


async def _bridge_tool(name: str, call: ToolCall) -> dict:
    if name == "ccf_lookup":
        return {"result": _ccf_lookup(call.args.get("query") or "")}
    run = None
    if name == "run_create":
        run = RUNS.create(call.conv, call.args.get("goal") or "",
                          call.args.get("stages") or [])
    elif name == "run_update":
        run = RUNS.update(call.args.get("run_id") or "",
                          call.args.get("stages") or [])
        if run is None:
            return {"result": json.dumps(
                {"ok": False, "error": "run_id 不存在"}, ensure_ascii=False)}
    await _manager.push_run(call.conv, run)
    return {"result": json.dumps({"ok": True, "run": run}, ensure_ascii=False,
                                 default=str)}
