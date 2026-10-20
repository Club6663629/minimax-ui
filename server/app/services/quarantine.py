"""毒丸任务隔离（Poison Guard）—— SAI 2026-10-14 落地，防 incident-retro-20261013 复发。

事故机理（详见 /data/memory/experts/general/knowledge/incident-retro-20261013.md）
    导演台长任务（多段 / 长时长）在 ComfyUI 上把宿主内存打爆 → kernel OOM 杀 python
    → worker 池按"失联 ≥30s"摘节点 → 任务 attempts < MAX_ATTEMPTS(2) 触发 _requeue_or_fail
    的**回退重投**（worker_url 清空 → 被重新领取 → 派到另一台节点）
    → 同一条毒任务把集群逐个打爆（10-12~10-13 复发 5 次，三台生成节点全灭）。

本模块提供三层隔离
    1) 熔断名单：长任务若在运行中打爆节点，则其签名进入名单，后续同名任务直接判失败；
    2) 跨节点重投禁令 + 节点粘滞：节点失联导致失败的任务**永不换节点重投**；
       节点仍存活时只允许在**同一个节点**重试（同一时刻只有一个节点承担风险）；
    3) 事件留痕：所有隔离动作写 poison_events.jsonl，供复盘与告警。

落盘（持久卷，服务重启不丢）
    <data_dir>/poison_tasks.json    熔断名单 + 节点粘滞表
    <data_dir>/poison_events.jsonl  隔离事件流（JSONL，append-only）

开关
    POISON_GUARD=1|0            总开关（默认 1=开启）
    POISON_GUARD_FILE=<path>    覆盖熔断名单路径（自测用）
    POISON_GUARD_EVENTS=<path>  覆盖事件流路径（自测用）
"""
import json
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LONG_TASK_SECONDS = 20    # "长任务"判定阈值：mode=director 或 duration >= 20s
PIN_TTL_SECONDS = 1800    # 节点粘滞有效期（30 分钟）
PIN_MAX_ATTEMPTS = 10     # 同节点重试等待上限（每次等待派发计一次）

try:  # 服务内运行：跟随 settings.data_dir
    from ..config import settings  # noqa
    _DEFAULT_DIR = Path(settings.data_dir).resolve()
except Exception:  # 独立自测：退回环境变量/默认路径
    _DEFAULT_DIR = Path(os.getenv("MINIMAX_DATA_DIR", "/data/workspace/minimax-ui/server/data"))


def enabled() -> bool:
    """总开关；POISON_GUARD=0/false 时整体关闭（回退到旧行为）。"""
    return os.getenv("POISON_GUARD", "1").strip().lower() not in ("0", "false", "no", "off")


def _store() -> Path:
    p = os.getenv("POISON_GUARD_FILE")
    return Path(p) if p else _DEFAULT_DIR / "poison_tasks.json"


def _events_path() -> Path:
    p = os.getenv("POISON_GUARD_EVENTS")
    return Path(p) if p else _DEFAULT_DIR / "poison_events.jsonl"


# ------------------------------------------------------------------ 签名
def signature(task) -> dict:
    """任务签名：模式 + 时长 + 分辨率 + 段数（段数仅作证据，不参与匹配）。"""
    mode = (getattr(task, "mode", "") or "").lower()
    try:
        duration = float(getattr(task, "duration", 0) or 0)
    except Exception:
        duration = 0.0
    resolution = getattr(task, "resolution", "") or ""
    segs = 0
    raw = getattr(task, "timeline_data", None)
    if raw:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict) and isinstance(data.get("segments"), list):
                segs = len(data["segments"])
        except Exception:
            segs = 0
    return {"mode": mode, "duration": duration, "resolution": resolution, "segments": segs}


def sig_key(sig: dict) -> str:
    """匹配键（粗粒度）：同一用户重投同一档长任务即命中熔断，不因分段微调而绕过。"""
    return f"{sig.get('mode')}|{sig.get('duration')}s|{sig.get('resolution')}"


def is_long(task) -> bool:
    """长任务（风险类）：导演台整卡长任务，或时长 >= LONG_TASK_SECONDS。"""
    mode = (getattr(task, "mode", "") or "").lower()
    try:
        duration = float(getattr(task, "duration", 0) or 0)
    except Exception:
        duration = 0.0
    return mode == "director" or duration >= LONG_TASK_SECONDS


