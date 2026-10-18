"""E2E：history 回放 + run_update 推送。用法同 e2e_tools.py。"""
import asyncio
import json
import sys

import websockets


async def turn(ws, text):
    """跑一个回合，返回 (回复文本, 是否收到 run_update, run)。"""
    await ws.send(json.dumps({"type": "message", "text": text}))
    got, run_ok, run = "", False, None
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout=180))
        t = m["type"]
        if t == "token":
            got += m["delta"]
        elif t == "run_update":
            run_ok, run = True, m["run"]
        elif t == "error":
            raise SystemExit("error: " + m["message"])
        elif t == "turn_done":
            return got, run_ok, run


async def main(token: str) -> None:
    uri = "ws://127.0.0.1:8787/api/pi/ws?conv=ui-e2e"
    async with websockets.connect(uri, additional_headers={
            "Cookie": f"wb_session={token}"}) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
        assert first["type"] == "history", f"first={first['type']}"
        print("HISTORY OK")
        for attempt in range(3):
            got, ok, run = await turn(
                ws, "请立即调用 run_create 工具（不要只用文字回答）："
                    "goal=测试，stages=[{title:步骤一}]。工具调用成功后只回复：完成。")
            if ok:
                assert run["stages"][0]["title"] == "步骤一", run
                print("RUN UPDATE OK")
                return
            print(f"attempt {attempt + 1}: no run_update, reply: {got[:80]}")
        raise SystemExit("模型三次未调用 run_create")


asyncio.run(main(sys.argv[1]))
