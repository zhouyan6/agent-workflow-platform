# bridge/tool_backend.py
"""localhost-only 工具后端：Pi 扩展的薄壳工具经此调用 agent.tools 业务逻辑。"""
import asyncio
import json

from agent import tools as agent_tools
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

router = APIRouter()
_manager = None  # 由 mount_tool_backend 注入


def mount_tool_backend(app, manager) -> None:
    global _manager
    _manager = manager
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
    if _manager.ctx_factory is None:
        raise HTTPException(503, "ctx_factory 未配置（server 未接入）")
    ctx = _manager.ctx_factory(user)
    result = await asyncio.to_thread(agent_tools.dispatch, name, call.args, ctx)
    return {"result": result}
