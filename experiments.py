"""Experiment tracking: parse JSONL training logs into numeric series.

Generic parsing: each line is one JSON object; numeric values are collected,
nested dicts are flattened one level ("loss" -> {"total": 1.2} becomes loss.total).
"""
import json
import os
import re

TAIL_BYTES = 8 * 1024 * 1024  # read at most last 8MB per request


def _flatten(obj, prefix=""):
    out = {}
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[key] = float(v)
        elif isinstance(v, dict) and prefix == "":  # flatten one level only
            for k2, v2 in v.items():
                if isinstance(v2, bool):
                    continue
                if isinstance(v2, (int, float)):
                    out[f"{key}.{k2}"] = float(v2)
    return out


def parse_jsonl(path, limit=5000, mode=None, scope=None):
    """Return (records, error): records = list of flattened dicts, newest last.

    mode/scope: optional filters on the corresponding string fields
    (e.g. mode='v' for image-only epochs, scope='epoch' to skip round aggregates).
    """
    if not os.path.isfile(path):
        return [], f"file not found: {path}"
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > TAIL_BYTES:
                f.seek(-TAIL_BYTES, os.SEEK_END)
                f.readline()  # drop partial line
            raw = f.read()
    except OSError as e:
        return [], str(e)
    records = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if mode and obj.get("mode") != mode:
            continue
        if scope and obj.get("scope") != scope:
            continue
        flat = _flatten(obj)
        if flat:
            records.append(flat)
    if len(records) > limit:
        records = records[-limit:]
    return records, None


# ---- curated metric tiers for the fair DDPM experiments ----
import math

TIER2_FIELDS = ["diff_noise", "diff_condition_recon", "diff_condition_contrast", "diff",
                "gen_answer_i2t", "gen_answer_weighted"]
TIER3_FIELDS = ["grad_norm", "generator_i2t_grad_norm", "generator_grad_present",
                "amp_scale", "optimizer_step_skipped"]


def _parse_raw(path):
    records = []
    try:
        with open(path, "rb") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
    except OSError:
        pass
    return records


# Eval lines vary by paper/codebase. Keep it loose: Round/Epoch/Step + val|test|eval + Name[=] number.
EVAL_SCORE_RE = re.compile(
    r"(?:(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[^\n]*?)?"
    r"(?:Round|Epoch|epoch|Step|step)\s+(\d+)\s*:\s*"
    r"(?:val|test|eval)\s+"
    r"([A-Za-z][\w@.+-]*)"
    r"(?:\[(\w+)\])?"
    r"\s*=\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
)
SCORE_RE = EVAL_SCORE_RE  # backward name


def _append_score(scores, key, rnd, val, ts):
    if not key:
        return
    bucket = scores.setdefault(key, {"rounds": [], "values": [], "times": [], "name": key})
    if rnd in bucket["rounds"]:
        return
    bucket["rounds"].append(rnd)
    bucket["values"].append(val)
    bucket["times"].append(ts)


def parse_scores(log_path):
    """Parse per-round eval scores from a log. Not tied to one metric name."""
    from datetime import datetime
    scores = {}
    if not log_path or not os.path.isfile(log_path):
        return scores
    try:
        with open(log_path, "r", errors="replace") as f:
            for line in f:
                m = EVAL_SCORE_RE.search(line)
                if not m:
                    continue
                ts = None
                if m.group(1):
                    try:
                        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
                    except ValueError:
                        ts = None
                rnd = int(m.group(2))
                name = m.group(3)
                mode = m.group(4) or ""
                val = float(m.group(5))
                _append_score(scores, name, rnd, val, ts)
                if mode:
                    _append_score(scores, mode, rnd, val, ts)
                    _append_score(scores, f"{name}[{mode}]", rnd, val, ts)
    except OSError:
        pass
    for s in scores.values():
        vals = s["values"]
        if not vals:
            continue
        hi = max(vals)
        lo = min(vals)
        s.update({"first": vals[0], "latest": vals[-1],
                  "max": hi, "min": lo, "best": hi,
                  "best_round": s["rounds"][vals.index(hi)]})
    return scores


def pick_score_series(scores, hint=""):
    """Choose a log series for a paper metric by name overlap; else the richest eval series."""
    if not scores:
        return None
    hint = (hint or "").lower()
    keys = [k for k, s in scores.items() if s.get("values")]
    if not keys:
        return None
    tokens = re.findall(r"[a-z][a-z0-9]+", hint)
    best_key, best_n = None, -1
    for k in keys:
        kl = k.lower()
        n = 0
        if hint and kl in hint:
            n += 3
        for t in tokens:
            if len(t) >= 3 and (t in kl or kl in t):
                n += 1
        if n > best_n:
            best_n, best_key = n, k
    if best_n > 0:
        return scores[best_key]
    def rank(k):
        s = scores[k]
        npts = len(s.get("values") or [])
        kl = k.lower()
        look = ("acc", "score", "map", "fid", "psnr", "bleu", "auc", "f1", "recall", "prec")
        bonus = 2 if any(x in kl for x in look) else 0
        return (bonus, npts)
    return scores[max(keys, key=rank)]


