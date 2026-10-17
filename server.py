"""GPU workbench FastAPI server.

Default bind 0.0.0.0:8787 so campus LAN can open http://<server-ip>:8787
(login required). Override with WORKBENCH_HOST / WORKBENCH_PORT.
"""
import asyncio
import glob
import json
import os
import re
import secrets
import signal
import stat as stat_mod
import time

from fastapi import Depends, FastAPI, HTTPException, File, Form, Header, Request, UploadFile
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import chat
import experiments as exp
import feishu
from agent.library import enabled_skills as agent_enabled_skills
from agent.routes import mount_agent
from agent.tools import ToolCtx
from db import DB, now, hash_password, seed_users, verify_password
from monitor import GPUMonitor
from scheduler import Scheduler

ROOT = os.path.dirname(os.path.abspath(__file__))

db = DB()
seed_users(db)


def _backfill_paper_titles():
    """One-time: give already-extracted papers their real title from the card."""
    for row in db.query("SELECT id, title, card_json FROM papers "
                        "WHERE title='上传的 PDF' AND card_json IS NOT NULL"):
        try:
            t = (json.loads(row["card_json"]).get("文献信息") or {}).get("标题")
        except (ValueError, TypeError):
            continue
        if t:
            db.execute("UPDATE papers SET title=? WHERE id=?", (str(t)[:200], row["id"]))


_backfill_paper_titles()
monitor = GPUMonitor()
scheduler = Scheduler(db, monitor, ROOT)
monitor.task_pids_provider = lambda: [
    r["pid"] for r in db.query("SELECT pid FROM tasks WHERE status='running'") if r["pid"]
]

app = FastAPI(title="GPU Workbench")


# ---------------- auth ----------------
SESSION_DAYS = 30


def _settings_map():
    return {r["key"]: r["value"] for r in db.query("SELECT key,value FROM settings")}


def _default_owner():
    """Owner attributed to terminal-started (non-workbench) experiment processes."""
    return _settings_map().get("default_owner", "zhouyan")


def resolve_token(token: str):
    """Return {username, role} for a live session token, else None."""
    token = (token or "").strip()
    if not token:
        return None
    row = db.query_one(
        "SELECT s.expires_at, u.username, u.role FROM sessions s "
        "JOIN users u ON u.username = s.username WHERE s.token=?", (token,))
    if not row:
        return None
    if row["expires_at"] < now():
        db.execute("DELETE FROM sessions WHERE token=?", (token,))
        return None
    return {"username": row["username"], "role": row["role"]}


def get_current_user(request: Request, authorization: str = Header(default="")):
    """Resolve user from Bearer token (wb CLI) or session cookie. 401 otherwise."""
    token = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    if not token:
        token = request.cookies.get("wb_session", "")
    user = resolve_token(token)
    if not user:
        raise HTTPException(401, "未登录或会话已过期,请重新登录")
    return user


def require_advisor(user=Depends(get_current_user)):
    if user["role"] != "advisor":
        raise HTTPException(403, "仅导师可操作")
    return user


def is_adv(user):
    return user["role"] == "advisor"


def _scoped_where(user, table, alias=""):
    """('' , ()) for advisor (sees all) or ('WHERE t.owner=?', (name,)) for student."""
    if is_adv(user):
        return "", ()
    return f"WHERE {alias or table}.owner=?", (user["username"],)


def _get_task_for(task_id, user):
    row = _get_task(task_id)
    if not (is_adv(user) or row.get("owner") == user["username"]):
        raise HTTPException(404, "task not found")
    return row


def _get_paper_for(paper_id, user):
    row = db.query_one("SELECT * FROM papers WHERE id=?", (paper_id,))
    if not row or not (is_adv(user) or row.get("owner") == user["username"]):
        raise HTTPException(404, "paper not found")
    return row


def _get_exp_for(exp_id, user):
    row = db.query_one("SELECT * FROM experiments WHERE id=?", (exp_id,))
    if not row or not (is_adv(user) or row.get("owner") == user["username"]):
        raise HTTPException(404, "experiment not found")
    return row


def _path_allowed(path, user):
    """Students may only read paths registered under their own tasks/experiments,
    or paths of live-discovered experiments attributed to them."""
    if is_adv(user):
        return True
    p = os.path.realpath(os.path.expanduser(path))
    row = db.query_one(
        "SELECT 1 AS ok FROM tasks WHERE owner=? AND log_file=? "
        "UNION ALL SELECT 1 FROM experiments WHERE owner=? AND jsonl_path=? "
        "UNION ALL SELECT 1 FROM experiments WHERE owner=? AND log_path=? LIMIT 1",
        (user["username"], p, user["username"], p, user["username"], p))
    if row:
        return True
    return _LIVE_PATH_OWNER.get(p) == user["username"]


def _proc_owner_allowed(pid, user):
    """A GPU pid is accessible if it belongs to the user's task, or user is
    advisor / default owner of terminal-started processes."""
    if is_adv(user):
        return True
    row = db.query_one("SELECT owner FROM tasks WHERE pid=? AND status='running'", (pid,))
    if row:
        return row["owner"] == user["username"]
    return user["username"] == _default_owner()


# live-discovered experiment paths -> owner; refreshed on every /api/liveexps poll
_LIVE_PATH_OWNER = {}


