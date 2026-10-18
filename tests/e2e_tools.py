"""E2E：工具调用 + 确认门禁全链路（真实 Pi + GLM + 桥 + agent.tools）。

前置：server 已在本地 8787 运行，扩展已安装。
用法：.venv-win/Scripts/python -u tests/e2e_tools.py <token>
"""
import asyncio
import json
import sys

import websockets

BASE = "ws://127.0.0.1:8787/api/pi/ws?conv=tools-e2e"


async def turn(ws, text, auto_confirm=False):
    await ws.send(json.dumps({"type": "message", "text": text}))
    got, tools = "", []
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=180))
        t = m["type"]
        if t == "token":
            got += m["delta"]
        elif t == "tool_start":
            tools.append(m.get("tool"))
        elif t == "confirm_request" and auto_confirm:
            await ws.send(json.dumps(
                {"type": "confirm", "id": m["id"], "confirmed": True}))
            auto_confirm = False
        elif t == "error":
            raise SystemExit(f"error: {m['message']}")
        elif t == "turn_done":
            return got, tools


async def main(token: str) -> None:
    async with websockets.connect(BASE, additional_headers={
            "Cookie": f"wb_session={token}"}) as ws:
        got, tools = await turn(
            ws, "请调用 gpu_status 工具查看显卡，然后用一句话总结空闲情况。")
        print("turn1 tools:", tools, "| reply:", got[:80])
        assert "gpu_status" in tools, "GLM 未调用 gpu_status"
        print("TOOL E2E OK")

        got, tools = await turn(
            ws, "请提交一个测试任务：名字 t-echo，命令 echo hello，需要 1 张 GPU。",
            auto_confirm=True)
        print("turn2 tools:", tools, "| reply:", got[:80])
        assert "submit_task" in tools, "GLM 未调用 submit_task"
        print("CONFIRM-GATE E2E OK")


asyncio.run(main(sys.argv[1]))
