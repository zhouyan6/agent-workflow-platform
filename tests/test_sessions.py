# tests/test_sessions.py
import json
import os
import time

from bridge.sessions import PiSessionManager


def test_find_session_file_picks_newest_match(tmp_path):
    old = tmp_path / "convA-2026-10-01.jsonl"
    new = tmp_path / "convA-2026-10-16.jsonl"
    other = tmp_path / "convB-2026-10-16.jsonl"
    for f in (old, new, other):
        f.write_text("{}")
    back = time.time() - 10000
    os.utime(old, (back, back))
    assert PiSessionManager.find_session_file(str(tmp_path), "convA") == str(new)


def test_find_session_file_none(tmp_path):
    assert PiSessionManager.find_session_file(str(tmp_path), "nope") is None
    assert PiSessionManager.find_session_file(str(tmp_path / "missing"), "x") is None


def test_zhipu_env_reads_config(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"api_key": "sk-test", "model": "glm-4.7"}), encoding="utf-8")
    m = PiSessionManager(str(tmp_path))
    assert m._zhipu_env() == {"ZHIPU_API_KEY": "sk-test"}


def test_zhipu_env_empty_without_config(tmp_path):
    m = PiSessionManager(str(tmp_path))
    assert m._zhipu_env() == {}


def test_spawn_args():
    m = PiSessionManager(".")
    args = m._spawn_args("conv1")
    assert args[:2] == ["--mode", "rpc"]
    assert "--session-dir" in args
    i = args.index("-n")
    assert args[i + 1] == "conv1"