class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/login")
async def login(body: LoginIn):
    u = db.query_one("SELECT * FROM users WHERE username=?", (body.username.strip(),))
    if not u or not verify_password(body.password, u["pw_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions(token,username,created_at,expires_at) VALUES(?,?,?,?)",
               (token, u["username"], now(), now() + SESSION_DAYS * 86400))
    resp = JSONResponse({"username": u["username"], "role": u["role"], "token": token})
    resp.set_cookie("wb_session", token, max_age=SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", path="/")
    return resp


@app.post("/api/logout")
async def logout(request: Request, authorization: str = Header(default="")):
    token = authorization[7:].strip() if authorization.startswith("Bearer ") \
        else request.cookies.get("wb_session", "")
    if token:
        db.execute("DELETE FROM sessions WHERE token=?", (token,))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("wb_session", path="/")
    return resp


@app.get("/api/me")
async def me(user=Depends(get_current_user)):
    return user


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


def _validate_new_password(pw: str):
    pw = (pw or "").strip()
    if len(pw) < 6:
        raise HTTPException(400, "新密码至少 6 位")
    if len(pw) > 128:
        raise HTTPException(400, "新密码过长")
    return pw


@app.post("/api/me/password")
async def change_my_password(body: ChangePasswordIn, user=Depends(get_current_user)):
    """Self-service password change. Requires current password. Invalidates all sessions."""
    row = db.query_one("SELECT pw_hash FROM users WHERE username=?", (user["username"],))
    if not row or not verify_password(body.old_password, row["pw_hash"]):
        raise HTTPException(400, "当前密码不正确")
    new_pw = _validate_new_password(body.new_password)
    if body.old_password == new_pw:
        raise HTTPException(400, "新密码不能与当前密码相同")
    db.execute("UPDATE users SET pw_hash=? WHERE username=?",
               (hash_password(new_pw), user["username"]))
    db.execute("DELETE FROM sessions WHERE username=?", (user["username"],))
    return {"ok": True, "message": "密码已更新，请重新登录"}


@app.get("/api/users")
async def list_users(user=Depends(require_advisor)):
    return db.query("SELECT username, role, created_at FROM users ORDER BY role, username")


@app.post("/api/users/{username}/password")
async def reset_password(username: str, body: dict, user=Depends(require_advisor)):
    if not db.query_one("SELECT 1 FROM users WHERE username=?", (username,)):
        raise HTTPException(404, "user not found")
    pw = _validate_new_password(body.get("password") or "")
    db.execute("UPDATE users SET pw_hash=? WHERE username=?", (hash_password(pw), username))
    db.execute("DELETE FROM sessions WHERE username=?", (username,))
    return {"ok": True}



@app.on_event("startup")
async def startup():
    asyncio.create_task(monitor.loop())
    asyncio.create_task(scheduler.loop())
    from agent import jobs as agent_jobs
    asyncio.create_task(agent_jobs.loop(db))


# ---------------- GPU ----------------
@app.get("/api/gpu")
async def gpu(user=Depends(get_current_user)):
    return monitor.snapshot()


@app.get("/api/gpu/stream")
async def gpu_stream(user=Depends(get_current_user)):
    async def gen():
        while True:
            yield f"data: {json.dumps(monitor.snapshot())}\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------- tasks ----------------
class TaskIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    command: str = Field(min_length=1)
    gpus_needed: int = Field(ge=1, le=8)
    workdir: str | None = None
    gpus_pinned: str | None = None  # e.g. "2" or "2,5" — wait for exactly these cards


def _clean_pin(pin: str | None):
    if not pin:
        return None
    try:
        pins = sorted({int(x) for x in pin.split(",") if x.strip() != ""})
    except ValueError:
        raise HTTPException(400, "指定卡格式错误,应为如 2 或 2,5")
    if not pins or len(pins) > 8:
        raise HTTPException(400, "指定卡为空或超过 8 张")
    return ",".join(str(p) for p in pins)


def _proc_cwd(pid):
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def _external_task_name(cmd: str, proc_name: str) -> str:
    m = re.search(r"([^\s/]+\.py\b)", cmd or "")
    if m:
        return m.group(1)
    base = (proc_name or "gpu-proc").split("/")[-1]
    return (base or "gpu-proc")[:60]


def _list_external_tasks(user, owner_filter: str = ""):
    """GPU processes started outside the workbench (SSH/tmux/etc), as queue rows."""
    snap = monitor.snapshot()
    default_owner = _default_owner()
    task_pids = {
        r["pid"] for r in db.query("SELECT pid FROM tasks WHERE status='running' AND pid IS NOT NULL")
        if r["pid"]
    }
    by_pid = {}
    for g in snap.get("gpus", []):
        for p in g.get("procs", []):
            pid = p.get("pid")
            if not pid or p.get("user") != snap.get("me"):
                continue
            if p.get("wb") or pid in task_pids:
                continue
            owner = default_owner
            trow = db.query_one(
                "SELECT owner FROM tasks WHERE pid=? AND status='running'", (pid,))
            if trow:
                continue  # already a workbench task
            if not (is_adv(user) or owner == user["username"]):
                continue
            if owner_filter and owner != owner_filter:
                continue
            ent = by_pid.get(pid)
            if not ent:
                cmd = (p.get("cmd") or p.get("name") or "")[:500]
                run_sec = p.get("run")
                started = (time.time() - run_sec) if isinstance(run_sec, (int, float)) else None
                by_pid[pid] = {
                    "id": -int(pid),           # synthetic; avoids clashing with DB ids
                    "source": "external",
                    "name": _external_task_name(cmd, p.get("name") or ""),
                    "command": cmd or (p.get("name") or f"pid:{pid}"),
                    "gpus_needed": 1,
                    "workdir": _proc_cwd(pid),
                    "status": "running",
                    "gpu_assigned": str(g["index"]),
                    "pid": pid,
                    "log_file": None,
                    "owner": owner,
                    "created_at": started,
                    "started_at": started,
                    "finished_at": None,
                    "exit_code": None,
                    "gpus_pinned": None,
                    "after_task": None,
                }
            else:
                gpus = set(str(x) for x in str(ent["gpu_assigned"]).split(",") if x != "")
                gpus.add(str(g["index"]))
                ent["gpu_assigned"] = ",".join(sorted(gpus, key=lambda x: int(x)))
                ent["gpus_needed"] = len(gpus)
    return sorted(by_pid.values(), key=lambda x: (-(x["started_at"] or 0), x["pid"]))


@app.get("/api/tasks")
async def list_tasks(owner: str = "", user=Depends(get_current_user)):
    where, params = _scoped_where(user, "tasks")
    if is_adv(user) and owner:
        where = "WHERE owner=?"
        params = (owner,)
    rows = db.query(f"SELECT * FROM tasks {where} ORDER BY id DESC LIMIT 200", params)
    out = []
    for r in rows:
        d = dict(r)
        d["source"] = "workbench"
        if d["status"] == "running" and d["log_file"] and os.path.isfile(d["log_file"]):
            try:
                with open(d["log_file"], "rb") as f:
                    size = os.path.getsize(d["log_file"])
                    f.seek(max(0, size - 8192))
                    lines = [l for l in f.read().decode("utf-8", "replace").splitlines() if l.strip()]
                if lines:
                    d["log_tail"] = lines[-1][:400]
                alerts = []
                for l in lines[-100:]:
                    for kw in ("Traceback", "RuntimeError", "CUDA out of memory",
                               "CUDA error", "NaN detected", "loss=nan"):
                        if kw in l and kw not in alerts:
                            alerts.append(kw)
                if alerts:
                    d["alert"] = ",".join(alerts[:3])
            except OSError:
                pass
        out.append(d)
    # External (non-workbench) GPU jobs first among running items
    try:
        external = _list_external_tasks(user, owner if is_adv(user) else "")
    except Exception:
        external = []
    if external:
        out = external + out
    return out


@app.post("/api/tasks")
async def create_task(t: TaskIn, user=Depends(get_current_user)):
    if t.workdir and not os.path.isdir(t.workdir):
        raise HTTPException(400, f"workdir does not exist: {t.workdir}")
    pin = _clean_pin(t.gpus_pinned)
    need = len(pin.split(",")) if pin else t.gpus_needed
    cur = db.execute(
        "INSERT INTO tasks(name, command, gpus_needed, workdir, gpus_pinned, owner, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (t.name, t.command, need, t.workdir, pin, user["username"], now()),
    )
    return db.query_one("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,))


class ChainIn(BaseModel):
    name_prefix: str = Field(min_length=1, max_length=100)
    commands: list[str] = Field(min_length=1)
    gpus_needed: int = Field(ge=1, le=8, default=1)
    workdir: str | None = None
    gpus_pinned: str | None = None


@app.post("/api/tasks/chain")
async def create_chain(c: ChainIn, user=Depends(get_current_user)):
    """Sequential task chain: task[i+1] starts only after task[i] succeeds (exit 0)."""
    if c.workdir and not os.path.isdir(c.workdir):
        raise HTTPException(400, f"workdir does not exist: {c.workdir}")
    pin = _clean_pin(c.gpus_pinned)
    need = len(pin.split(",")) if pin else c.gpus_needed
    created, prev_id = [], None
    for i, cmd in enumerate(c.commands, 1):
        cur = db.execute(
            "INSERT INTO tasks(name, command, gpus_needed, workdir, gpus_pinned, after_task, "
            "owner, created_at) VALUES(?,?,?,?,?,?,?,?)",
            (f"{c.name_prefix}·{i}/{len(c.commands)}", cmd, need, c.workdir, pin,
             prev_id, user["username"], now()),
        )
        prev_id = cur.lastrowid
        created.append(db.query_one("SELECT * FROM tasks WHERE id=?", (cur.lastrowid,)))
    return created


@app.post("/api/tasks/{task_id}/stop")
async def stop_task(task_id: int, user=Depends(get_current_user)):
    _get_task_for(task_id, user)
    try:
        await asyncio.to_thread(scheduler.stop, task_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: int, user=Depends(get_current_user)):
    _get_task_for(task_id, user)
    scheduler.cancel(task_id)
    return {"ok": True}


@app.post("/api/procs/{pid}/stop")
async def stop_proc(pid: int, user=Depends(get_current_user)):
    """Stop one of MY gpu processes (started outside the workbench). Owner-checked."""
    try:
        uid = os.stat(f"/proc/{pid}").st_uid
    except OSError:
        raise HTTPException(404, "process not found")
    if uid != os.getuid():
        raise HTTPException(403, "not your process")
    if not _proc_owner_allowed(pid, user):
        raise HTTPException(404, "process not found")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


def _file_info(path):
    try:
        st = os.stat(path)
        return {"path": path, "size": st.st_size,
                "mtime": st.st_mtime, "uid": st.st_uid}
    except OSError:
        return None


@app.get("/api/procs/{pid}/logs")
async def proc_logs(pid: int, user=Depends(get_current_user)):
    """Discover log files of a running process: open fds + recent files in cwd."""
    if not _proc_owner_allowed(pid, user):
        raise HTTPException(404, "process not found")
    candidates, seen = [], set()

    def add(path):
        if path in seen:
            return
        info = _file_info(path)
        if info and info["size"] > 0:
            seen.add(path)
            candidates.append(info)

    try:  # files the process currently holds open (its real log/jsonl)
        for fd in glob.glob(f"/proc/{pid}/fd/*"):
            try:
                target = os.path.realpath(fd)
                if os.stat(target).st_mode & 0o170000 == stat_mod.S_IFREG:
                    add(target)
            except OSError:
                continue
    except OSError:
        pass
    cwd = None
    try:
        cwd = os.path.realpath(f"/proc/{pid}/cwd")
    except OSError:
        pass
    if cwd and os.path.isdir(cwd):  # recently written files near the workdir
        pats = ["*.log", "*.jsonl", "*.txt", "*.out", "logs/*.log", "logs/*.jsonl",
                "log/*.log", "output/*.log", "outputs/*.jsonl"]
        for pat in pats:
            for f in glob.glob(os.path.join(cwd, pat))[:30]:
                try:
                    if os.path.isfile(f) and os.stat(f).st_uid == os.getuid():
                        add(f)
                except OSError:
                    continue
    candidates.sort(key=lambda c: c["mtime"], reverse=True)
    return {"cwd": cwd, "candidates": candidates[:15]}


def _check_log_path(path, user=None):
    path = os.path.realpath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise HTTPException(404, f"file not found: {path}")
    if os.stat(path).st_uid != os.getuid():
        raise HTTPException(403, "not your file")
    if user is not None and not _path_allowed(path, user):
        raise HTTPException(404, f"file not found: {path}")
    return path


def _pid_files(pid, exts, cwd_pats=()):
    """Newest files (by mtime) with given extensions among the pid's open fds,
    falling back to recent files under its working directory."""
    cands = []
    try:
        for fd in glob.glob(f"/proc/{pid}/fd/*"):
            try:
                target = os.path.realpath(fd)
                st = os.stat(target)
                if (st.st_mode & 0o170000 == stat_mod.S_IFREG and st.st_size > 0
                        and st.st_uid == os.getuid()
                        and target.endswith(tuple(exts))):
                    cands.append((st.st_mtime, target))
            except OSError:
                continue
    except OSError:
        pass
    if not cands:
        try:
            cwd = os.path.realpath(f"/proc/{pid}/cwd")
        except OSError:
            cwd = None
        if cwd and os.path.isdir(cwd):
            for pat in cwd_pats:
                for f in glob.glob(os.path.join(cwd, pat), recursive=True):
                    try:
                        st = os.stat(f)
                        if st.st_size > 0 and st.st_uid == os.getuid() and f.endswith(tuple(exts)):
                            cands.append((st.st_mtime, f))
                    except OSError:
                        continue
    return max(cands)[1] if cands else None


def _exp_name(jsonl):
    parts = [p for p in jsonl.split("/") if p]
    return "/".join(parts[-3:-1]) if len(parts) >= 3 else jsonl


OUTPUT_DIR_RE = re.compile(r"--output_dir[= ]([^\s]+)")
ROUNDS_RE = re.compile(r"--communication_rounds[= ](\d+)")


def _read_cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def _exp_files_for_pid(pid):
    """Map a training process to its jsonl + text log via --output_dir in cmdline."""
    jsonl = log = None
    cmd = _read_cmdline(pid)
    m = OUTPUT_DIR_RE.search(cmd)
    if m:
        od = m.group(1)
        if not od.startswith("/"):
            try:
                cwd = os.path.realpath(f"/proc/{pid}/cwd")
            except OSError:
                cwd = "."
            od = os.path.normpath(os.path.join(cwd, od))
        jp = os.path.join(od, "loss_history.jsonl")
        if os.path.isfile(jp):
            jsonl = jp
        seed_dir = os.path.basename(od.rstrip("/"))              # seed5 or exp name
        stem = os.path.basename(os.path.dirname(od.rstrip("/")))  # batch or exp name
        # text logs conventionally sit in <batch>/logs/ or the batch root, named
        # after the experiment. When the jsonl sits directly in the exp dir (no
        # seed subdir), the exp dir name alone identifies the log; for the
        # seed-subdir layout require the exp stem too (seeds repeat across exps).
        generic_seed = re.fullmatch(r"seed\d+", seed_dir)
        for d in (os.path.join(os.path.dirname(os.path.dirname(od)), "logs"),
                  os.path.join(os.path.dirname(od), "logs"),
                  os.path.dirname(od)):
            if os.path.isdir(d):
                cands = [(os.path.getmtime(f), f)
                         for f in glob.glob(os.path.join(d, "*.log"))
                         if seed_dir in os.path.basename(f)
                         and (generic_seed is None or stem in os.path.basename(f))]
                if cands:
                    log = max(cands)[1]
                    break
    if not jsonl:
        jsonl = _pid_files(pid, (".jsonl",), ("output/**/*.jsonl",))
    if not log:
        # fd fallback: training usually has its log open as stdout/stderr
        log = _pid_files(pid, (".log",))
    return jsonl, log


@app.get("/api/liveexps")
async def live_exps(user=Depends(get_current_user)):
    """Auto-discover MY running GPU experiments: pid -> jsonl/log being written.
    Attribution: pid matches a task row -> task owner; else default_owner."""
    snap = monitor.snapshot()
    default_owner = _default_owner()
    out, seen = [], set()
    for g in snap.get("gpus", []):
        for p in g.get("procs", []):
            if p.get("user") != snap.get("me") or p.get("wb"):
                continue
            trow = db.query_one("SELECT owner FROM tasks WHERE pid=? AND status='running'",
                                (p["pid"],))
            owner = trow["owner"] if trow else default_owner
            if not (is_adv(user) or owner == user["username"]):
                continue
            j, log = _exp_files_for_pid(p["pid"])
            if not j or j in seen:
                continue
            seen.add(j)
            eta = None
            if log:
                mr = ROUNDS_RE.search(_read_cmdline(p["pid"]))
                if mr:
                    eta = exp.round_eta(log, int(mr.group(1)))
            out.append({"pid": p["pid"], "gpu": g["index"], "jsonl": j, "log": log,
                        "name": _exp_name(j) + f" · GPU{g['index']}", "eta": eta,
                        "owner": owner})
    out.sort(key=lambda x: x["gpu"])
    _LIVE_PATH_OWNER.clear()
    for x in out:
        _LIVE_PATH_OWNER[os.path.realpath(x["jsonl"])] = x["owner"]
        if x["log"]:
            _LIVE_PATH_OWNER[os.path.realpath(x["log"])] = x["owner"]
    return out


@app.get("/api/metrics")
async def metrics_by_path(jsonl: str, log: str = "", user=Depends(get_current_user)):
    j = _check_log_path(jsonl, user)
    lp = None
    if log:
        try:
            lp = _check_log_path(log, user)
        except HTTPException:
            lp = None
    return exp.compute_metrics(j, lp)


@app.get("/api/series")
async def series_by_path(jsonl: str, mode: str = "", limit: int = 3000,
                         user=Depends(get_current_user)):
    j = _check_log_path(jsonl, user)
    records, err = exp.parse_jsonl(j, limit=limit, mode=mode or None, scope="epoch")
    if err:
        raise HTTPException(400, err)
    return exp.series(records)


def _best_log_for_pid(pid):
    """Pick the log file an experiment process is actively writing."""
    cands = []
    try:
        for fd in glob.glob(f"/proc/{pid}/fd/*"):
            try:
                target = os.path.realpath(fd)
                st = os.stat(target)
                if (st.st_mode & 0o170000 == stat_mod.S_IFREG and st.st_size > 0
                        and st.st_uid == os.getuid()):
                    cands.append((st.st_mtime, target))
            except OSError:
                continue
    except OSError:
        pass
    logs = [c for c in cands if c[1].endswith((".log", ".jsonl", ".txt", ".out"))]
    if logs:
        return max(logs)[1]
    if cands:
        return max(cands)[1]
    # fallback: newest log-looking file in the process working dir
    try:
        cwd = os.path.realpath(f"/proc/{pid}/cwd")
    except OSError:
        return None
    c2 = []
    for pat in ("*.log", "*.jsonl", "*.out", "logs/*.log", "logs/*.jsonl",
                "log/*.log", "output/*.log", "outputs/*.jsonl"):
        for f in glob.glob(os.path.join(cwd, pat))[:30]:
            try:
                st = os.stat(f)
                if st.st_size > 0 and st.st_uid == os.getuid():
                    c2.append((st.st_mtime, f))
            except OSError:
                continue
    return max(c2)[1] if c2 else None


def _tail_stream(file):
    """Spawn `tail -F -n 100 <file>` and stream its stdout as SSE. Killed on disconnect."""
    async def gen():
        proc = await asyncio.create_subprocess_exec(
            "tail", "-F", "-n", "100", file,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            yield f"data: {json.dumps({'__file__': file})}\n\n"
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                yield f"data: {json.dumps(line.decode('utf-8', 'replace').rstrip())}\n\n"
        finally:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/logs/attach")
async def logfile_attach(path: str, user=Depends(get_current_user)):
    return _tail_stream(_check_log_path(path, user))


@app.get("/api/procs/{pid}/attach")
async def proc_attach(pid: int, user=Depends(get_current_user)):
    """tail -f the log of a running experiment process (auto-detected)."""
    if not _proc_owner_allowed(pid, user):
        raise HTTPException(404, "process not found")
    file = _best_log_for_pid(pid)
    if not file:
        async def nofile():
            yield f"data: {json.dumps({'__error__': '未找到该进程的日志文件'})}\n\n"
        return StreamingResponse(nofile(), media_type="text/event-stream")
    return _tail_stream(file)


@app.get("/api/logs/tail")
async def logfile_tail(path: str, lines: int = 300, user=Depends(get_current_user)):
    path = _check_log_path(path, user)
    try:
        with open(path, "rb") as f:
            size = os.path.getsize(path)
            f.seek(max(0, size - 1024 * 1024))
            data = f.read().decode("utf-8", "replace").splitlines()
        return {"lines": data[-lines:]}
    except OSError as e:
        raise HTTPException(400, str(e))


@app.get("/api/logs/stream")
async def logfile_stream(path: str, user=Depends(get_current_user)):
    path = _check_log_path(path, user)

    async def gen():
        offset = 0
        while True:
            try:
                size = os.path.getsize(path)
                if size > 512 * 1024:
                    offset = max(offset, size - 512 * 1024)
                with open(path, "rb") as f:
                    f.seek(offset)
                    chunk = f.read().decode("utf-8", errors="replace")
                    offset = f.tell()
                if chunk:
                    for line in chunk.splitlines():
                        yield f"data: {json.dumps(line)}\n\n"
            except OSError:
                pass
            await asyncio.sleep(1.5)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _get_task(task_id):
    row = db.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not row:
        raise HTTPException(404, "task not found")
    return row


@app.get("/api/tasks/{task_id}/log")
async def task_log(task_id: int, lines: int = 200, user=Depends(get_current_user)):
    row = _get_task_for(task_id, user)
    if not row["log_file"] or not os.path.isfile(row["log_file"]):
        return {"lines": []}
    with open(row["log_file"], "rb") as f:
        data = f.read().decode("utf-8", errors="replace").splitlines()
    return {"lines": data[-lines:]}


@app.get("/api/tasks/{task_id}/log/stream")
async def task_log_stream(task_id: int, user=Depends(get_current_user)):
    row = _get_task_for(task_id, user)
    path = row["log_file"]

    async def gen():
        offset = 0
        while True:
            chunk = ""
            if path and os.path.isfile(path):
                with open(path, "rb") as f:
                    size = os.path.getsize(path)
                    if size > 512 * 1024:  # start from last 512KB
                        offset = max(offset, size - 512 * 1024)
                    f.seek(offset)
                    chunk = f.read().decode("utf-8", errors="replace")
                    offset = f.tell()
            if chunk:
                for line in chunk.splitlines():
                    yield f"data: {json.dumps(line)}\n\n"
            current = db.query_one("SELECT status FROM tasks WHERE id=?", (task_id,))
            if current and current["status"] not in ("running", "pending"):
                yield "data: [__END__]\n\n"
                return
            await asyncio.sleep(1.5)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------- experiments ----------------
class ExpIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    jsonl_path: str = Field(min_length=1)
    log_path: str | None = None


@app.get("/api/experiments")
async def list_exps(user=Depends(get_current_user)):
    where, params = _scoped_where(user, "experiments")
    return db.query(f"SELECT * FROM experiments {where} ORDER BY id DESC", params)


@app.post("/api/experiments")
async def create_exp(e: ExpIn, user=Depends(get_current_user)):
    path = os.path.expanduser(e.jsonl_path)
    if not os.path.isfile(path):
        raise HTTPException(400, f"file not found: {path}")
    log_path = os.path.expanduser(e.log_path) if e.log_path else None
    if log_path and not os.path.isfile(log_path):
        log_path = None
    cur = db.execute(
        "INSERT INTO experiments(name, jsonl_path, log_path, owner, created_at) "
        "VALUES(?,?,?,?,?)",
        (e.name, path, log_path, user["username"], now()),
    )
    return db.query_one("SELECT * FROM experiments WHERE id=?", (cur.lastrowid,))


@app.post("/api/experiments/{exp_id}/logpath")
async def set_exp_logpath(exp_id: int, body: dict, user=Depends(get_current_user)):
    _get_exp_for(exp_id, user)
    lp = os.path.expanduser(body.get("log_path") or "")
    if not os.path.isfile(lp):
        raise HTTPException(400, f"file not found: {lp}")
    db.execute("UPDATE experiments SET log_path=? WHERE id=?", (lp, exp_id))
    return {"ok": True}


@app.delete("/api/experiments/{exp_id}")
async def delete_exp(exp_id: int, user=Depends(get_current_user)):
    _get_exp_for(exp_id, user)
    db.execute("DELETE FROM experiments WHERE id=?", (exp_id,))
    return {"ok": True}


@app.get("/api/experiments/{exp_id}/series")
async def exp_series(exp_id: int, limit: int = 3000, mode: str = "",
                     user=Depends(get_current_user)):
    row = _get_exp_for(exp_id, user)
    records, err = exp.parse_jsonl(row["jsonl_path"], limit=limit,
                                   mode=mode or None, scope="epoch")
    if err:
        raise HTTPException(400, err)
    return exp.series(records)


@app.get("/api/experiments/{exp_id}/metrics")
async def exp_metrics(exp_id: int, user=Depends(get_current_user)):
    row = _get_exp_for(exp_id, user)
    return exp.compute_metrics(row["jsonl_path"], row.get("log_path"))


# ---------------- chat ----------------
class ChatIn(BaseModel):
    messages: list  # [{role, content}]
    include_gpu: bool = True
    experiment_id: int | None = None
    exp_steps: int = 50
    jsonl: str | None = None   # live experiment (by path) instead of registered id
    log: str | None = None
    exp_name: str | None = None


class AnalyzeIn(BaseModel):
    question: str = ""


def _tail_text(path, n=80, max_bytes=256 * 1024):
    try:
        with open(path, "rb") as f:
            size = os.path.getsize(path)
            f.seek(max(0, size - max_bytes))
            lines = f.read().decode("utf-8", "replace").splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""


def _visible_tasks(user):
    """Active tasks visible to this user (used for AI context injection)."""
    if is_adv(user):
        return db.query("SELECT * FROM tasks WHERE status IN ('pending','running') ORDER BY id")
    return db.query("SELECT * FROM tasks WHERE owner=? AND status IN ('pending','running') "
                    "ORDER BY id", (user["username"],))


async def _do_analyze(name, log_file, question, user):
    log_tail = _tail_text(log_file)
    if not log_tail:
        raise HTTPException(400, "日志为空或不可读")
    sys_prompt = chat.build_system_prompt(
        gpu_snapshot=monitor.snapshot(),
        tasks=_visible_tasks(user),
        exp_name=name,
        exp_summary="该实验最近的训练日志(截尾):\n" + log_tail,
    )
    q = question or ("分析这个实验最近的训练状态:loss 趋势、是否有异常(NaN/loss 爆炸/停滞/"
                     "梯度问题),以及下一步建议。用中文简洁回答。")
    try:
        reply = await chat.chat([{"role": "user", "content": q}], sys_prompt)
    except chat.ChatError as e:
        raise HTTPException(400, str(e))
    return {"reply": reply, "log_file": log_file}


@app.post("/api/procs/{pid}/analyze")
async def analyze_proc(pid: int, body: AnalyzeIn, user=Depends(get_current_user)):
    if not _proc_owner_allowed(pid, user):
        raise HTTPException(404, "process not found")
    file = _best_log_for_pid(pid)
    if not file:
        raise HTTPException(400, "未找到该进程的日志文件")
    cmd = ""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = f.read().replace(b"\x00", b" ").decode()[:120]
    except OSError:
        pass
    return await _do_analyze(f"进程 {pid} ({cmd})", file, body.question, user)


@app.post("/api/tasks/{task_id}/analyze")
async def analyze_task(task_id: int, body: AnalyzeIn, user=Depends(get_current_user)):
    row = _get_task_for(task_id, user)
    if not row["log_file"] or not os.path.isfile(row["log_file"]):
        raise HTTPException(400, "该任务没有日志")
    return await _do_analyze(f"工作台任务 #{task_id} {row['name']}", row["log_file"],
                             body.question, user)


@app.post("/api/chat")
async def do_chat(c: ChatIn, user=Depends(get_current_user)):
    tasks = _visible_tasks(user) if c.include_gpu else None
    exp_name, exp_summary = None, None
    if c.jsonl and os.path.isfile(c.jsonl):  # live experiment by path
        if not _path_allowed(c.jsonl, user):
            raise HTTPException(404, f"file not found: {c.jsonl}")
        records, _ = exp.parse_jsonl(c.jsonl, limit=10**9)
        if records:
            exp_name = c.exp_name or c.jsonl
            exp_summary = exp.summarize(records, c.exp_steps)
        if c.log and os.path.isfile(c.log):
            sv = exp.pick_score_series(exp.parse_scores(c.log))
            if sv and sv.get("values"):
                hist = ", ".join(f"r{r}:{v:.1f}" for r, v in zip(sv["rounds"], sv["values"]))
                exp_summary = (exp_summary + "\n\n" if exp_summary else "") + \
                    f"评测得分[{sv.get('name') or 'log'}] 逐轮: {hist} (最佳 {sv['best']:.2f})"
            tail = _tail_text(c.log, n=40)
            if tail:
                exp_summary = (exp_summary + "\n\n" if exp_summary else "") + \
                    "最新训练日志:\n" + tail
    elif c.experiment_id:
        row = _get_exp_for(c.experiment_id, user)
        if row:
            records, _ = exp.parse_jsonl(row["jsonl_path"], limit=10**9)
            if records:
                exp_name, exp_summary = row["name"], exp.summarize(records, c.exp_steps)
    sys_prompt = chat.build_system_prompt(
        gpu_snapshot=monitor.snapshot() if c.include_gpu else None,
        tasks=tasks, exp_name=exp_name, exp_summary=exp_summary,
    )
    try:
        reply = await chat.chat(c.messages, sys_prompt)
    except chat.ChatError as e:
        raise HTTPException(400, str(e))
    return {"reply": reply}


@app.post("/api/chat/prepare")
async def prepare_chat(c: ChatIn, user=Depends(get_current_user)):
    """Return system prompt + context for browser-direct chat (no LLM call)."""
    tasks = _visible_tasks(user) if c.include_gpu else None
    exp_name, exp_summary = None, None
    if c.jsonl and os.path.isfile(c.jsonl):
        if not _path_allowed(c.jsonl, user):
            raise HTTPException(404, f"file not found: {c.jsonl}")
        records, _ = exp.parse_jsonl(c.jsonl, limit=10**9)
        if records:
            exp_name = c.exp_name or c.jsonl
            exp_summary = exp.summarize(records, c.exp_steps)
        if c.log and os.path.isfile(c.log):
            sv = exp.pick_score_series(exp.parse_scores(c.log))
            if sv and sv.get("values"):
                hist = ", ".join(f"r{r}:{v:.1f}" for r, v in zip(sv["rounds"], sv["values"]))
                exp_summary = (exp_summary + "\n\n" if exp_summary else "") + \
                    f"评测得分[{sv.get('name') or 'log'}] 逐轮: {hist} (最佳 {sv['best']:.2f})"
            tail = _tail_text(c.log, n=40)
            if tail:
                exp_summary = (exp_summary + "\n\n" if exp_summary else "") + \
                    "最新训练日志:\n" + tail
    elif c.experiment_id:
        row = _get_exp_for(c.experiment_id, user)
        if row:
            records, _ = exp.parse_jsonl(row["jsonl_path"], limit=10**9)
            if records:
                exp_name, exp_summary = row["name"], exp.summarize(records, c.exp_steps)
    sys_prompt = chat.build_system_prompt(
        gpu_snapshot=monitor.snapshot() if c.include_gpu else None,
        tasks=tasks, exp_name=exp_name, exp_summary=exp_summary,
    )
    return {"system": sys_prompt}


def _smart_names(names):
    """Strip the common prefix/suffix tokens within a batch so only the
    distinguishing part of each experiment name remains."""
    if len(names) <= 1:
        return {n: n for n in names}
    toks = {n: n.split("_") for n in names}
    p = 0
    while all(len(t) > p for t in toks.values()) and len({t[p] for t in toks.values()}) == 1:
        p += 1
    s = 0
    while (all(len(t) > p + s for t in toks.values())
           and len({t[-1 - s] for t in toks.values()}) == 1):
        s += 1
    return {n: ("_".join(t[p:len(t) - s]) if len(t) - s > p else n)
            for n, t in toks.items()}


@app.get("/api/activity")
async def activity(days: int = 180, user=Depends(get_current_user)):
    """Experiment history with smart short names, best eval score and GPU.
    Students only see experiments they registered/are running plus their own tasks."""
    import time as _t
    cutoff = _t.time() - days * 86400
    roots = set()

    def add_root_from(path):
        if path:
            r = os.path.dirname(os.path.dirname(os.path.dirname(path)))
            if os.path.isdir(r):
                roots.add(r)

    scoped = not is_adv(user)
    where, params = _scoped_where(user, "experiments")
    exps = db.query(f"SELECT jsonl_path FROM experiments {where}", params)
    allowed_jsonls = {e["jsonl_path"] for e in exps}
    for x in await live_exps(user):
        add_root_from(x["jsonl"])
        allowed_jsonls.add(x["jsonl"])
    for e in exps:
        add_root_from(e["jsonl_path"])

    items, exps_by_batch = [], {}
    for root in roots:
        jps = glob.glob(os.path.join(root, "*", "*", "*", "loss_history.jsonl")) + \
              glob.glob(os.path.join(root, "*", "*", "loss_history.jsonl"))
        for jp in jps:
            if scoped and os.path.realpath(jp) not in {os.path.realpath(p)
                                                       for p in allowed_jsonls}:
                continue
            try:
                st = os.stat(jp)
            except OSError:
                continue
            if max(st.st_ctime, st.st_mtime) < cutoff:
                continue
            parts = jp.split(os.sep)
            batch, expdir, seed = parts[-4], parts[-3], parts[-2]
            # matching text log (for best score + gpu number)
            log, best, gpu = None, None, None
            generic = re.fullmatch(r"seed\d+", seed)
            for d in (os.path.join(root, batch, "logs"), os.path.join(root, batch)):
                if os.path.isdir(d):
                    cands = [(os.path.getmtime(f), f)
                             for f in glob.glob(os.path.join(d, "*.log"))
                             if seed in os.path.basename(f)
                             and (generic is None or expdir in os.path.basename(f))]
                    if cands:
                        log = max(cands)[1]
                        break
            if log:
                mg = re.search(r"_gpu(\d+)", os.path.basename(log))
                gpu = mg.group(1) if mg else None
                sv = exp.pick_score_series(exp.parse_scores(log))
                best = sv.get("best") if sv and sv.get("best") is not None else None
            item = {"name": f"{expdir}/{seed}", "short": expdir, "seed": seed,
                    "group": batch, "start": st.st_ctime, "end": st.st_mtime,
                    "kind": "experiment", "best": best, "gpu": gpu}
            items.append(item)
            exps_by_batch.setdefault(batch, set()).add(expdir)
    # smart short names per batch
    for batch, names in exps_by_batch.items():
        smart = _smart_names(sorted(names))
        for it in items:
            if it["group"] == batch:
                it["short"] = smart[it["name"].split("/")[0]]
    twhere, tparams = _scoped_where(user, "tasks")
    for t in db.query(f"SELECT name, created_at, started_at, finished_at FROM tasks {twhere}",
                      tparams):
        s = t["started_at"] or t["created_at"]
        e = t["finished_at"] or t["started_at"] or t["created_at"]
        if s >= cutoff:
            items.append({"name": t["name"], "short": t["name"], "seed": "",
                          "group": "workbench", "start": s, "end": e,
                          "kind": "task", "best": None, "gpu": None})
    items.sort(key=lambda x: x["start"])
    return {"items": items}


# ---------------- papers ----------------
CCF_STOP = {"ieee", "acm", "the", "of", "on", "for", "and", "in", "international",
            "conference", "journal", "symposium", "transactions", "workshop",
            "proceedings", "annual", "meeting", "association", "computing", "computers",
            "systems", "science", "research", "letters", "review", "a", "an"}
CCF_TOK_RE = re.compile(r"[a-z0-9&+]+")
CCF_ALIAS = {"kdd": "sigkdd", "nips": "neurips"}  # common aliases -> catalog acronym
JOUR_HINT = {"transactions", "journal", "letters", "期刊", "杂志", "trans"}
CONF_HINT = {"conference", "symposium", "meeting", "workshop", "proceedings", "会议"}


def _ccf_tokens(s):
    toks = set()
    for t in CCF_TOK_RE.findall((s or "").lower()):
        if t in CCF_STOP or (len(t) == 4 and t.isdigit()):
            continue
        toks.add(CCF_ALIAS.get(t, t))
    # keep CJK words (期刊/会议 hints) separately
    return toks


try:
    with open(os.path.join(ROOT, "static", "ccf.json"), encoding="utf-8") as _f:
        CCF_ENTRIES = []
        # Short catalog names that are plain English words — never treat as acronyms
        _FALSE_ACR = {
            "models", "networks", "image", "information", "performance", "integration",
            "computational", "computer", "systems", "software", "security", "mobile",
            "multimedia", "parallel", "distributed", "internet", "cluster", "cloud",
            "pattern", "machine", "learning", "vision", "graphics", "database",
            "science", "sciences", "journal", "letters", "transactions", "review",
            "computing", "communication", "communications", "processing", "analysis",
            "engineering", "technology", "applications", "intelligence", "knowledge",
        }
        for e in json.load(_f):
            s_raw = (e.get("s") or "").strip()
            s = s_raw.lower()
            words = [w for w in CCF_TOK_RE.findall(s) if w not in CCF_STOP]
            acr = CCF_ALIAS.get(words[0], words[0]) if words else ""
            full = _ccf_tokens(e.get("f") or "") - {acr}
            fd = e.get("fd") or ""
            sd = re.sub(r"\W", "", s)  # e.g. 'ACMMM' for 'ACM MM' queries
            # Real short acronyms (CVPR/NeurIPS/ACM MM); reject long titles & English words
            acr_ok = (
                bool(acr)
                and 3 <= len(acr) <= 10
                and len(s_raw) <= 14
                and acr not in _FALSE_ACR
                and not (len(acr) >= 6 and acr.isalpha() and acr.isascii() and acr.islower() is False and False)
                and acr not in CCF_STOP
            )
            # Extra: if short name is a single English-looking word (>=6 letters) reject
            if acr_ok and len(words) == 1 and len(acr) >= 6 and acr in _FALSE_ACR:
                acr_ok = False
            if acr_ok and len(words) == 1 and len(acr) >= 6 and acr.isalpha():
                # models/networks/image already blocked; also block other long single words
                # unless they have internal capitals in original (NeurIPS, MoDELS→models blocked)
                if s_raw.isalpha() and s_raw.lower() == s_raw:
                    acr_ok = False  # e.g. "Networks"
            CCF_ENTRIES.append((e["r"], e["t"], acr, full, fd, sd, e, acr_ok))
except (OSError, ValueError):
    CCF_ENTRIES = []


def ccf_rank(venue):
    """Match a venue string (e.g. 'ICML 2024' or 'IEEE TPAMI') against the CCF
    catalog (2026 第七版, despaced full names). Returns the entry dict or None."""
    if not venue or not CCF_ENTRIES:
        return None
    low = venue.lower()
    q = {CCF_ALIAS.get(t, t) for t in _ccf_tokens(low)}
    qd = re.sub(r"\W", "", low)
    if not q and not qd:
        return None
    prefer_jour = any(h in low for h in ("transactions", "journal", "letters", "期刊", "杂志"))
    prefer_conf = any(h in low for h in ("conference", "symposium", "meeting", "workshop", "会议"))
    cands = []
    for rank, typ, acr, full, fd, sd, detail, acr_ok in CCF_ENTRIES:
        if (acr and acr in q) or (full and full <= q) \
                or (sd and len(sd) >= 3 and sd in qd) \
                or (fd and len(fd) >= 12 and fd in qd):
            cands.append((rank, typ, detail))
    if not cands:
        return None
    order = {"A": 0, "B": 1, "C": 2}

    def key(c):
        pref = 0
        if prefer_jour:
            pref = 0 if c[1] == "期刊" else 1
        elif prefer_conf:
            pref = 0 if c[1] == "会议" else 1
        return (pref, order[c[0]])

    return min(cands, key=key)[2]


_PREPRINT_RE = re.compile(
    r"^(arxiv|biorxiv|medrxiv|preprint|未在论文中找到)(\b|$)", re.I)


def ccf_rank_paper(venue="", raw_text="", title=""):
    """CCF match from venue string.

    If venue is clearly a preprint (arXiv etc.), return None — UI should just
    show「arXiv」, not invent a CCF badge from citations in the body text.
    """
    venue = (venue or "").strip()
    if not venue or _PREPRINT_RE.match(venue) or "arxiv.org" in venue.lower():
        return None
    return ccf_rank(venue)


def _paper_ccf(row):
    """Compute CCF badge for a papers row (uses card venue + raw_text fallback)."""
    venue = title = ""
    card = row.get("card_json")
    if card:
        try:
            info = (json.loads(card).get("文献信息") or {})
            venue = info.get("期刊或会议") or ""
            title = info.get("标题") or ""
        except (ValueError, TypeError):
            pass
    title = title or (row.get("title") or "")
    return ccf_rank_paper(venue, row.get("raw_text") or "", title)


def _sanitize_text(s: str) -> str:
    """Strip UTF-16 surrogates that pypdf sometimes emits (math glyphs etc.).
    SQLite/utf-8 rejects them and would 500 on INSERT. Also strip NUL — SQLite
    TEXT length()/C API truncates at the first \\0."""
    if not s:
        return s
    s = s.replace("\x00", "")
    return s.encode("utf-8", "surrogatepass").decode("utf-8", "replace")


PAPERS_PDF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "papers_pdf")


def _ensure_pdf_dir():
    os.makedirs(PAPERS_PDF_DIR, exist_ok=True)


def _pdf_store_path(paper_id: int) -> str:
    return os.path.join(PAPERS_PDF_DIR, f"{int(paper_id)}.pdf")


def _save_paper_pdf(paper_id: int, data: bytes) -> str:
    _ensure_pdf_dir()
    path = _pdf_store_path(paper_id)
    with open(path, "wb") as f:
        f.write(data)
    rel = f"papers_pdf/{int(paper_id)}.pdf"
    db.execute("UPDATE papers SET pdf_file=?, updated_at=? WHERE id=?",
               (rel, now(), paper_id))
    return rel


def _extract_pdf_bytes(data: bytes) -> dict:
    import pdf_extract
    return pdf_extract.extract_pdf(data)


@app.post("/api/papers/pdf")
async def create_paper_pdf(file: bytes = File(...), title: str = Form(""),
                           user=Depends(get_current_user)):
    """Create a paper record from a PDF upload (multipart field 'file')."""
    try:
        info = _extract_pdf_bytes(file)
        raw = info["raw_text"]
    except ImportError as e:
        raise HTTPException(400, f"PDF 依赖缺失: {e}；请改用粘贴文本或安装 pdfplumber/pypdf")
    except Exception as e:
        raise HTTPException(400, f"PDF 解析失败: {e}")
    if len((raw or "").strip()) < 200:
        raise HTTPException(400, "无法从 PDF 提取文本(可能是扫描版),请改用粘贴文本")
    t = _sanitize_text(title) or "上传的 PDF"
    cur = db.execute(
        "INSERT INTO papers(title, source_type, source_ref, raw_text, owner, saved, created_at, "
        "updated_at) VALUES(?,?,?,?,?,0,?,?)",
        (t, "pdf", t, raw[:200000], user["username"], now(), now()))
    pid = cur.lastrowid
    try:
        _save_paper_pdf(pid, file)
    except OSError as e:
        print(f"[pdf] store failed paper={pid}: {e}", flush=True)
    print(f"[pdf] paper={pid} tables={info.get('table_count')} "
          f"chars={info.get('chars')} engine={info.get('engine')}", flush=True)
    row = db.query_one("SELECT id,title,source_type,source_ref,owner,created_at,updated_at,"
                       "pdf_file FROM papers WHERE id=?", (pid,))
    return {
        **row,
        "pdf_tables": info.get("table_count", 0),
        "pdf_engine": info.get("engine"),
        "raw_chars": info.get("chars", len(raw)),
        "has_table_appendix": "PDF TABLES (machine-extracted" in (raw or ""),
        "ocr_used": bool(info.get("ocr_used")),
    }


@app.post("/api/papers/{paper_id}/reparse-pdf")
async def reparse_paper_pdf(paper_id: int,
                            file: UploadFile | None = File(None),
                            user=Depends(get_current_user)):
    """Re-extract text+tables from a newly uploaded PDF or the stored copy."""
    row = _get_paper_for(paper_id, user)
    data = None
    if file is not None and getattr(file, "filename", None):
        data = await file.read()
    if not data:
        rel = row.get("pdf_file") or ""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), rel) if rel else ""
        if not path or not os.path.isfile(path):
            path = _pdf_store_path(paper_id)
        if not os.path.isfile(path):
            raise HTTPException(400, "没有可重解析的 PDF，请重新上传该论文 PDF")
        with open(path, "rb") as f:
            data = f.read()
    try:
        info = _extract_pdf_bytes(data)
        raw = info["raw_text"]
    except Exception as e:
        raise HTTPException(400, f"PDF 解析失败: {e}")
    if len((raw or "").strip()) < 200:
        raise HTTPException(400, "无法从 PDF 提取文本")
    try:
        _save_paper_pdf(paper_id, data)
    except OSError as e:
        print(f"[pdf] store failed paper={paper_id}: {e}", flush=True)
    db.execute("UPDATE papers SET raw_text=?, source_type=?, updated_at=? WHERE id=?",
               (raw[:200000], "pdf", now(), paper_id))
    print(f"[pdf/reparse] paper={paper_id} tables={info.get('table_count')} "
          f"chars={info.get('chars')} engine={info.get('engine')}", flush=True)
    return {
        "ok": True,
        "id": paper_id,
        "pdf_tables": info.get("table_count", 0),
        "pdf_engine": info.get("engine"),
        "raw_chars": info.get("chars", len(raw)),
        "has_table_appendix": "PDF TABLES (machine-extracted" in (raw or ""),
        "ocr_used": bool(info.get("ocr_used")),
    }


class PaperIn(BaseModel):
    title: str = ""
    text: str = ""
    arxiv_url: str = ""


@app.post("/api/papers")
async def create_paper(p: PaperIn, user=Depends(get_current_user)):
    """Create a paper record from an arXiv link or pasted text (JSON body)."""
    if p.arxiv_url:
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#]+)", p.arxiv_url)
        aid = (m.group(1) if m else p.arxiv_url).strip("/")
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                r = await client.get(f"https://arxiv.org/abs/{aid}",
                                     headers={"User-Agent": "gpu-workbench/3.0"})
            r.raise_for_status()
        except Exception as e:
            raise HTTPException(400, f"arXiv 抓取失败({e}),请改用上传 PDF 或粘贴文本")
        html = r.text
        mt = re.search(r"<title>\[?[\d.]*\]?\s*(.*?)</title>", html, re.S)
        ab = re.search(r'<blockquote class="abstract[^"]*">\s*<span[^>]*>Abstract:</span>(.*?)</blockquote>', html, re.S)
        t = (mt.group(1).strip().replace("\n", " ") if mt else p.title or f"arXiv:{aid}")
        abstract = re.sub(r"<[^>]+>", " ", ab.group(1)).strip() if ab else ""
        raw = (f"标题: {t}\n\n摘要: {abstract}\n\n"
               f"(注: 仅抓取到摘要,完整内容请上传 PDF 或粘贴全文)")
        source_type, source_ref = "arxiv", aid
    elif p.text and len(p.text.strip()) >= 50:
        raw, source_type, source_ref = p.text, "text", ""
        t = p.title or p.text.strip().splitlines()[0][:80]
    else:
        raise HTTPException(400, "需要 arxiv_url 或 text(至少 50 字)")
    t, raw = _sanitize_text(t), _sanitize_text(raw)
    cur = db.execute(
        "INSERT INTO papers(title, source_type, source_ref, raw_text, owner, saved, created_at, "
        "updated_at) VALUES(?,?,?,?,?,0,?,?)",
        (t, source_type, source_ref, raw[:200000], user["username"], now(), now()))
    return db.query_one("SELECT id,title,source_type,source_ref,owner,created_at,updated_at "
                        "FROM papers WHERE id=?", (cur.lastrowid,))


@app.get("/api/papers")
async def list_papers(owner: str = "", saved: str = "", user=Depends(get_current_user)):
    where, params = _scoped_where(user, "papers")
    if is_adv(user) and owner:
        where, params = "WHERE owner=?", (owner,)
    if saved == "1":
        where = (where + " AND saved=1") if where else "WHERE saved=1"
    rows = db.query(f"SELECT id,title,source_type,source_ref,owner,saved,card_json,created_at,"
                    f"updated_at FROM papers {where} ORDER BY id DESC LIMIT 100", params)
    for r in rows:
        card = r.pop("card_json", None)
        r["extracted"] = bool(card and str(card).strip() not in ("", "{}", "null"))
        r["ccf"] = None
        r["venue"] = ""
        if card:
            try:
                info = (json.loads(card).get("文献信息") or {})
                venue = info.get("期刊或会议") or ""
                title = info.get("标题") or r.get("title") or ""
                r["venue"] = venue
                r["ccf"] = ccf_rank_paper(venue, "", title)
                if info.get("标题"):
                    r["title"] = info.get("标题")
            except (ValueError, TypeError):
                pass
    return rows


def _pdf_table_meta_from_raw(raw: str) -> dict:
    raw = raw or ""
    m = re.search(r"共检测到\s*(\d+)\s*张可用表格", raw)
    n = int(m.group(1)) if m else (raw.count("【PDF表格"))
    return {
        "pdf_tables": n,
        "ocr_used": "含 OCR" in raw or "【PDF表格OCR" in raw,
        "raw_chars": len(raw),
        "has_table_appendix": "PDF TABLES (machine-extracted" in raw,
    }


@app.get("/api/papers/{paper_id}")
async def get_paper(paper_id: int, user=Depends(get_current_user)):
    row = _get_paper_for(paper_id, user)
    row["ccf"] = _paper_ccf(row)
    row["venue"] = ""
    if row.get("card_json"):
        try:
            row["venue"] = ((json.loads(row["card_json"]).get("文献信息") or {})
                            .get("期刊或会议") or "")
        except (ValueError, TypeError):
            pass
    row.update(_pdf_table_meta_from_raw(row.get("raw_text") or ""))
    return row


@app.post("/api/papers/{paper_id}/save")
async def save_paper(paper_id: int, user=Depends(get_current_user)):
    _get_paper_for(paper_id, user)
    db.execute("UPDATE papers SET saved=1, updated_at=? WHERE id=?", (now(), paper_id))
    return {"ok": True}


class ExtractIn(BaseModel):
    sections: list[str] | None = None


class ExtractApplyIn(BaseModel):
    card: dict
    sections: list[str] | None = None


def _parse_extract_sections(request_data, query_params):
    sections = None
    if isinstance(request_data, dict):
        sections = request_data.get("sections")
    q = query_params.getlist("sections")
    if q and not sections:
        sections = q
    if isinstance(sections, str):
        sections = [sections]
    return sections


def _merge_and_save_paper_card(paper_id, row, card, sections=None):
    """Merge partial card into papers.card_json and return API payload."""
    merged = {}
    if row.get("card_json"):
        try:
            merged = json.loads(row["card_json"]) or {}
            if not isinstance(merged, dict):
                merged = {}
        except (ValueError, TypeError):
            merged = {}
    if not isinstance(card, dict):
        raise HTTPException(400, "card 必须是对象")
    # Only keep known extract sections from incoming partial
    clean = {k: card[k] for k in chat.EXTRACT_SECTION_ORDER if k in card}
    if not clean:
        raise HTTPException(400, "card 中没有可入库的抽取板块")
    merged.update(clean)
    card = merged
    real_title = (card.get("文献信息") or {}).get("标题") or ""
    if real_title and row["title"] != real_title:
        db.execute("UPDATE papers SET title=? WHERE id=?", (str(real_title)[:200], paper_id))
    db.execute("UPDATE papers SET card_json=?, updated_at=? WHERE id=?",
               (json.dumps(card, ensure_ascii=False), now(), paper_id))
    # Re-read for CCF (venue / title may have changed)
    row2 = db.query_one("SELECT * FROM papers WHERE id=?", (paper_id,)) or row
    ccf = _paper_ccf(row2)
    used = list(clean.keys()) if not sections else [
        k for k in (sections or []) if k in clean
    ]
    return {"card": card, "sections_used": used or list(clean.keys()), "ccf": ccf,
            "sections_requested": sections}


@app.post("/api/papers/{paper_id}/extract")
async def extract_paper_api(paper_id: int, request: Request,
                            user=Depends(get_current_user)):
    row = _get_paper_for(paper_id, user)
    sections = None
    ct = (request.headers.get("content-type") or "").lower()
    data = None
    if "json" in ct:
        try:
            data = await request.json()
        except Exception:
            data = None
    sections = _parse_extract_sections(data if isinstance(data, dict) else None,
                                       request.query_params)
    print(f"[extract] paper={paper_id} sections={sections!r} user={user['username']}", flush=True)
    try:
        card = await chat.extract_paper(row["raw_text"] or "", sections=sections)
    except chat.ChatError as e:
        raise HTTPException(400, str(e))
    return _merge_and_save_paper_card(paper_id, row, card, sections)


@app.post("/api/papers/{paper_id}/extract/prepare")
async def prepare_paper_extract(paper_id: int, request: Request,
                                user=Depends(get_current_user)):
    """Build extract messages for browser-direct LLM calls (no LLM on server)."""
    row = _get_paper_for(paper_id, user)
    data = None
    ct = (request.headers.get("content-type") or "").lower()
    if "json" in ct:
        try:
            data = await request.json()
        except Exception:
            data = None
    sections = _parse_extract_sections(data if isinstance(data, dict) else None,
                                       request.query_params)
    built = chat.build_extract_messages(row["raw_text"] or "", sections=sections)
    print(f"[extract/prepare] paper={paper_id} sections={built['sections']!r} "
          f"chars={built['excerpt_chars']} user={user['username']}", flush=True)
    return built


@app.post("/api/papers/{paper_id}/extract/apply")
async def apply_paper_extract(paper_id: int, body: ExtractApplyIn,
                              user=Depends(get_current_user)):
    """Merge client-side LLM extract result into paper card."""
    row = _get_paper_for(paper_id, user)
    print(f"[extract/apply] paper={paper_id} sections={body.sections!r} "
          f"user={user['username']}", flush=True)
    return _merge_and_save_paper_card(paper_id, row, body.card, body.sections)


TRANSLATE_SYSTEM = (
    "你是学术翻译。把用户给的 JSON 对象中每个键对应的值翻译成简体中文（保留数字、"
    "专有名词可括号附原文，人名保留英文原名）。输出**仅一个 JSON 对象**，键名与输入"
    "完全一致，不要输出任何 JSON 以外的文字。"
)


@app.post("/api/papers/{paper_id}/translate")
async def translate_paper_info(paper_id: int, user=Depends(get_current_user)):
    """Translate the 文献信息 section of a paper card to Chinese (cached in info_zh)."""
    row = _get_paper_for(paper_id, user)
    if row.get("info_zh"):
        return json.loads(row["info_zh"])
    if not row.get("card_json"):
        raise HTTPException(400, "请先完成论文调研抽取")
    info = (json.loads(row["card_json"]) or {}).get("文献信息")
    if not info:
        raise HTTPException(400, "调研卡中没有文献信息")
    try:
        reply = await chat.chat(
            [{"role": "user", "content": json.dumps(info, ensure_ascii=False)}],
            TRANSLATE_SYSTEM)
    except chat.ChatError as e:
        raise HTTPException(400, str(e))
    content = reply.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        zh = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(400, "翻译结果解析失败,请重试")
    if not isinstance(zh, dict):
        raise HTTPException(400, "翻译结果格式错误,请重试")
    db.execute("UPDATE papers SET info_zh=?, updated_at=? WHERE id=?",
               (json.dumps(zh, ensure_ascii=False), now(), paper_id))
    return zh


@app.delete("/api/papers/{paper_id}")
async def del_paper(paper_id: int, user=Depends(get_current_user)):
    _get_paper_for(paper_id, user)
    db.execute("DELETE FROM papers WHERE id=?", (paper_id,))
    return {"ok": True}


# ---------------- dir check ----------------
class DirIn(BaseModel):
    path: str = Field(min_length=1)


@app.post("/api/dircheck")
async def dircheck(d: DirIn, user=Depends(get_current_user)):
    p = os.path.realpath(os.path.expanduser(d.path))
    if not os.path.isdir(p):
        return {"exists": False, "path": p}
    writable = os.access(p, os.W_OK)
    try:
        entries = sorted(os.listdir(p))[:30]
    except OSError:
        entries = []
    junk = {"__pycache__", ".git", ".claude", ".idea", "node_modules"}
    items = [{"name": e, "dir": os.path.isdir(os.path.join(p, e))} for e in entries]
    items.sort(key=lambda i: (i["name"] in junk, not i["dir"], i["name"].lower()))
    has_code = any(e.endswith((".py", ".sh", ".yaml", ".yml", ".toml", ".json"))
                   or e in ("configs", "config", "src") for e in entries)
    return {"exists": True, "writable": writable, "path": p,
            "entries": entries, "items": items, "has_code": has_code}


# ---------------- settings ----------------
def _public_settings():
    raw = _settings_map()
    out = dict(raw)
    if out.get("feishu_webhook"):
        out["feishu_webhook"] = feishu.mask_webhook(out["feishu_webhook"])
    if out.get("feishu_secret"):
        out["feishu_secret"] = "••••"
    return out


@app.get("/api/settings")
async def get_settings(user=Depends(require_advisor)):
    return _public_settings()


@app.put("/api/settings")
async def put_settings(body: dict, user=Depends(require_advisor)):
    for k, v in body.items():
        key, value = str(k), str(v)
        if key == "feishu_webhook" and "••••" in value:
            continue
        if key == "feishu_secret" and value == "••••":
            continue
        if key == "feishu_webhook" and value and not feishu.webhook_ok(value):
            raise HTTPException(400, "Webhook 须是飞书或 Lark 自定义机器人地址")
        db.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    return {"ok": True}


@app.post("/api/feishu/test")
async def test_feishu(user=Depends(get_current_user)):
    """Send one test line with the saved webhook. Does not start a bot."""
    settings = _settings_map()
    webhook = (settings.get("feishu_webhook") or "").strip()
    if not webhook:
        raise HTTPException(400, "请先填写并保存飞书 Webhook")
    try:
        await feishu.send_text(
            webhook, "科研工作台测试：飞书通知已接通。", settings.get("feishu_secret") or "")
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "message": "已发到飞书"}


