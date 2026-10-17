"""直接 spawn Pi RPC，打印原始事件（调试事件形状）。"""
import asyncio
import json
import os
import sys

sys.path.insert(0, ".")
from bridge.pi_client import PiClient
from bridge.pi_process import PiProcess


async def main():
    env = {"ZHIPU_API_KEY": json.load(open("config.json"))["api_key"]}
    proc = PiProcess(["--mode", "rpc", "--no-session",
                      "--provider", "zhipu", "--model", "glm-4.7"],
                     cwd=".", env=env)
    await proc.start()
    client = PiClient(proc)
    await client.prompt("请只回复两个字：成功")
    while True:
        ev = await asyncio.wait_for(client.stream_events.get(), timeout=120)
        print(json.dumps(ev, ensure_ascii=False)[:400])
        if ev.get("type") in ("agent_settled", "_process_exit"):
            break
    await client.close()


asyncio.run(main())
