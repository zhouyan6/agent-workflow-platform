# bridge/pi_process.py
"""Pi RPC 子进程管理。"""
import json
import logging
import shutil

log = logging.getLogger(__name__)

# Windows 上 npm 全局命令是 pi.cmd，CreateProcess 需要 .cmd 后缀的完整路径
PI_BIN = shutil.which("pi") or "pi"


def parse_line(line: str) -> dict | None:
    """解析一行 JSONL。空行/非 JSON 返回 None（记警告），绝不抛异常。"""
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        log.warning("pi rpc: 丢弃非 JSON 行: %.200s", line)
        return None
