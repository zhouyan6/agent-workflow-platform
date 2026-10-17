"""Failure cases and the per-user agent profile. Profiles stay private to the owner."""
import json

from db import now

CASE_STATUSES = ("open", "resolved")


def _case(db, user, case_id):
    return db.query_one(
        "SELECT * FROM agent_cases WHERE id=? AND owner=?", (case_id, user["username"]))


def list_cases(db, user):
    return db.query(
        "SELECT id, title, experiment, symptom, cause, action, status, source_file, "
        "created_at, updated_at FROM agent_cases WHERE owner=? ORDER BY updated_at DESC",
        (user["username"],))


def _clean(text, limit):
    return (text or "").strip()[:limit]


def save_case(db, user, body, case_id=None):
    title = _clean(body.get("title"), 120) or "未命名案例"
    status = body.get("status") or "open"
    if status not in CASE_STATUSES:
        status = "open"
    fields = (
        title,
        _clean(body.get("experiment"), 200),
        _clean(body.get("symptom"), 2000),
        _clean(body.get("cause"), 2000),
        _clean(body.get("action"), 2000),
        status,
        _clean(body.get("source_file"), 80),
    )
    if case_id:
        if not _case(db, user, case_id):
            raise ValueError("案例不存在")
        db.execute(
            "UPDATE agent_cases SET title=?, experiment=?, symptom=?, cause=?, action=?, "
            "status=?, source_file=?, updated_at=? WHERE id=? AND owner=?",
            fields + (now(), case_id, user["username"]))
        return _case(db, user, case_id)
    cur = db.execute(
        "INSERT INTO agent_cases(owner, title, experiment, symptom, cause, action, status, "
        "source_file, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (user["username"],) + fields + (now(), now()))
    return _case(db, user, cur.lastrowid)


def delete_case(db, user, case_id):
    if not _case(db, user, case_id):
        raise ValueError("案例不存在")
    db.execute("DELETE FROM agent_cases WHERE id=? AND owner=?", (case_id, user["username"]))


def _join(a, b):
    parts = []
    for text in (a, b):
        text = (text or "").strip()
        if text and text not in parts:
            parts.append(text)
    return "\n".join(parts)[:2000]


def merge_cases(db, user, id_a, id_b):
    if int(id_a) == int(id_b):
        raise ValueError("请选择两个不同的案例")
    a, b = _case(db, user, id_a), _case(db, user, id_b)
    if not a or not b:
        raise ValueError("案例不存在")
    db.execute(
        "UPDATE agent_cases SET title=?, experiment=?, symptom=?, cause=?, action=?, "
        "updated_at=? WHERE id=? AND owner=?",
        (_join(a["title"], b["title"])[:120] or a["title"],
         _join(a["experiment"], b["experiment"])[:200],
         _join(a["symptom"], b["symptom"]),
         _join(a["cause"], b["cause"]),
         _join(a["action"], b["action"]),
         now(), a["id"], user["username"]))
    db.execute("DELETE FROM agent_cases WHERE id=? AND owner=?", (b["id"], user["username"]))
    return _case(db, user, a["id"])


def split_case(db, user, case_id):
    row = _case(db, user, case_id)
    if not row:
        raise ValueError("案例不存在")
    moved = (row.get("action") or "").strip()
    db.execute(
        "UPDATE agent_cases SET action='', updated_at=? WHERE id=? AND owner=?",
        (now(), case_id, user["username"]))
    cur = db.execute(
        "INSERT INTO agent_cases(owner, title, experiment, symptom, cause, action, status, "
        "source_file, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (user["username"], (row["title"] + "（拆分）")[:120], row.get("experiment") or "",
         "" if moved else (row.get("symptom") or ""),
         "", moved, "open", row.get("source_file") or "", now(), now()))
    return {"original": _case(db, user, case_id), "created": _case(db, user, cur.lastrowid)}


def _builtin_names():
    from agent.tools import BUILTIN_SKILLS
    return [name for name, _ in BUILTIN_SKILLS]


def _parse_skill_sets(raw):
    """Return (on, off). Skills that are in neither set stay on, so new tools are available."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    names = set(_builtin_names())
    if isinstance(parsed, dict):
        off = {str(x) for x in (parsed.get("off") or [])}
        return names - off
    if isinstance(parsed, list):
        # older allow-list: keep what was checked, and turn on skills added later
        return {str(x) for x in parsed} | names
    return None


def get_profile(db, user):
    row = db.query_one(
        "SELECT prompt, skills_json, updated_at FROM agent_profiles WHERE owner=?",
        (user["username"],))
    enabled = _parse_skill_sets((row or {}).get("skills_json"))
    return {
        "prompt": (row or {}).get("prompt") or "",
        "skills": None if enabled is None else sorted(enabled),
        "updated_at": (row or {}).get("updated_at"),
    }


def enabled_skills(db, username):
    """None means every builtin skill."""
    row = db.query_one("SELECT skills_json FROM agent_profiles WHERE owner=?", (username,))
    if not row or not row.get("skills_json"):
        return None
    return _parse_skill_sets(row["skills_json"])


def save_profile(db, user, prompt, skills):
    prompt = _clean(prompt, 2000)
    if skills is None:
        skills_json = None
    else:
        chosen = []
        for name in skills:
            name = str(name or "").strip()
            if name and name not in chosen:
                chosen.append(name)
        catalog = _builtin_names()
        skills_json = json.dumps({
            "on": [n for n in chosen if n in catalog],
            "off": [n for n in catalog if n not in chosen],
        }, ensure_ascii=False)
    db.execute(
        "INSERT INTO agent_profiles(owner, prompt, skills_json, updated_at) VALUES(?,?,?,?) "
        "ON CONFLICT(owner) DO UPDATE SET prompt=excluded.prompt, "
        "skills_json=excluded.skills_json, updated_at=excluded.updated_at",
        (user["username"], prompt, skills_json, now()))
    return get_profile(db, user)