def series_best(s, higher=True):
    if not s:
        return None
    if higher:
        return s.get("max", s.get("best"))
    return s.get("min")


def round_eta(log_path, total_rounds):
    """Estimate finish time from median per-round duration of score checkpoints."""
    scores = parse_scores(log_path)
    for s in scores.values():
        times = s.get("times") or []
        if len(times) < 2:
            continue
        diffs = [b - a for a, b in zip(times, times[1:]) if 60 < b - a < 86400]
        if not diffs:
            continue
        avg = sorted(diffs)[len(diffs) // 2]  # median, robust to pauses
        cur = s["rounds"][-1]
        remain = max(0, total_rounds - cur - 1)
        return {"round": cur, "total": total_rounds, "avg_round": avg,
                "remain": remain, "eta_sec": avg * remain}
    return None


def compute_metrics(path, log_path=None, recent=8):
    """Tiered metric summary from the newest epoch-level records."""
    records = [r for r in _parse_raw(path) if r.get("scope") == "epoch"] if path else []
    if not records:
        return {"error": "no epoch records", "tiers": [], "nan_count": 0,
                "round": 0, "records": 0, "scores": parse_scores(log_path)}

    def agg(rs, f):
        vals = [r[f] for r in rs
                if isinstance(r.get(f), (int, float)) and not isinstance(r[f], bool)
                and math.isfinite(r[f])]
        if not vals:
            return None
        head = sum(vals[:3]) / min(3, len(vals))          # initial level
        tail = vals[-recent:]
        latest = sum(tail[-3:]) / min(3, len(tail))       # current level
        prev = None
        if len(tail) > 3:
            prev = sum(tail[:-3]) / len(tail[:-3])
        return {"first": head, "latest": latest, "prev": prev,
                "min": min(vals), "max": max(vals), "n": len(vals)}

    def rows_for(mode, fields):
        rs = [r for r in records if not mode or r.get("mode") == mode]
        out = []
        for f in fields:
            a = agg(rs, f)
            if a:
                out.append({"field": f, "mode": mode or "-", **a})
        return out

    nan_count = 0
    for r in records[-recent:]:
        for v in r.values():
            if isinstance(v, float) and not math.isfinite(v):
                nan_count += 1

    tiers = [
        {"name": "① 实验有效性 (Table 1 / 3)", "rows":
            rows_for("vl", ["task", "total"]) + rows_for("v", ["task", "total"])
            + rows_for("l", ["task", "total"])},
        {"name": "② 扩散生成器", "rows": rows_for("vl", TIER2_FIELDS)},
        {"name": "③ 训练健康", "rows": rows_for(None, TIER3_FIELDS)},
        {"name": "④ 辅助观察", "rows": rows_for("vl", ["clip_fusion", "clip_fusion_weighted"])},
    ]
    rounds = max((r.get("round") or 0) for r in records)
    return {"tiers": [t for t in tiers if t["rows"]], "nan_count": nan_count,
            "round": rounds, "records": len(records),
            "scores": parse_scores(log_path)}


def series(records, fields=None):
    """Turn records into per-field value lists. Auto-pick top fields if none given."""
    if not records:
        return {"count": 0, "fields": [], "data": {}}
    all_fields = sorted({k for r in records for k in r})
    chosen = [f for f in fields if f in all_fields] if fields else all_fields[:12]
    data = {f: [r[f] for r in records if f in r] for f in chosen}
    return {"count": len(records), "fields": all_fields, "data": data}


def summarize(records, tail_n=50):
    """Compact stats of the last N records for AI analysis."""
    if not records:
        return "no data"
    tail = records[-tail_n:]
    lines = [f"total records: {len(records)}, summarizing last {len(tail)}:"]
    keys = sorted({k for r in tail for k in r})
    for k in keys[:40]:
        vals = [r[k] for r in tail if k in r]
        if len(vals) < 2:
            continue
        first, last = vals[0], vals[-1]
        lo, hi = min(vals), max(vals)
        lines.append(f"- {k}: last={last:.6g} (first={first:.6g}, min={lo:.6g}, max={hi:.6g})")
    return "\n".join(lines)
