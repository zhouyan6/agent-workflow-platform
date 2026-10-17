"""Tool-calling loop. Streams answer tokens and executes tools."""
import asyncio
import json

import httpx

import chat
from agent import tools

MAX_ROUNDS = 8


def new_stream_state():
    return {"content": [], "calls": {}}


def ingest_choice(state, choice):
    """Fold one streaming delta, or a non-stream message, into state.

    Returns a text fragment to show the user, or None.
    """
    delta = choice.get("delta")
    if delta is None and choice.get("message") is not None:
        msg = choice.get("message") or {}
        delta = {
            "content": msg.get("content") or "",
            "tool_calls": msg.get("tool_calls") or [],
        }
    delta = delta or {}
    token = None
    if delta.get("content"):
        state["content"].append(delta["content"])
        token = delta["content"]
    for tc in delta.get("tool_calls") or []:
        idx = tc.get("index", 0)
        slot = state["calls"].setdefault(idx, {
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        })
        if tc.get("id"):
            slot["id"] = tc["id"]
        if tc.get("type"):
            slot["type"] = tc["type"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] += fn["name"]
        if fn.get("arguments"):
            slot["function"]["arguments"] += fn["arguments"]
    return token


def finalize_state(state):
    calls = []
    for idx in sorted(state["calls"]):
        slot = state["calls"][idx]
        if not slot["function"]["name"]:
            continue
        if not slot["id"]:
            slot["id"] = f"call_{idx}"
        calls.append(slot)
    return "".join(state["content"]), calls


def consume_sse_line(state, line):
    """Parse one SSE line. Returns a text token, 'done', or None."""
    line = (line or "").strip()
    if not line or line.startswith(":"):
        return None
    if not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if data == "[DONE]":
        return "done"
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return None
    if obj.get("error"):
        err = obj["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise chat.ChatError(str(msg)[:300])
    choices = obj.get("choices") or []
    if not choices:
        return None
    return ingest_choice(state, choices[0])


def _parse_args(raw):
    raw = (raw or "").strip() or "{}"
    try:
        args = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return args if isinstance(args, dict) else {}


async def complete_stream(messages, tool_schema, on_token):
    """Call the configured chat model. Returns (content, tool_calls)."""
    cfg = chat.load_config()
    if not cfg:
        raise chat.ChatError("尚未配置模型，请先在设置里保存 API Token")
    payload = {
        "model": cfg.get("model", "glm-4.7"),
        "messages": messages,
        "stream": True,
        "temperature": 0.2,
    }
    if tool_schema:
        payload["tools"] = tool_schema
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    state = new_stream_state()
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")[:500]
                    raise chat.ChatError(f"模型接口返回 {resp.status_code}: {body}")
                ctype = (resp.headers.get("content-type") or "").lower()
                if "application/json" in ctype and "text/event-stream" not in ctype:
                    obj = json.loads((await resp.aread()).decode("utf-8", "replace"))
                    if obj.get("error"):
                        err = obj["error"]
                        msg = err.get("message") if isinstance(err, dict) else str(err)
                        raise chat.ChatError(str(msg)[:300])
                    choice = (obj.get("choices") or [{}])[0]
                    token = ingest_choice(state, choice)
                    if token:
                        await on_token(token)
                    return finalize_state(state)
                async for line in resp.aiter_lines():
                    token = consume_sse_line(state, line)
                    if token == "done":
                        break
                    if token:
                        await on_token(token)
    except chat.ChatError:
        raise
    except httpx.TimeoutException:
        raise chat.ChatError("模型响应超时")
    except httpx.HTTPError as e:
        raise chat.ChatError(f"模型接口调用失败: {e}")
    return finalize_state(state)


async def run_turn(history, ctx, emit, sink, confirm=None):
    """Run up to MAX_ROUNDS tool calls. Append new messages to sink.

    confirm(confirm_id) -> bool is required before stop/submit/save.
    """
    messages = list(history)
    schema = tools.openai_tools(getattr(ctx, "enabled_skills", None))
    for _ in range(MAX_ROUNDS):
        content, calls = await complete_stream(
            messages, schema, lambda text: emit("token", text=text))
        if calls:
            assistant = {
                "role": "assistant",
                "content": content or None,
                "tool_calls": calls,
            }
            messages.append(assistant)
            sink.append({
                "role": "assistant",
                "content": content or "",
                "tool_calls": calls,
            })
            for tc in calls:
                name = tc["function"]["name"]
                args = _parse_args(tc["function"].get("arguments"))
                if name in tools.CONFIRM_TOOLS:
                    await emit("confirm", confirm_id=tc["id"], name=name,
                               summary=tools.confirm_summary(name, args)[:1200])
                    approved = await confirm(tc["id"]) if confirm else False
                    if not approved:
                        result = tools._err("用户取消了该操作")
                    else:
                        result = await asyncio.to_thread(tools.dispatch, name, args, ctx)
                else:
                    await emit("tool_start", name=name, call_id=tc["id"])
                    result = await asyncio.to_thread(tools.dispatch, name, args, ctx)
                ok = True
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict) and parsed.get("ok") is False:
                        ok = False
                except json.JSONDecodeError:
                    pass
                await emit("tool_end", name=name, call_id=tc["id"],
                           preview=result[:400], ok=ok)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
                sink.append({
                    "role": "tool",
                    "content": result,
                    "tool_name": name,
                    "tool_call_id": tc["id"],
                })
            continue
        sink.append({"role": "assistant", "content": content or "（模型没有返回内容）"})
        if not content:
            await emit("token", text="（模型没有返回内容）")
        return
    raise chat.ChatError("工具调用超过 8 轮，已停止")
