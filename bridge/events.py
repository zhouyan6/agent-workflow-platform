# bridge/events.py
"""Pi RPC 事件 → 网页 WebSocket 消息。"""


def _dialog(ev: dict) -> dict:
    req = ev.get("request") if isinstance(ev.get("request"), dict) else ev
    return {
        "title": req.get("title") or "确认操作",
        "message": req.get("message") or req.get("prompt") or "",
    }


def translate_event(ev: dict) -> list[dict]:
    t = ev.get("type")
    if t == "message_update":
        ame = ev.get("assistantMessageEvent") or {}
        k = ame.get("type")
        if k == "text_delta":
            return [{"type": "token", "delta": ame.get("delta") or ""}]
        if k == "thinking_delta":
            return [{"type": "thinking", "delta": ame.get("delta") or ""}]
        return []
    if t == "agent_start":
        return [{"type": "turn_start"}]
    if t == "agent_settled":
        return [{"type": "turn_done"}]
    if t == "tool_execution_start":
        return [{"type": "tool_start", "toolCallId": ev.get("toolCallId"),
                 "tool": ev.get("toolName")}]
    if t == "tool_execution_end":
        return [{"type": "tool_end", "toolCallId": ev.get("toolCallId")}]
    if t == "extension_ui_request":
        return [{"type": "confirm_request", "id": ev.get("id"),
                 "request": _dialog(ev)}]
    if t == "_bridge_plan":
        return [{"type": "plan_update", "steps": ev.get("steps") or []}]
    if t == "_bridge_run":
        return [{"type": "run_update", "run": ev.get("run") or {}}]
    if t == "_process_exit":
        return [{"type": "error", "message": "Pi 引擎进程退出"}]
    return []
