"""Nightly review jobs. The schedule stays off until someone enables it."""
import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import chat
from agent import artifacts, store, tools
from db import now

DEFAULT_CRON = "0 22 * * *"
DEFAULT_TZ = "Asia/Shanghai"
REVIEW_TITLE = "定时复盘"
_locks = {}


def _field_ok(expr, value):
    expr = (expr or "").strip()
    if expr == "*":
        return True
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("*/"):
            try:
                step = int(part[2:])
            except ValueError:
                continue
            if step > 0 and value % step == 0:
                return True
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                if int(a) <= value <= int(b):
                    return True
            except ValueError:
                continue
            continue
        if part.isdigit() and int(part) == value:
            return True
    return False


def cron_matches(expr, dt):
    parts = (expr or "").split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    dow = dow.replace("7", "0")
    cron_dow = (dt.weekday() + 1) % 7
    return (_field_ok(minute, dt.minute) and _field_ok(hour, dt.hour)
            and _field_ok(dom, dt.day) and _field_ok(month, dt.month)
            and _field_ok(dow, cron_dow))


def resolve_tz(name):
    name = (name or DEFAULT_TZ).strip() or DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except Exception:
        if name in ("Asia/Shanghai", "Asia/Chongqing", "PRC"):
            return timezone(timedelta(hours=8))
        raise ValueError("时区无效")


def local_now(timezone_name):
    return datetime.now(resolve_tz(timezone_name))


def is_due(job, moment=None):
    if not job.get("enabled"):
        return False
    moment = moment or local_now(job.get("timezone"))
    if not cron_matches(job.get("cron") or "", moment):
        return False
    last = job.get("last_run_at")
    if not last:
        return True
    prev = datetime.fromtimestamp(last, moment.tzinfo)
    return (prev.year, prev.month, prev.day, prev.hour, prev.minute) != (
        moment.year, moment.month, moment.day, moment.hour, moment.minute)


def validate_cron(expr):
    parts = (expr or "").split()
    if len(parts) != 5:
        raise ValueError("cron 需要 5 段：分 时 日 月 周")
    field = r"(\*|\*/\d+|\d+(-\d+)?)"
    for part in parts:
        if not re.fullmatch(rf"{field}(,{field})*", part):
            raise ValueError("cron 含无法识别的字段")
    return " ".join(parts)


def ensure_default_jobs(db):
    for user in db.query("SELECT username FROM users"):
        owner = user["username"]
        row = db.query_one(
            "SELECT id FROM agent_jobs WHERE owner=? AND name=?", (owner, "每晚复盘"))
        if row:
            continue
        db.execute(
            "INSERT INTO agent_jobs(owner, name, enabled, cron, timezone, created_at) "
            "VALUES(?,?,0,?,?,?)",
            (owner, "每晚复盘", DEFAULT_CRON, DEFAULT_TZ, now()))


def list_jobs(db, user):
    return db.query(
        "SELECT id, owner, name, enabled, cron, timezone, last_run_at, last_status, "
        "last_message FROM agent_jobs WHERE owner=? ORDER BY id",
        (user["username"],))


def update_job(db, user, job_id, enabled, cron, timezone):
    row = db.query_one("SELECT * FROM agent_jobs WHERE id=? AND owner=?",
                       (job_id, user["username"]))
    if not row:
        raise ValueError("任务不存在")
    cron = validate_cron(cron or row["cron"])
    timezone = (timezone or row["timezone"] or DEFAULT_TZ).strip() or DEFAULT_TZ
    resolve_tz(timezone)
    db.execute(
        "UPDATE agent_jobs SET enabled=?, cron=?, timezone=? WHERE id=?",
        (1 if enabled else 0, cron, timezone, job_id))
    return db.query_one(
        "SELECT id, owner, name, enabled, cron, timezone, last_run_at, last_status, "
        "last_message FROM agent_jobs WHERE id=?", (job_id,))


