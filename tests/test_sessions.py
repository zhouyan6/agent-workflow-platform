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
    assert m._zhipu_env() == {"ZHIPU_API_KEY": "sk-test"}


def test_zhipu_env_empty_without_config(tmp_path):
    m = PiSessionManager(str(tmp_path))
    assert m._zhipu_env() == {}


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
