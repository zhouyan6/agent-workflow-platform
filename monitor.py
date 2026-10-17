"""GPU monitor: polls nvidia-smi every 2s, caches state in memory.

Set WORKBENCH_MOCK=1 to run without GPUs (local development).
Idle rule: util < IDLE_UTIL and mem_used < IDLE_MEM_MB (no process using the card).
"""
import asyncio
import os
import random
import subprocess
import time

IDLE_UTIL = 10.0      # % below this counts as idle
IDLE_MEM_MB = 2000.0  # MB below this counts as idle
# shared cards: someone holds a little memory but util is low and plenty spare
SHARED_SPARE_MB = float(os.environ.get("WORKBENCH_SHARED_SPARE", "12000"))
POLL_INTERVAL = 2.0

GPU_QUERY = ("index,uuid,utilization.gpu,memory.used,memory.total,temperature.gpu")
APPS_QUERY = ("gpu_uuid,pid,process_name,used_memory")


def _run_smi(query, extra=None):
    r = subprocess.run(
        ["nvidia-smi", f"--query-{query}", "--format=csv,noheader,nounits"] + (extra or []),
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "nvidia-smi failed")
    return r.stdout


def _parse_float(s, default=0.0):
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


class GPUMonitor:
    task_pids_provider = None  # set by server: returns pids of running workbench tasks

    def __init__(self):
        self.mock = os.environ.get("WORKBENCH_MOCK", "") == "1"
        self.gpus = []      # [{index, uuid, util, mem_used, mem_total, procs:[{pid,name,mem}]}]
        self.error = None
        self.ts = 0.0
        if self.mock:
            self._mock_state = [
                {"util": random.uniform(0, 5), "mem": random.uniform(200, 800)}
                for _ in range(8)
            ]

    # ---------- real ----------
    def _poll_real(self):
        try:
            gpus = self._query_bulk()
        except Exception:
            # one or more GPUs fell off the bus: query each card individually
            gpus = self._query_per_gpu()
        by_uuid = {g["uuid"]: g for g in gpus}
        try:
            apps_out = _run_smi("compute-apps=" + APPS_QUERY)
            for line in apps_out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 4:
                    continue
                uuid, pid, name, mem = parts[0], parts[1], ",".join(parts[2:-1]), parts[-1]
                g = by_uuid.get(uuid)
                if g:
                    g["procs"].append({"pid": int(pid), "name": name, "mem": _parse_float(mem)})
        except Exception:
            pass  # no compute apps / query unsupported
        GPUMonitor._enrich_procs(gpus)
        return gpus

    @staticmethod
    def _enrich_procs(gpus):
        """Add process owner + full command line (what experiment) via /proc."""
        if not os.path.isdir("/proc"):
            return
        import pwd
        task_pids = set()
        provider = GPUMonitor.task_pids_provider
        if provider:
            try:
                task_pids = {pid for pid in provider() if pid}
            except Exception:
                pass
        try:
            with open("/proc/uptime") as f:
                uptime = float(f.read().split()[0])
        except OSError:
            uptime = 0.0
        for g in gpus:
            for p in g["procs"]:
                pid = p["pid"]
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
                    p["cmd"] = cmd[:200]
                except OSError:
                    p["cmd"] = p.get("name", "")
                try:
                    p["user"] = pwd.getpwuid(os.stat(f"/proc/{pid}").st_uid).pw_name
                except (OSError, KeyError):
                    p["user"] = "?"
                try:
                    with open(f"/proc/{pid}/stat") as f:
                        stat = f.read().rsplit(")", 1)[1].split()
                    pgrp = int(stat[2])
                    start_ticks = float(stat[19])
                    p["run"] = max(0, int(uptime - start_ticks / 100))
                    p["wb"] = pgrp in task_pids  # belongs to a workbench task
                except (OSError, ValueError, IndexError):
                    p["run"] = None
                    p["wb"] = False

    @staticmethod
    def _query_bulk():
        gpu_out = _run_smi("gpu=" + GPU_QUERY)
        gpus = []
        for line in gpu_out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            gpus.append({
                "index": int(parts[0]),
                "uuid": parts[1],
                "util": _parse_float(parts[2]),
                "mem_used": _parse_float(parts[3]),
                "mem_total": _parse_float(parts[4]),
                "temp": _parse_float(parts[5]) if len(parts) > 5 and parts[5] else None,
                "procs": [],
            })
        if not gpus:
            raise RuntimeError("empty gpu list")
        return gpus

    @staticmethod
    def _query_per_gpu():
        """Fallback when bulk query fails: some GPUs may be broken (NVML errors)."""
        gpus = []
        try:
            listing = subprocess.run(
                ["nvidia-smi", "-L"], capture_output=True, text=True, timeout=10,
            ).stdout
        except Exception:
            return []
        for line in listing.strip().splitlines():
            if "Unable to determine" in line:
                continue
            try:
                head, uuid = line.strip().split(" (UUID: ")
                index = int(head.split()[1].rstrip(":"))
                uuid = uuid.rstrip(")")
            except (ValueError, IndexError):
                continue
            try:
                out = _run_smi("gpu=" + GPU_QUERY, extra=[f"--id={index}"])
                parts = [p.strip() for p in out.strip().splitlines()[0].split(",")]
                g = {
                    "index": index, "uuid": uuid,
                    "util": _parse_float(parts[2] if len(parts) > 2 else "0"),
                    "mem_used": _parse_float(parts[3] if len(parts) > 3 else "0"),
                    "mem_total": _parse_float(parts[4] if len(parts) > 4 else "24576"),
                    "temp": _parse_float(parts[5]) if len(parts) > 5 and parts[5] else None,
                    "procs": [],
                }
                try:  # per-GPU compute apps (bulk query fails when a card is broken)
                    apps = _run_smi("compute-apps=pid,process_name,used_memory",
                                    extra=[f"--id={index}"])
                    for ln in apps.strip().splitlines():
                        ap = [p.strip() for p in ln.split(",")]
                        if len(ap) >= 3 and ap[0].isdigit():
                            g["procs"].append({
                                "pid": int(ap[0]), "name": ap[1],
                                "mem": _parse_float(ap[-1]),
                            })
                except Exception:
                    pass
                gpus.append(g)
            except Exception:
                # broken card: report as present but unusable (treated as busy)
                gpus.append({
                    "index": index, "uuid": uuid, "error": True,
                    "util": 100.0, "mem_used": 1e9, "mem_total": 1e9, "procs": [],
                })
        gpus.sort(key=lambda g: g["index"])
        # cards missing from -L output entirely (fell off the bus) -> placeholder
        if gpus:
            for index in range(gpus[-1]["index"] + 1):
                if not any(g["index"] == index for g in gpus):
                    gpus.append({
                        "index": index, "uuid": f"MISSING-{index}", "error": True,
                        "util": 100.0, "mem_used": 1e9, "mem_total": 1e9, "procs": [],
                    })
            gpus.sort(key=lambda g: g["index"])
        return gpus

    # ---------- mock ----------
    def _poll_mock(self):
        # keep two cards busy-ish so queue behaviour is visible, others drift idle
        for i, st in enumerate(self._mock_state):
            busy = i in (6, 7)
            base_u, base_m = (55, 40000) if busy else (2, 500)
            st["util"] = min(100, max(0, st["util"] * 0.6 + base_u * 0.4 + random.uniform(-4, 4)))
            st["mem"] = min(81920, max(200, st["mem"] * 0.6 + base_m * 0.4 + random.uniform(-150, 150)))
        gpus = []
        for i, st in enumerate(self._mock_state):
            procs = []
            if st["mem"] > 3000:
                procs.append({"pid": 40000 + i, "name": "/home/niexin/python train.py",
                              "mem": st["mem"], "user": "niexin",
                              "cmd": "python train.py --diffusion --ndev 1"})
            gpus.append({
                "index": i, "uuid": f"MOCK-{i}", "util": round(st["util"], 1),
                "mem_used": round(st["mem"], 0), "mem_total": 81920, "procs": procs,
            })
        return gpus

    # ---------- api ----------
    def poll(self):
        try:
            self.gpus = self._poll_mock() if self.mock else self._poll_real()
            self.error = None
        except Exception as e:
            self.error = str(e)
        self.ts = time.time()

    @staticmethod
    def is_idle(gpu):
        return gpu["util"] < IDLE_UTIL and gpu["mem_used"] < IDLE_MEM_MB

    @staticmethod
    def is_assignable(gpu):
        """Cards the scheduler may use: fully idle, or lightly-shared with enough spare."""
        if gpu.get("error"):
            return False
        if GPUMonitor.is_idle(gpu):
            return True
        return gpu["util"] < IDLE_UTIL and (gpu["mem_total"] - gpu["mem_used"]) >= SHARED_SPARE_MB

    def snapshot(self):
        import getpass
        return {
            "ts": self.ts,
            "error": self.error,
            "mock": self.mock,
            "me": getpass.getuser(),
            "idle_rule": {"util": IDLE_UTIL, "mem_mb": IDLE_MEM_MB,
                          "shared_spare_mb": SHARED_SPARE_MB},
            "gpus": self.gpus,
        }

    async def loop(self):
        while True:
            await asyncio.to_thread(self.poll)
            await asyncio.sleep(POLL_INTERVAL)