def _review_session(db, owner):
    row = db.query_one(
        "SELECT id FROM agent_sessions WHERE owner=? AND title=? ORDER BY id DESC LIMIT 1",
        (owner, REVIEW_TITLE))
    if row:
        return row["id"]
    return store.create_session(db, {"username": owner}, REVIEW_TITLE)


def anomalies_since(db, owner, since_ts, limit=8):
    rows = db.query(
        "SELECT id, name, status, exit_code, log_file, finished_at FROM tasks "
        "WHERE owner=? AND finished_at>=? AND status IN ('failed','killed','done') "
        "ORDER BY finished_at DESC LIMIT 40",
        (owner, since_ts))
    found = []
    for row in rows:
        tail, signals = "", []
        path = row.get("log_file") or ""
        if path:
            try:
                tail = tools._tail_text(path, 40)
                signals = tools._signals(tail)
            except OSError:
                tail = ""
        if row["status"] not in ("failed", "killed") and not signals:
            continue
        found.append({
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "exit_code": row["exit_code"],
            "signals": signals,
            "tail": (tail or "")[-1500:],
        })
        if len(found) >= limit:
            break
    return found


def render_report(owner, items, summary=""):
    lines = [f"# 每晚复盘 · {owner}", "", f"- 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    if summary:
        lines += ["## 摘要", "", summary.strip(), ""]
    for item in items:
        lines += [
            f"## #{item['id']} {item['name']}",
            "",
            f"- 状态: {item['status']}  退出码: {item['exit_code']}",
            f"- 信号: {'、'.join(item['signals']) or '无'}",
            "",
            "```",
            item["tail"] or "（无日志）",
            "```",
            "",
        ]
    return "\n".join(lines).strip() + "\n"


async def _summarize(body):
    try:
        return await chat.chat(
            [{"role": "user", "content":
              "用不超过 150 字概括这些实验异常，并给一个下一步。不要编造日志里没有的数字。\n\n"
              + body[:6000]}],
            "你是科研工作台的复盘助手。只根据给定材料作答，用中文。")
    except chat.ChatError:
        return ""


async def run_job(db, job_id, manual=False):
    lock = _locks.setdefault(job_id, asyncio.Lock())
    if lock.locked():
        return {"ok": False, "message": "正在复盘"}
    async with lock:
        job = db.query_one("SELECT * FROM agent_jobs WHERE id=?", (job_id,))
        if not job:
            return {"ok": False, "message": "任务不存在"}
        moment = local_now(job.get("timezone"))
        start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        items = anomalies_since(db, job["owner"], start.timestamp())
        if not items:
            message = "今天没有失败或日志异常的任务"
            db.execute(
                "UPDATE agent_jobs SET last_run_at=?, last_status=?, last_message=? WHERE id=?",
                (now(), "empty", message, job_id))
            return {"ok": True, "message": message, "file": None}
        body = render_report(job["owner"], items)
        summary = await _summarize(body)
        text = render_report(job["owner"], items, summary)
        sid = _review_session(db, job["owner"])
        stamp = moment.strftime("%Y%m%d-%H%M%S" if manual else "%Y%m%d-%H%M")
        saved = artifacts.write_text(job["owner"], sid, f"review-{stamp}.md", text)
        message = f"已写入 {saved['name']}，{len(items)} 个异常"
        db.execute(
            "UPDATE agent_jobs SET last_run_at=?, last_status=?, last_message=? WHERE id=?",
            (now(), "done", message, job_id))
        return {"ok": True, "message": message, "file": saved["name"], "session_id": sid}


async def tick(db):
    ensure_default_jobs(db)
    for job in db.query("SELECT * FROM agent_jobs WHERE enabled=1"):
        if is_due(job):
            await run_job(db, job["id"])


async def loop(db):
    await asyncio.sleep(8)
    while True:
        try:
            await tick(db)
        except Exception as e:
            print(f"[agent-jobs] {e}")
        await asyncio.sleep(30)
