"""Feishu custom-bot webhook. Outbound only: the lab server posts out, no public inbound."""
import base64
import hashlib
import hmac
import time

import httpx

WEBHOOK_PREFIXES = (
    "https://open.feishu.cn/open-apis/bot/v2/hook/",
    "https://open.larksuite.com/open-apis/bot/v2/hook/",
)


def mask_webhook(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    head, _, tail = url.rpartition("/")
    if not head or not tail:
        return "••••"
    shown = tail[-4:] if len(tail) > 4 else ""
    return f"{head}/••••{shown}"


def webhook_ok(url: str) -> bool:
    url = (url or "").strip()
    return any(url.startswith(p) and len(url) > len(p) for p in WEBHOOK_PREFIXES)


def sign(secret: str, timestamp: int | None = None) -> tuple[str, str]:
    ts = str(int(time.time() if timestamp is None else timestamp))
    digest = hmac.new(f"{ts}\n{secret}".encode(), digestmod=hashlib.sha256).digest()
    return ts, base64.b64encode(digest).decode()


async def send_text(webhook: str, text: str, secret: str = "") -> dict:
    webhook = (webhook or "").strip()
    secret = (secret or "").strip()
    if not webhook:
        return {"ok": False, "skipped": "no webhook"}
    if not webhook_ok(webhook):
        raise RuntimeError("Webhook 须是飞书或 Lark 自定义机器人地址")
    body = {"msg_type": "text", "content": {"text": (text or "")[:8000]}}
    if secret:
        ts, sig = sign(secret)
        body["timestamp"] = ts
        body["sign"] = sig
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(webhook, json=body)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        raise RuntimeError(data.get("msg") or r.text[:200] or f"HTTP {r.status_code}")
    code = data.get("code", data.get("StatusCode", 0))
    if code not in (0, "0", None):
        raise RuntimeError(data.get("msg") or data.get("StatusMessage") or "飞书拒绝了这条消息")
    return {"ok": True}


def _settings(db) -> dict:
    return {r["key"]: r["value"] for r in db.query("SELECT key,value FROM settings")}


def _flag_on(settings: dict, key: str) -> bool:
    return settings.get(key, "1") != "0"


async def notify(db, kind: str, text: str) -> None:
    """kind is 'run', 'round', or 'node'. No-op when that switch is off."""
    settings = _settings(db)
    flag = {"run": "notify_run", "round": "notify_round", "node": "notify_node"}.get(kind)
    if not flag:
        return
    if not _flag_on(settings, flag):
        return
    webhook = (settings.get("feishu_webhook") or "").strip()
    if not webhook:
        return
    try:
        await send_text(webhook, text, settings.get("feishu_secret") or "")
    except Exception as e:
        print(f"[feishu] {kind} notify failed: {e}")


def run_text(row) -> str:
    label = {"done": "成功", "failed": "失败", "killed": "已停止"}.get(row["status"], row["status"])
    lines = [f"实验结束 · {label}", f"任务 #{row['id']} {row['name'] or ''}".strip()]
    if row["exit_code"] is not None:
        lines.append(f"退出码 {row['exit_code']}")
    if row["gpu_assigned"]:
        lines.append(f"GPU {row['gpu_assigned']}")
    if row["workdir"]:
        lines.append(str(row["workdir"]))
    return "\n".join(lines)


def node_text(sig) -> str:
    kind = sig[0]
    if kind == "ok":
        return "节点已恢复，GPU 查询正常。"
    if kind == "down":
        return f"节点异常\n无法查询 GPU：{sig[1]}"
    cards = ", ".join(str(i) for i in sig[1])
    return f"节点异常\n不可用 GPU：{cards}"


def round_text(event) -> str:
    """One plain-text card line for a finished communication round."""
    gpu = event.get("gpu")
    gpu_label = "?" if gpu is None or gpu == "" else str(gpu)
    rnd = int(event["round"])
    total = event.get("total")
    progress = f"Round {rnd}"
    if total:
        progress += f"  ({rnd + 1}/{int(total)})"
    lines = [f"实验轮次结束 · GPU {gpu_label}", progress, f"{event['metric']} = {float(event['score']):.4f}"]
    if event.get("output"):
        lines.append(str(event["output"]))
    if event.get("rate") not in (None, ""):
        lines.append(f"缺失率 {event['rate']}")
    if event.get("best") is not None:
        lines.append(f"当前最好 {float(event['best']):.4f} @ round {int(event['best_round'])}")
    return "\n".join(lines)
