# tests/test_events.py
from bridge.events import translate_event


def test_text_delta_to_token():
    ev = {"type": "message_update",
          "assistantMessageEvent": {"type": "text_delta", "delta": "你好"}}
    assert translate_event(ev) == [{"type": "token", "delta": "你好"}]


def test_thinking_delta():
    ev = {"type": "message_update",
          "assistantMessageEvent": {"type": "thinking_delta", "delta": "想想"}}
    assert translate_event(ev) == [{"type": "thinking", "delta": "想想"}]


def test_other_message_update_ignored():
    ev = {"type": "message_update",
          "assistantMessageEvent": {"type": "text_start", "contentIndex": 0}}
    assert translate_event(ev) == []


def test_lifecycle():
    assert translate_event({"type": "agent_start"}) == [{"type": "turn_start"}]
    assert translate_event({"type": "agent_settled"}) == [{"type": "turn_done"}]


def test_tool_events():
    assert translate_event({"type": "tool_execution_start", "toolCallId": "t1",
                            "toolName": "read"}) == [
        {"type": "tool_start", "toolCallId": "t1", "tool": "read"}]
    assert translate_event({"type": "tool_execution_end", "toolCallId": "t1"}) == [
        {"type": "tool_end", "toolCallId": "t1"}]


def test_confirm_request_passthrough_with_fallback_fields():
    ev = {"type": "extension_ui_request", "id": "d9", "title": "确认",
          "message": "提交训练？"}
    out = translate_event(ev)
    assert out[0]["type"] == "confirm_request"
    assert out[0]["id"] == "d9"
    assert out[0]["request"]["title"] == "确认"
    assert out[0]["request"]["message"] == "提交训练？"


def test_confirm_request_missing_fields_get_defaults():
    ev = {"type": "extension_ui_request", "id": "d2"}
    out = translate_event(ev)[0]
    assert out["request"]["title"] == "确认操作"
    assert out["request"]["message"] == ""


def test_process_exit_to_error():
    out = translate_event({"type": "_process_exit", "code": 1})
    assert out == [{"type": "error", "message": "Pi 引擎进程退出"}]


def test_unknown_event_empty():
    assert translate_event({"type": "queue_update"}) == []


def test_bridge_plan_to_plan_update():
    ev = {"type": "_bridge_plan", "steps": [{"title": "s", "status": "done"}]}
    assert translate_event(ev) == [
        {"type": "plan_update", "steps": [{"title": "s", "status": "done"}]}]


def test_bridge_run_to_run_update():
    run = {"id": "r1", "conv": "c", "goal": "g",
           "stages": [{"title": "s", "status": "pending"}], "updated": "x"}
    assert translate_event({"type": "_bridge_run", "run": run}) == [
        {"type": "run_update", "run": run}]


def test_bridge_event_to_agent_event():
    assert translate_event({"type": "_bridge_event", "text": "任务结束"}) == [
        {"type": "agent_event", "text": "任务结束"}]