class FeishuIn(BaseModel):
    feishu_webhook: str = ""
    feishu_secret: str = ""
    notify_run: str = "1"
    notify_round: str = "1"
    notify_node: str = "1"


def _save_setting(key: str, value: str):
    db.execute("INSERT INTO settings(key,value) VALUES(?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


@app.get("/api/feishu")
async def get_feishu(user=Depends(get_current_user)):
    raw = _settings_map()
    return {
        "feishu_webhook": feishu.mask_webhook(raw.get("feishu_webhook") or ""),
        "feishu_secret": "••••" if (raw.get("feishu_secret") or "").strip() else "",
        "notify_run": raw.get("notify_run", "1"),
        "notify_round": raw.get("notify_round", "1"),
        "notify_node": raw.get("notify_node", "1"),
        "configured": bool((raw.get("feishu_webhook") or "").strip()),
    }


@app.put("/api/feishu")
async def put_feishu(body: FeishuIn, user=Depends(get_current_user)):
    webhook = body.feishu_webhook.strip()
    secret = body.feishu_secret.strip()
    if "••••" not in webhook:
        if webhook and not feishu.webhook_ok(webhook):
            raise HTTPException(400, "Webhook 须是飞书或 Lark 自定义机器人地址")
        _save_setting("feishu_webhook", webhook)
    if secret != "••••":
        _save_setting("feishu_secret", secret)
    _save_setting("notify_run", "0" if body.notify_run == "0" else "1")
    _save_setting("notify_round", "0" if body.notify_round == "0" else "1")
    _save_setting("notify_node", "0" if body.notify_node == "0" else "1")
    return {"ok": True}


# ---------------- comparison & report ----------------
class CompareIn(BaseModel):
    paper_id: int
    jsonl: str = ""
    log: str = ""


DEFAULT_THRESH = {"reached_pct": "5", "near_pct": "15"}


@app.post("/api/compare")
async def compare_runs(c: CompareIn, user=Depends(get_current_user)):
    prow = _get_paper_for(c.paper_id, user)
    row = db.query_one("SELECT card_json FROM papers WHERE id=?", (c.paper_id,))
    if not row or not row["card_json"]:
        raise HTTPException(400, "请先完成论文调研抽取")
    card = json.loads(row["card_json"])
    targets = ((card.get("复现要点") or {}).get("对照指标")) or []
    if not targets:
        raise HTTPException(400, "调研卡中没有对照指标,请重新抽取")
    scores = {}
    if c.log and os.path.isfile(c.log):
        scores = exp.parse_scores(c.log)
    elif c.jsonl and os.path.isfile(c.jsonl):
        recs, _ = exp.parse_jsonl(c.jsonl, limit=10**9, scope="epoch")
        if recs:
            scores = {"vl": {"best": min(r["task"] for r in recs if "task" in r)}}
    settings_kv = _settings_map()
    reached = float(settings_kv.get("reached_pct", DEFAULT_THRESH["reached_pct"]))
    near = float(settings_kv.get("near_pct", DEFAULT_THRESH["near_pct"]))

    def pick_score(target: dict):
        """Use this paper's own 指标名/设定 to pick a log series — no fixed protocol template."""
        hint = " ".join(str(target.get(k) or "") for k in ("指标名", "设定", "说明", "论文表号"))
        series = exp.pick_score_series(scores, hint)
        higher = target.get("越高越好")
        if higher is None:
            higher = True
        return exp.series_best(series, bool(higher))

    rows, verdicts = [], []
    for t in targets:
        try:
            claimed = float(t.get("数值"))
        except (TypeError, ValueError):
            continue
        ours = pick_score(t)
        if ours is None:
            rows.append({"指标": t.get("指标名"), "表号": t.get("论文表号"),
                         "论文值": claimed, "本次最优": None, "差值": None, "判定": "无本次数据"})
            continue
        diff = ours - claimed
        rel = abs(diff) / max(abs(claimed), 1e-9) * 100
        if rel <= reached:
            v = "达到"
        elif rel <= near:
            v = "接近"
        else:
            v = "未达到"
        verdicts.append(v)
        rows.append({"指标": t.get("指标名"), "表号": t.get("论文表号"),
                     "论文值": claimed, "本次最优": round(ours, 4),
                     "差值": round(diff, 4), "相对误差%": round(rel, 2), "判定": v})
    if not verdicts:
        conclusion = "未复现"
    elif all(v == "达到" for v in verdicts):
        conclusion = "复现成功"
    elif any(v != "未达到" for v in verdicts):
        conclusion = "部分复现"
    else:
        conclusion = "未复现"
    return {"rows": rows, "conclusion": conclusion,
            "thresholds": {"reached_pct": reached, "near_pct": near}}


@app.get("/api/papers/{paper_id}/report")
async def paper_report(paper_id: int, jsonl: str = "", log: str = "",
                       user=Depends(get_current_user)):
    row = _get_paper_for(paper_id, user)
    cmp_res = await compare_runs(CompareIn(paper_id=paper_id, jsonl=jsonl, log=log), user)
    card = json.loads(row["card_json"]) if row["card_json"] else {}
    lines = [f"# 复现报告 · {row['title']}", "",
             f"- 论文来源: {row['source_type']} {row['source_ref'] or ''}",
             f"- 生成时间: {time_str()}", ""]
    info = card.get("文献信息") or {}
    lines += [f"## 论文: {info.get('标题', row['title'])}", "",
              f"- 作者: {info.get('作者', '未在论文中找到')}",
              f"- 发表: {info.get('期刊或会议', '')} {info.get('发表日期', '')}", ""]
    lines += ["## 指标对照", "", "| 指标 | 表号 | 论文值 | 本次最优 | 差值 | 相对误差% | 判定 |",
              "|---|---|---|---|---|---|---|"]
    for r in cmp_res["rows"]:
        lines.append(f"| {r['指标']} | {r['表号']} | {r['论文值']} | {r['本次最优']} | "
                     f"{r['差值']} | {r.get('相对误差%', '')} | {r['判定']} |")
    lines += ["", f"## 结论: {cmp_res['conclusion']}", ""]
    if log and os.path.isfile(log):
        sv = exp.pick_score_series(exp.parse_scores(log))
        if sv and sv.get("values"):
            hist = ", ".join(f"r{a}:{b:.2f}" for a, b in zip(sv["rounds"], sv["values"]))
            lines += [f"本次评测得分[{sv.get('name') or 'log'}] 逐轮: {hist} (最佳 {sv['best']:.2f})", ""]
    if jsonl and os.path.isfile(jsonl):
        recs, _ = exp.parse_jsonl(jsonl, limit=10**9, scope="epoch")
        if recs:
            lines += ["### 最新损失摘要", "", exp.summarize(recs, 30), ""]
    content = "\n".join(lines)
    return StreamingResponse(iter([content]), media_type="text/markdown",
                             headers={"Content-Disposition":
                                      f"attachment; filename=report_paper{paper_id}.md"})


def time_str():
    import time as _t
    return _t.strftime("%Y-%m-%d %H:%M:%S")


@app.get("/api/config")
async def get_config(user=Depends(get_current_user)):
    return {"chat_configured": chat.load_config() is not None, "mock": monitor.mock,
            **chat.public_config()}


@app.get("/api/chat-config")
async def get_chat_config(user=Depends(get_current_user)):
    return chat.public_config()


class ChatConfigIn(BaseModel):
    base_url: str = ""
    model: str = ""
    api_key: str = ""


@app.put("/api/chat-config")
async def put_chat_config(body: ChatConfigIn, user=Depends(get_current_user)):
    try:
        return chat.save_config(body.base_url, body.model, body.api_key)
    except chat.ChatError as e:
        raise HTTPException(400, str(e))


@app.post("/api/chat-config/test")
async def test_chat_config(body: ChatConfigIn, user=Depends(get_current_user)):
    """Probe the LLM endpoint with a tiny ping (does not save config)."""
    try:
        return await chat.test_connection(body.base_url, body.model, body.api_key)
    except chat.ChatError as e:
        raise HTTPException(400, str(e))


# ---------------- agent ----------------
def _agent_stop(user, task_id):
    task_id = int(task_id)
    if task_id < 0:
        pid = -task_id
        try:
            uid = os.stat(f"/proc/{pid}").st_uid
        except OSError:
            raise ValueError("进程不存在")
        if uid != os.getuid():
            raise ValueError("不是本机用户启动的进程，不能停止")
        if not _proc_owner_allowed(pid, user):
            raise ValueError("无权停止该进程")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as e:
            raise ValueError(str(e))
        return {"task_id": task_id, "pid": pid, "status": "killed", "source": "external"}
    try:
        row = _get_task_for(task_id, user)
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, str) else "任务不存在"
        raise ValueError(detail)
    if row["status"] == "running":
        scheduler.stop(int(task_id))
        return {"task_id": int(task_id), "name": row["name"], "status": "killed"}
    if row["status"] == "pending":
        scheduler.cancel(int(task_id))
        return {"task_id": int(task_id), "name": row["name"], "status": "cancelled"}
    raise ValueError(f"任务状态是 {row['status']}，不能停止")


