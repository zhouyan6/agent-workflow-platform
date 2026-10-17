# tests/test_parse_line.py
import json

from bridge.pi_process import parse_line


def test_basic_line():
    assert parse_line('{"type": "response"}\n') == {"type": "response"}


def test_crlf_stripped():
    assert parse_line('{"a": 1}\r\n') == {"a": 1}


def test_blank_and_whitespace_are_none():
    assert parse_line("") is None
    assert parse_line("   \n") is None


def test_garbage_is_none_not_raise():
    assert parse_line("not json at all") is None


def test_unicode_sep_inside_string_is_kept():
    # U+2028/U+2029 是合法 JSON 字符串内容，绝不能当行分隔符
    payload = json.dumps({"x": "ab"})
    assert parse_line(payload) == {"x": "ab"}