# ------------------------------------------------------------------ 存取
def _load() -> dict:
    f = _store()
    try:
        if f.exists():
            data = json.loads(f.read_text())
            if isinstance(data, dict):
                data.setdefault("poison", [])
                data.setdefault("pins", {})
                return data
    except Exception:
        logger.warning("毒丸清单读取失败，按空处理: %s", f)
    return {"poison": [], "pins": {}}


def _save(data: dict) -> None:
    f = _store()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        tmp.replace(f)
    except Exception:
        logger.exception("毒丸清单写入失败: %s", f)


def _event(kind: str, **kwargs) -> None:
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind}
    rec.update({k: v for k, v in kwargs.items() if k is not None})
    try:
        f = _events_path()
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("毒丸事件写入失败")


# ------------------------------------------------------------------ 熔断名单
def is_poisoned(task) -> bool:
    key = sig_key(signature(task))
    return any(e.get("key") == key for e in _load().get("poison", []))


def poison_reason(task) -> str:
    key = sig_key(signature(task))
    for e in _load().get("poison", []):
        if e.get("key") == key:
            return f"{e.get('reason', '')}（{e.get('ts', '')} @ {e.get('node', '')}）"
    return ""


def add_poison(task, reason: str, node: str = "", extra: Optional[dict] = None) -> dict:
    """把任务签名写入熔断名单（幂等，同签名覆盖并记录最新证据）。"""
    sig = signature(task)
    entry = {
        "key": sig_key(sig),
        "sig": sig,
        "reason": reason,
        "node": node,
        "task_id": getattr(task, "id", None),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if extra:
        entry.update(extra)
    data = _load()
    data["poison"] = [e for e in data["poison"] if e.get("key") != entry["key"]] + [entry]
    _save(data)
    _event("poison_add", **entry)
    logger.error("毒丸熔断：%s ← %s（节点 %s，任务 #%s）", entry["key"], reason, node, entry["task_id"])
    return entry


def poison_list() -> list:
    return _load().get("poison", [])


def clear_poison(key: Optional[str] = None) -> int:
    """解除熔断：key 为空则清空全部。返回清除条数。"""
    data = _load()
    before = len(data["poison"])
    data["poison"] = [] if not key else [e for e in data["poison"] if e.get("key") != key]
    _save(data)
    _event("poison_clear", key=key, removed=before - len(data["poison"]))
    return before - len(data["poison"])


# ------------------------------------------------------------------ 节点粘滞（同节点重试）
def pin_set(task_id: int, url: str) -> None:
    data = _load()
    cur = data["pins"].get(str(task_id)) or {}
    data["pins"][str(task_id)] = {
        "url": (url or "").rstrip("/"),
        "ts": time.time(),
        "attempts": cur.get("attempts", 0),
    }
    _save(data)


def pin_get(task_id: int) -> Optional[str]:
    entry = _load()["pins"].get(str(task_id))
    if not entry:
        return None
    if time.time() - float(entry.get("ts", 0)) > PIN_TTL_SECONDS:
        pin_clear(task_id)
        _event("pin_expired", task_id=task_id, url=entry.get("url"))
        return None
    return entry.get("url")


def pin_attempts(task_id: int) -> int:
    return int((_load()["pins"].get(str(task_id)) or {}).get("attempts", 0))


def pin_bump(task_id: int) -> int:
    data = _load()
    entry = data["pins"].get(str(task_id))
    if entry is None:
        return 0
    entry["attempts"] = int(entry.get("attempts", 0)) + 1
    _save(data)
    return entry["attempts"]


def pin_clear(task_id: int) -> None:
    data = _load()
    if str(task_id) in data["pins"]:
        data["pins"].pop(str(task_id), None)
        _save(data)


# ------------------------------------------------------------------ 存活探测
def node_alive(url: str, timeout: float = 3.0) -> bool:
    """同步探测节点是否活着（/system_stats 200）。用于判断"节点死于任务运行中"。"""
    url = (url or "").rstrip("/")
    if not url or url == "pending":
        return False
    try:
        with urllib.request.urlopen(f"{url}/system_stats", timeout=timeout) as r:
            return 200 <= getattr(r, "status", 200) < 300
    except Exception:
        return False

