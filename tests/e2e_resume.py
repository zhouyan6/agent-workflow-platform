"""E2E 会话恢复验收：两个连接 + 一次服务重启都记住上下文。"""
import asyncio
import json
import sys

import websockets

BASE = "ws://127.0.0.1:8787/api/pi/ws?conv=resume-test"


async def round_(token: str, text: str) -> str:
    async with websockets.connect(BASE, additional_headers={
            "Cookie": f"wb_session={token}"}) as ws:
        await ws.send(json.dumps({"type": "message", "text": text}))
        got = ""
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=120))
            if m["type"] == "token":
                got += m["delta"]
            elif m["type"] == "turn_done":
                return got
            elif m["type"] == "error":
                raise SystemExit(f"error: {m['message']}")


async def main(token: str, second: bool):
    if not second:
        r = await round_(token, "记住：我的暗号是西瓜。请只回复：收到")
        print("turn1:", r[:50])
    else:
        r = await round_(token, "我的暗号是什么？请只回复暗号本身")
        print("turn2:", r[:80])
        print("RESUME OK" if "西瓜" in r else "RESUME FAIL")


asyncio.run(main(sys.argv[1], sys.argv[2] == "second"))
