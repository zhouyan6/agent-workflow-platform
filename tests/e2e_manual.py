"""E2E 手动验收脚本：直连 /api/pi/ws，走真实 Pi + GLM。

用法：.venv-win/Scripts/python tests/e2e_manual.py <token>
"""
import asyncio
import json
import sys

import websockets


async def main(token: str) -> None:
    uri = "ws://127.0.0.1:8787/api/pi/ws?conv=e2e-test"
    async with websockets.connect(uri, additional_headers={
            "Cookie": f"wb_session={token}"}) as ws:
        print("connected")
        await ws.send(json.dumps({"type": "message",
                                  "text": "请只回复两个字：成功"}))
        got_text = ""
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout=120))
            t = m["type"]
            if t == "token":
                got_text += m["delta"]
            elif t in ("turn_start", "tool_start", "tool_end"):
                print("event:", t)
            elif t == "turn_done":
                print("reply:", got_text)
                print("E2E OK" if "成功" in got_text else "E2E REPLY UNEXPECTED")
                break
            elif t == "error":
                print("ERROR:", m["message"])
                sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
