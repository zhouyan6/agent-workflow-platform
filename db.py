"""SQLite storage for tasks and experiments. No ORM, stdlib only."""
import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, "workbench.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  command TEXT NOT NULL,
  gpus_needed INTEGER NOT NULL,
  workdir TEXT DEFAULT NULL,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending/running/done/failed/killed/cancelled
  gpu_assigned TEXT,                       -- e.g. "0,3"
  pid INTEGER,
  log_file TEXT,
  created_at REAL,
  started_at REAL,
  finished_at REAL,
  exit_code INTEGER
);
CREATE TABLE IF NOT EXISTS experiments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  jsonl_path TEXT NOT NULL,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS papers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT,
  source_type TEXT,
  source_ref TEXT,
  raw_text TEXT,
  card_json TEXT,
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users(
  username TEXT PRIMARY KEY,
  role TEXT NOT NULL,                      -- advisor | student
  pw_hash TEXT NOT NULL,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  created_at REAL,
  expires_at REAL
);
CREATE TABLE IF NOT EXISTS agent_sessions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '新对话',
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS agent_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT,
  tool_name TEXT,
  tool_call_id TEXT,
  tool_calls_json TEXT,
  created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_session ON agent_messages(session_id);
CREATE TABLE IF NOT EXISTS agent_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL,
  owner TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  started_at REAL,
  finished_at REAL,
  error TEXT
);
CREATE TABLE IF NOT EXISTS agent_skills(
  name TEXT PRIMARY KEY,
  owner TEXT,
  kind TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  description TEXT
);
CREATE TABLE IF NOT EXISTS agent_jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 0,
  cron TEXT NOT NULL DEFAULT '0 22 * * *',
  timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
  last_run_at REAL,
  last_status TEXT,
  last_message TEXT,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS agent_cases(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner TEXT NOT NULL,
  title TEXT NOT NULL,
  experiment TEXT,
  symptom TEXT,
  cause TEXT,
  action TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  source_file TEXT,
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS agent_profiles(
  owner TEXT PRIMARY KEY,
  prompt TEXT,
  skills_json TEXT,
  updated_at REAL
);
"""

# 固定账号: 导师 niexin, 学生 zhouyan/lihan/jiyang。初始密码 = 用户名。
KNOWN_USERS = [("niexin", "advisor"), ("zhouyan", "student"),
               ("lihan", "student"), ("jiyang", "student")]

PBKDF2_ITER = 200000


def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), PBKDF2_ITER)
    return f"pbkdf2${PBKDF2_ITER}${salt}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, iters, salt, expect = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(),
                                 bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), expect)
    except (ValueError, TypeError):
        return False


def seed_users(db: "DB"):
    """Insert any missing known account with default password = username."""
    for username, role in KNOWN_USERS:
        if not db.query_one("SELECT 1 FROM users WHERE username=?", (username,)):
            db.execute("INSERT INTO users(username,role,pw_hash,created_at) VALUES(?,?,?,?)",
                       (username, role, hash_password(username), now()))


class DB:
    def __init__(self, path=DB_PATH):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        for col, decl in (("log_path", "TEXT"),):
            try:
                self._conn.execute(f"ALTER TABLE experiments ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass  # already exists
        for col, decl in (("gpus_pinned", "TEXT"), ("after_task", "INTEGER"),
                          ("ai_summary", "TEXT"), ("owner", "TEXT DEFAULT 'zhouyan'")):
            try:
                self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {decl}")
            except sqlite3.OperationalError:
                pass
        for table in ("experiments", "papers"):
            try:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN owner TEXT DEFAULT 'zhouyan'")
            except sqlite3.OperationalError:
                pass
        try:
            self._conn.execute("ALTER TABLE papers ADD COLUMN info_zh TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            # existing papers are treated as already in the library
            self._conn.execute("ALTER TABLE papers ADD COLUMN saved INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute("ALTER TABLE papers ADD COLUMN pdf_file TEXT")
        except sqlite3.OperationalError:
            pass
        self._conn.commit()

    def execute(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql, params=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None


def now():
    return time.time()
