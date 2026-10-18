# tests/test_sessions.py
import json
import os

from bridge.sessions import PiSessionManager


def test_resume_path_from_index(tmp_path):
    m = PiSessionManager(str(tmp_path))
    sess = tmp_path / "pi-sessions"
    f = sess / "2026-10-17T00-00-00-000Z_uuid.jsonl"
    f.write_text("{}")
    m._save_index({"convA": str(f)})
    assert m._resume_path("convA") == str(f)


def test_resume_path_missing_file_or_entry(tmp_path):
    m = PiSessionManager(str(tmp_path))
    assert m._resume_path("nope") is None
    m._save_index({"convA": str(tmp_path / "pi-sessions" / "gone.jsonl")})
    assert m._resume_path("convA") is None


def test_zhipu_env_reads_config(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"api_key": "sk-test", "model": "glm-4.7"}), encoding="utf-8")
    m = PiSessionManager(str(tmp_path))
    assert m._pi_env("c").get("ZHIPU_API_KEY") == "sk-test"


def test_zhipu_env_empty_without_config(tmp_path):
    m = PiSessionManager(str(tmp_path))
    assert "ZHIPU_API_KEY" not in m._pi_env("c")


def test_spawn_args():
    m = PiSessionManager(".")
    args = m._spawn_args("conv1")
    assert args[:2] == ["--mode", "rpc"]
    assert "--session-dir" in args
    # 不传 provider/model 时 Pi 连默认 Anthropic（403）
    i = args.index("--provider")
    assert args[i + 1] == "zhipu"
    j = args.index("--model")
    assert args[j + 1] == "glm-4.7"
    k = args.index("-n")
    assert args[k + 1] == "conv1"


class _FakeClient:
    def __init__(self):
        import types
        from bridge.pi_process import PiProcess
        self.process = PiProcess([], cwd=".")
        # 未 start() 的 PiProcess.alive 为 False；注入假 proc 使其存活，
        # 从而覆盖 push_plan 的存活推送路径（不真正启动子进程）。
        self.process.proc = types.SimpleNamespace(returncode=None)
        self.stream_events = __import__("asyncio").Queue()


async def test_push_plan_stores_and_emits():
    import asyncio
    from bridge.sessions import PiSessionManager
    m = PiSessionManager(".")
    m._clients["c1"] = _FakeClient()
    await m.push_plan("c1", [{"title": "步骤一", "status": "done"}])
    assert m._plans["c1"] == [{"title": "步骤一", "status": "done"}]
    ev = await asyncio.wait_for(m._clients["c1"].stream_events.get(), timeout=1)
    assert ev == {"type": "_bridge_plan", "steps": [{"title": "步骤一", "status": "done"}]}


async def test_push_plan_without_client_just_stores():
    from bridge.sessions import PiSessionManager
    m = PiSessionManager(".")
    await m.push_plan("ghost", [{"title": "x", "status": "pending"}])  # 不抛异常
    assert m._plans["ghost"] == [{"title": "x", "status": "pending"}]


def test_user_for_and_pi_env():
    import os
    from bridge.sessions import PiSessionManager
    m = PiSessionManager(".")
    assert m.user_for("nobody") is None
    m._users["c1"] = {"username": "u", "role": "advisor"}
    assert m.user_for("c1") == {"username": "u", "role": "advisor"}
    m.bridge_token = "tok"
    m.bridge_url = "http://127.0.0.1:8787"
    (tmp := m._pi_env("c1"))
    assert tmp["PI_CONV"] == "c1"
    assert tmp["PI_BRIDGE_TOKEN"] == "tok"
    assert tmp["PI_BRIDGE_URL"] == "http://127.0.0.1:8787"


def test_index_upgrade_and_list(tmp_path):
    import json as _json
    from bridge.sessions import PiSessionManager
    m = PiSessionManager(str(tmp_path))
    old = tmp_path / "pi-sessions" / "s1.jsonl"
    old.write_text("{}")
    m._save_index({"old-conv": str(old),                     # 旧格式：纯字符串
                   "new-conv": {"path": str(old), "title": "复现X", "updated": "2026-10-18T01:00:00"}})
    convs = m.list_convs()
    ids = {c["id"] for c in convs}
    assert ids == {"old-conv", "new-conv"}
    by_id = {c["id"]: c for c in convs}
    assert by_id["old-conv"]["title"] == "old-conv"          # 旧值回退
    assert by_id["new-conv"]["title"] == "复现X"


def test_list_convs_sorted_by_updated(tmp_path):
    from bridge.sessions import PiSessionManager
    m = PiSessionManager(str(tmp_path))
    m._save_index({"a": {"path": "x", "title": "a", "updated": "1"},
                   "b": {"path": "x", "title": "b", "updated": "2"}})
    assert [c["id"] for c in m.list_convs()] == ["b", "a"]


def test_delete_conv_removes_everything(tmp_path):
    import asyncio
    from bridge.sessions import PiSessionManager
    m = PiSessionManager(str(tmp_path))
    f = tmp_path / "pi-sessions" / "c1.jsonl"
    f.write_text("{}")
    m._save_index({"c1": {"path": str(f), "title": "t", "updated": "1"}})
    assert m.delete_conv("c1") is True
    assert not f.exists()
    assert "c1" not in m._load_index()
    assert m.delete_conv("ghost") is False


def test_maybe_set_title(tmp_path):
    from bridge.sessions import PiSessionManager
    m = PiSessionManager(str(tmp_path))
    f = tmp_path / "pi-sessions" / "c1.jsonl"
    f.write_text("{}")
    m._save_index({"c1": {"path": str(f), "title": "c1", "updated": "1"}})
    m.maybe_set_title("c1", "复现 PmcmFL 论文的第一句话")
    assert m._load_index()["c1"]["title"] == "复现 PmcmFL 论文的第一句话"[:24]
    m.maybe_set_title("c1", "第二条消息不该改标题")
    assert m._load_index()["c1"]["title"] == "复现 PmcmFL 论文的第一句话"[:24]
