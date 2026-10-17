"""SQLite persistence for agent sessions, messages, runs, and skills."""
import json

from db import now


def create_session(db, user, title="新对话"):
    title = (title or "新对话").strip()[:80] or "新对话"
    t = now()
    cur = db.execute(
        "INSERT INTO agent_sessions(owner, title, created_at, updated_at) VALUES(?,?,?,?)",
        (user["username"], title, t, t))
    return cur.lastrowid


def _visible(row, user):
    if not row:
        return False
    if user.get("role") == "advisor":
        return True
    return row.get("owner") == user.get("username")


def get_session(db, session_id, user):
    row = db.query_one("SELECT * FROM agent_sessions WHERE id=?", (session_id,))
    return row if _visible(row, user) else None


def list_sessions(db, user, limit=50):
    if user.get("role") == "advisor":
        return db.query(
            "SELECT id, owner, title, created_at, updated_at FROM agent_sessions "
            "ORDER BY updated_at DESC LIMIT ?", (limit,))
    return db.query(
        "SELECT id, owner, title, created_at, updated_at FROM agent_sessions "
        "WHERE owner=? ORDER BY updated_at DESC LIMIT ?",
        (user["username"], limit))


def rename_session(db, session_id, title):
    title = (title or "新对话").replace("\n", " ").strip()[:80] or "新对话"
    db.execute("UPDATE agent_sessions SET title=?, updated_at=? WHERE id=?",
               (title, now(), session_id))


def touch_session(db, session_id):
    db.execute("UPDATE agent_sessions SET updated_at=? WHERE id=?", (now(), session_id))


def delete_session(db, session_id):
    db.execute("DELETE FROM agent_messages WHERE session_id=?", (session_id,))
    db.execute("DELETE FROM agent_runs WHERE session_id=?", (session_id,))
    db.execute("DELETE FROM agent_sessions WHERE id=?", (session_id,))


def add_message(db, session_id, role, content="", tool_name=None,
                tool_call_id=None, tool_calls=None):
    db.execute(
        "INSERT INTO agent_messages(session_id, role, content, tool_name, tool_call_id, "
        "tool_calls_json, created_at) VALUES(?,?,?,?,?,?,?)",
        (session_id, role, content or "", tool_name, tool_call_id,
         json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None, now()))
    touch_session(db, session_id)


def list_messages(db, session_id):
    rows = db.query(
        "SELECT id, session_id, role, content, tool_name, tool_call_id, tool_calls_json, "
        "created_at FROM agent_messages WHERE session_id=? ORDER BY id", (session_id,))
    for row in rows:
        raw = row.pop("tool_calls_json", None)
        row["tool_calls"] = json.loads(raw) if raw else None
    return rows


def to_model_messages(rows):
    """OpenAI-style history. System prompt is added by the caller."""
    out = []
    for m in rows:
        role = m["role"]
        if role == "user":
            out.append({"role": "user", "content": m.get("content") or ""})
        elif role == "assistant":
            msg = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                msg["tool_calls"] = m["tool_calls"]
                if not msg["content"]:
                    msg["content"] = None
            out.append(msg)
        elif role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.get("tool_call_id") or "",
                "content": m.get("content") or "",
            })
    return out


def start_run(db, session_id, owner):
    cur = db.execute(
        "INSERT INTO agent_runs(session_id, owner, status, started_at) VALUES(?,?,?,?)",
        (session_id, owner, "running", now()))
    return cur.lastrowid


def finish_run(db, run_id, status, error=None):
    db.execute(
        "UPDATE agent_runs SET status=?, finished_at=?, error=? WHERE id=?",
        (status, now(), error, run_id))


def seed_skills(db, skills):
    for name, description in skills:
        db.execute(
            "INSERT INTO agent_skills(name, owner, kind, enabled, description) VALUES(?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET description=excluded.description, kind='builtin', "
            "enabled=1",
            (name, "", "builtin", 1, description))


def list_skills(db):
    return db.query(
        "SELECT name, kind, enabled, description FROM agent_skills "
        "WHERE enabled=1 ORDER BY name")
