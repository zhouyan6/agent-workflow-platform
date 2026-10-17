# tests/test_pi_process.py
import asyncio

from bridge.pi_process import PiProcess, parse_line  # parse_line 已由 Task 2 覆盖


def test_alive_false_before_start():
    p = PiProcess(["--mode", "rpc"], cwd=".")
    assert p.alive is False


def test_parse_line_reexported():
    assert parse_line('{"ok": 1}') == {"ok": 1}
