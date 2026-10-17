"""Task scheduler: pending queue + GPU lock, launches commands with CUDA_VISIBLE_DEVICES.

Each task runs in its own process group (session) so it survives a backend restart
and can be killed as a whole via os.killpg.
"""
import asyncio
import glob
import os
import re
import signal
import subprocess
import time

from db import now

SCAN_INTERVAL = 5.0
IS_WIN = os.name == "nt"


def _pid_alive(pid):
    if not pid:
        return False
    try:
        if IS_WIN:
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            k32.CloseHandle(h)
            return True
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class Scheduler:
    def __init__(self, db, monitor, root):
        self.db = db
        self.monitor = monitor
        self.logs_dir = os.path.join(root, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)
        self.procs = {}  # task_id -> Popen (only tasks started by this process)
        self._pending_reviews = []
        self._reviewing = set()
        self._pending_notices = []  # task ids whose finish should be pushed to Feishu
        self._node_seen = None  # first sample is a baseline, not an alert
        self._round_files = {}  # log path -> offset / seen rounds

    # ---------- gpu bookkeeping ----------
    def locked_gpus(self):
        rows = self.db.query("SELECT gpu_assigned FROM tasks WHERE status='running'")
        locked = set()
        for r in rows:
            locked.update(int(x) for x in (r["gpu_assigned"] or "").split(",") if x != "")
        return locked

    def free_gpus(self):
        locked = self.locked_gpus()
        return [g["index"] for g in self.monitor.gpus
                if self.monitor.is_assignable(g) and g["index"] not in locked]

    # ---------- lifecycle ----------
    def _finish(self, task_id, status, exit_code=None, notify=True):
        self.procs.pop(task_id, None)
        self.db.execute(
            "UPDATE tasks SET status=?, finished_at=?, exit_code=? WHERE id=?",
            (status, now(), exit_code, task_id),
        )
        if notify and status in ("done", "failed", "killed"):
            self._pending_notices.append(task_id)
        if status in ("failed", "killed", "cancelled"):
            self._cancel_chain(task_id)

    def _cancel_chain(self, task_id):
        """A chain task died -> cancel everything waiting behind it (recursive)."""
        while True:
            nxt = self.db.query_one(
                "SELECT id FROM tasks WHERE after_task=? AND status='pending'", (task_id,))
            if not nxt:
                return
            self.db.execute(
                "UPDATE tasks SET status='cancelled', finished_at=? WHERE id=? AND status='pending'",
                (now(), nxt["id"]))
            task_id = nxt["id"]

    def _reap(self):
        """Move finished running tasks to done/failed."""
        for row in self.db.query("SELECT * FROM tasks WHERE status='running'"):
            tid = row["id"]
            popen = self.procs.get(tid)
            if popen is not None:
                rc = popen.poll()
                if rc is None:
                    continue
                self._finish(tid, "done" if rc == 0 else "failed", rc)
                self._pending_reviews.append(tid)
            else:
                # adopted task from a previous backend process
                if not _pid_alive(row["pid"]):
                    self._finish(tid, "done", None)
                    self._pending_reviews.append((tid, "done"))

    def _schedule(self):
        free = self.free_gpus()
        locked = self.locked_gpus()
        by_idx = {g["index"]: g for g in self.monitor.gpus}
        for row in self.db.query("SELECT * FROM tasks WHERE status='pending' ORDER BY id"):
            # chain gate: wait until the predecessor finished successfully
            if row["after_task"]:
                prev = self.db.query_one(
                    "SELECT status FROM tasks WHERE id=?", (row["after_task"],))
                if not prev or prev["status"] != "done":
                    continue
            # pinned cards: wait for exactly these, ignore other free cards
            if row["gpus_pinned"]:
                pins = [int(x) for x in str(row["gpus_pinned"]).split(",") if x != ""]
                if all(i in by_idx and i not in locked
                       and self.monitor.is_assignable(by_idx[i]) for i in pins):
                    self._start(row, pins)
                continue
            need = row["gpus_needed"]
            if len(free) < need:
                continue  # keep scanning later pending tasks (they may need fewer)
            cards, free = free[:need], free[need:]
            self._start(row, cards)

    def _start(self, row, cards):
        tid = row["id"]
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = ",".join(str(c) for c in cards)
        log_path = os.path.join(self.logs_dir, f"task_{tid}.log")
        try:
            os.makedirs(row["workdir"] or ".", exist_ok=True)
        except OSError:
            pass
        kwargs = dict(
            cwd=row["workdir"] or None, env=env,
            stdout=open(log_path, "wb"), stderr=subprocess.STDOUT,
        )
        if not IS_WIN:
            kwargs["start_new_session"] = True
        popen = subprocess.Popen(row["command"], shell=True, **kwargs)
        self.procs[tid] = popen
        self.db.execute(
            "UPDATE tasks SET status='running', gpu_assigned=?, pid=?, log_file=?, "
            "started_at=? WHERE id=?",
            (",".join(str(c) for c in cards), popen.pid, log_path, now(), tid),
        )

    # ---------- public ops ----------
    def stop(self, task_id):
        """Kill a running task (whole process group)."""
        row = self.db.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not row or row["status"] != "running":
            raise ValueError("task not running")
        popen = self.procs.get(task_id)
        try:
            if not IS_WIN and row["pid"]:
                os.killpg(os.getpgid(row["pid"]), signal.SIGTERM)
            elif popen is not None:
                popen.terminate()
            elif row["pid"]:
                os.kill(row["pid"], signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        if popen is not None:
            try:
                rc = popen.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if not IS_WIN:
                    try:
                        os.killpg(os.getpgid(row["pid"]), signal.SIGKILL)
                    except OSError:
                        pass
                else:
                    popen.kill()
                rc = popen.wait()
            self._finish(task_id, "killed", rc)
        else:
            self._finish(task_id, "killed", None)

    def cancel(self, task_id):
        self.db.execute(
            "UPDATE tasks SET status='cancelled', finished_at=? WHERE id=? AND status='pending'",
            (now(), task_id),
        )

    # ---------- AI review ----------
    async def _ai_review(self, task_id):
        self._reviewing.add(task_id)
        try:
            row = self.db.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
            if not row or not row["log_file"] or not os.path.isfile(row["log_file"]):
                return
            import chat as chat_mod
            if not chat_mod.load_config():
                return  # chat not configured
            with open(row["log_file"], "rb") as f:
                size = os.path.getsize(row["log_file"])
                f.seek(max(0, size - 64 * 1024))
                tail = f.read().decode("utf-8", "replace").splitlines()[-80:]
            duration = (row["finished_at"] or now()) - (row["started_at"] or now())
            sys_prompt = chat_mod.build_system_prompt(
                exp_name=f"工作台任务 #{task_id} {row['name']}",
                exp_summary=(f"exit_code={row['exit_code']} 运行时长={duration:.0f}s\n"
                             f"日志尾部:\n" + "\n".join(tail)),
            )
            question = ("用 3-6 句话复盘这个刚结束的实验任务:是否成功、关键指标走势、"
                        "有无异常(NaN/OOM/报错)、下一步建议。用中文简洁回答。")
            reply = await chat_mod.chat([{"role": "user", "content": question}], sys_prompt)
            self.db.execute("UPDATE tasks SET ai_summary=? WHERE id=?", (reply, task_id))
        except Exception as e:
            self.db.execute("UPDATE tasks SET ai_summary=? WHERE id=?",
                            (f"AI 复盘失败: {e}", task_id))
        finally:
            self._reviewing.discard(task_id)

    def _kick_reviews(self):
        while self._pending_reviews:
            tid = self._pending_reviews.pop(0)
            if tid not in self._reviewing:
                asyncio.create_task(self._ai_review(tid))

    def _node_signature(self):
        if self.monitor.error:
            return ("down", str(self.monitor.error)[:180])
        bad = tuple(g["index"] for g in self.monitor.gpus if g.get("error"))
        if bad:
            return ("bad", bad)
        return ("ok",)

    async def _notify_run(self, task_id):
        import feishu
        row = self.db.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if row:
            await feishu.notify(self.db, "run", feishu.run_text(row))

    async def _notify_node(self, sig):
        import feishu
        await feishu.notify(self.db, "node", feishu.node_text(sig))

    def _kick_feishu(self):
        while self._pending_notices:
            tid = self._pending_notices.pop(0)
            asyncio.create_task(self._notify_run(tid))
        sig = self._node_signature()
        if self._node_seen is None:
            self._node_seen = sig
            return
        if sig != self._node_seen:
            self._node_seen = sig
            asyncio.create_task(self._notify_node(sig))
        self._scan_rounds()

    # ---------- per-round Feishu ----------
    _ROUND_RE = re.compile(
        r"(?:Round|Epoch|epoch|Step|step)\s+(\d+)\s*:\s*"
        r"(?:val|test|eval)\s+"
        r"([A-Za-z][\w@.+\-]*)"
        r"(?:\[(\w+)\])?"
        r"\s*=\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
    )

    def _cmdline(self, pid):
        try:
            raw = open(f"/proc/{pid}/cmdline", "rb").read()
        except OSError:
            return ""
        return raw.replace(b"\x00", b" ").decode("utf-8", "replace")

    def _log_paths(self, pid):
        found = []
        for fd in glob.glob(f"/proc/{pid}/fd/*"):
            try:
                target = os.path.realpath(fd)
            except OSError:
                continue
            if target.endswith(".log") and os.path.isfile(target):
                found.append(target)
        return list(dict.fromkeys(found))

    def _arg(self, cmd, flag):
        match = re.search(rf"--{flag}(?:=|\s+)(\S+)", cmd or "")
        return match.group(1) if match else ""

    def _round_sources(self):
        sources = {}
        for gpu in self.monitor.gpus or []:
            for proc in gpu.get("procs") or []:
                pid = proc.get("pid")
                if not pid or proc.get("wb"):
                    continue
                cmd = self._cmdline(pid)
                meta = {
                    "gpu": gpu.get("index"),
                    "output": self._arg(cmd, "output_dir"),
                    "total": self._arg(cmd, "communication_rounds"),
                    "rate": self._arg(cmd, "modal_missing_rate"),
                }
                for path in self._log_paths(pid):
                    sources.setdefault(path, meta)
        for row in self.db.query(
            "SELECT log_file, gpu_assigned, command FROM tasks WHERE status='running'"
        ):
            path = row["log_file"]
            if not path or not os.path.isfile(path):
                continue
            cmd = row["command"] or ""
            gpu = (row["gpu_assigned"] or "").split(",")[0]
            sources.setdefault(os.path.realpath(path), {
                "gpu": gpu,
                "output": self._arg(cmd, "output_dir"),
                "total": self._arg(cmd, "communication_rounds"),
                "rate": self._arg(cmd, "modal_missing_rate"),
            })
        return sources

    def _fresh_rounds(self, path):
        state = self._round_files.get(path)
        first = state is None
        if first:
            state = {"offset": 0, "seen": set(), "best": None, "best_round": None}
            self._round_files[path] = state
        try:
            size = os.path.getsize(path)
        except OSError:
            return []
        if first:
            state["offset"] = size
            return []
        if size < state["offset"]:
            state["offset"] = 0
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                handle.seek(state["offset"])
                chunk = handle.read()
                state["offset"] = handle.tell()
        except OSError:
            return []
        events = []
        for line in chunk.splitlines():
            match = self._ROUND_RE.search(line)
            if not match:
                continue
            rnd = int(match.group(1))
            metric = match.group(2)
            mode = match.group(3) or ""
            score = float(match.group(4))
            label = f"{metric}[{mode}]" if mode else metric
            key = (rnd, label)
            if key in state["seen"]:
                continue
            state["seen"].add(key)
            if state["best"] is None or score > state["best"]:
                state["best"] = score
                state["best_round"] = rnd
            events.append({
                "round": rnd,
                "metric": label,
                "score": score,
                "best": state["best"],
                "best_round": state["best_round"],
            })
        return events

    def _scan_rounds(self):
        import feishu
        settings = feishu._settings(self.db)
        if not feishu._flag_on(settings, "notify_round"):
            return
        alive = set()
        for path, meta in self._round_sources().items():
            alive.add(path)
            for event in self._fresh_rounds(path):
                event.update(meta)
                asyncio.create_task(feishu.notify(self.db, "round", feishu.round_text(event)))
        for path in list(self._round_files):
            if path not in alive:
                self._round_files.pop(path, None)

    # ---------- loop ----------
    async def loop(self):
        # adopt running tasks started before a backend restart
        for row in self.db.query("SELECT * FROM tasks WHERE status='running'"):
            if not _pid_alive(row["pid"]):
                self._finish(row["id"], "killed", None, notify=False)
        while True:
            try:
                self._reap()
                self._schedule()
                self._kick_reviews()
                self._kick_feishu()
            except Exception as e:
                print(f"[scheduler] error: {e}")
            await asyncio.sleep(SCAN_INTERVAL)