def _agent_submit(user, spec):
    cur = db.execute(
        "INSERT INTO tasks(name, command, gpus_needed, workdir, gpus_pinned, owner, created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (spec["name"], spec["command"], spec["gpus_needed"], spec.get("workdir"),
         spec.get("gpus_pinned"), user["username"], now()),
    )
    row = db.query_one("SELECT id, name, status, command, gpus_needed, workdir, owner "
                       "FROM tasks WHERE id=?", (cur.lastrowid,))
    return row


def _agent_ctx(user, session_id=None):
    return ToolCtx(
        user=user,
        db=db,
        snapshot=monitor.snapshot,
        path_allowed=lambda path: _path_allowed(path, user),
        proc_allowed=lambda pid: _proc_owner_allowed(pid, user),
        best_log=_best_log_for_pid,
        session_id=session_id,
        stop_task=lambda task_id: _agent_stop(user, task_id),
        submit_task=lambda spec: _agent_submit(user, spec),
        enabled_skills=agent_enabled_skills(db, user["username"]),
        external_tasks=lambda: _list_external_tasks(user),
    )


mount_agent(
    app,
    db=db,
    get_current_user=get_current_user,
    resolve_token=resolve_token,
    ctx_factory=_agent_ctx,
)

from bridge.routes import mount_pi

mount_pi(app, ROOT, resolve_token, ctx_factory=_agent_ctx)


# ---------------- frontend ----------------
app.mount("/static", StaticFiles(directory=os.path.join(ROOT, "static")), name="static")


@app.get("/")
async def index():
    return FileResponse(
        os.path.join(ROOT, "static", "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("WORKBENCH_HOST", "0.0.0.0")
    port = int(os.environ.get("WORKBENCH_PORT", "8787"))
    uvicorn.run(app, host=host, port=port)
