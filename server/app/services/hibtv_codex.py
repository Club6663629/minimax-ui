"""短剧文本阶段编排：提交网关 job → 轮询 → 拉产物落盘 → 叫人搬字段。

一条线划死：**文本归 codex，像素归平台**。
- 本模块只写「业务意图 + 上游产物引用」，schema 由 skill 自带，平台不拼提示词；
- 上游一律用网关机器上的**绝对路径**（取自 `artifacts[].abs`），绝不把 JSON 全文塞进 input；
- 首段（outline）也不内联小说正文：先落 `DATA_DIR/hibtv/{id}/source.txt` 再给绝对路径。
"""
import asyncio
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..config import HIBTV_DIR, settings
from ..models import HibtvJob, HibtvProject
from . import codex_gateway as gw
from . import hibtv_stages

logger = logging.getLogger(__name__)

GW_MATERIALS_HOST = Path(os.environ.get(
    "HIBTV_GW_MATERIALS_HOST",
    "/root/sai-v2/dw-engine/tenants/xiaojun/data/workspace/hibtv-materials",
))
GW_MATERIALS_MOUNT = Path(os.environ.get(
    "HIBTV_GW_MATERIALS_MOUNT",
    "/data/workspace/hibtv-materials",
))

# 平台 job kind → 网关 stage
STAGES = gw.STAGES
TEXT_KINDS = tuple(STAGES)
# 每个 stage 需要的上游：kind → [(label, 上游 kind)]
UPSTREAM: Dict[str, List[Tuple[str, str]]] = {
    "outline": [],
    "cast": [("上游大纲", "outline")],
    "art": [("上游大纲", "outline")],
    "refs": [("上游角色表", "cast")],
    "script": [("上游大纲", "outline"), ("上游美术", "art")],
    "staging": [("上游剧本", "script"), ("上游美术", "art")],
    "storyboard": [("上游剧本", "script"), ("上游美术", "art"),
                   ("上游角色表", "cast"), ("上游站位", "staging")],
}
# 各 stage「主产物」的文件名特征（用来从产物清单里挑出要引用给下游的那个文件）
MAIN_SUFFIX: Dict[str, str] = {
    "outline": "-outline.json",
    "cast": "-cast.json",
    "art": "art.json",
    "refs": "_asset.json",
    "script": "-script.json",
    "staging": "staging.json",
    "storyboard": "-storyboard.json",
}
MAX_ATTEMPTS = 3          # 首次 + 最多 2 次重试


# ------------------------------------------------------------------ 路径
def project_dir(project_id: int) -> Path:
    d = HIBTV_DIR / str(int(project_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def source_path(project_id: int) -> Path:
    """小说正文落盘位置（input 里给它的绝对路径，不内联正文）。"""
    return project_dir(project_id) / "source.txt"


def gateway_source_path(project_id: int) -> Path:
    """给 codex 的正文路径（网关容器内可见）。"""
    return GW_MATERIALS_MOUNT / str(int(project_id)) / "source.txt"


def _publish_source(project_id: int, local_path: Path) -> Path:
    """把平台正文同步到网关素材目录；复制失败必须阻断任务。"""
    target = GW_MATERIALS_HOST / str(int(project_id)) / "source.txt"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, target)
    except OSError as exc:
        raise RuntimeError(f"网关素材中转失败：{local_path} -> {target}：{exc}") from exc
    if not target.is_file() or target.stat().st_size != local_path.stat().st_size:
        raise RuntimeError(f"网关素材中转校验失败：{target}")
    return target


def job_dir(project_id: int, gateway_jid: str) -> Path:
    d = project_dir(project_id) / "codex" / (gateway_jid or "unknown")
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_source(project_id: int, text: str) -> str:
    path = source_path(project_id)
    path.write_text(text or "", encoding="utf-8")
    _publish_source(project_id, path)
    return str(path)


# ------------------------------------------------------------------ 幂等
def fingerprint(project: HibtvProject, kind: str, ep: int, hint: str = "",
                upstream_key: str = "") -> str:
    """幂等键：项目 + 段 + 集 + 输入指纹 + 提示。

    同键且已 done → 直接复用，不烧 token；小说正文改了、上游产物换了或 hint 变了才会重跑。
    `upstream_key` 由调用方传上游 job 的 `codex_job_id`（最准的「上游版本」）。
    """
    material = project.novel_text or "" if kind == "outline" else upstream_key
    raw = (f"{project.id}:{kind}:{ep}:{hint}:"
           f"{hashlib.sha256(str(material).encode()).hexdigest()}")
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def doc_ep(kind: str, ep: int) -> int:
    """全集级文档（outline/cast/art）的 ep 恒为 0，其余按集。"""
    return 0 if kind in ("outline", "cast", "art") else int(ep or 1)


def upstream_key(db: Session, project: HibtvProject, kind: str, ep: int) -> str:
    """上游版本指纹：取每个上游段最近一次成功 job 的网关 jid（最准的「上游版本」）。"""
    if kind == "outline":
        return ""
    parts: List[str] = []
    for _label, up in UPSTREAM.get(kind, []):
        job = latest_done(db, project.id, up, ep)
        parts.append(str(job.codex_job_id or job.id) if job is not None else "-")
    return "|".join(parts)


# ------------------------------------------------------------------ 上游引用
def latest_done(db: Session, project_id: int, kind: str, ep: int) -> Optional[HibtvJob]:
    from .hibtv_jobs import jloads

    jobs = (db.query(HibtvJob)
            .filter(HibtvJob.project_id == project_id, HibtvJob.kind == kind,
                    HibtvJob.status == "done")
            .order_by(HibtvJob.id.desc()).limit(10).all())
    for job in jobs:
        if doc_ep(kind, job.ep) != doc_ep(kind, ep):
            continue
        if jloads(job.artifacts, []):
            return job
    return None


def main_artifact(artifacts: List[Dict], kind: str) -> str:
    """从产物清单里挑主产物的**网关侧绝对路径**（下游 input 引用的就是它）。"""
    suffix = MAIN_SUFFIX.get(kind, "")
    rows = [a for a in (artifacts or []) if isinstance(a, dict)]
    for row in rows:
        name = Path(str(row.get("path") or "")).name
        if suffix and name.endswith(suffix) and not name.startswith("intake"):
            if row.get("abs"):
                return str(row["abs"])
    for row in rows:
        if row.get("abs"):
            return str(row["abs"])
    return ""


def upstream_refs(db: Session, project: HibtvProject, kind: str, ep: int) -> Dict[str, str]:
    """上游引用：{label: 网关侧绝对路径}。缺上游就报错（不猜、不降级）。"""
    from .hibtv_jobs import jloads

    out: Dict[str, str] = {}
    for label, up in UPSTREAM.get(kind, []):
        job = latest_done(db, project.id, up, ep)
        if job is None:
            raise RuntimeError(f"{kind} 需要上游「{up}」，但还没有成功产物，先跑那一段")
        abs_path = main_artifact(jloads(job.artifacts, []), up)
        if not abs_path:
            raise RuntimeError(f"上游「{up}」的产物清单里没有可引用的绝对路径")
        out[label] = abs_path
    return out


# ------------------------------------------------------------------ 任务正文（只写业务意图）
def task_for(db: Session, project: HibtvProject, kind: str, ep: int,
             hint: str = "") -> str:
    stage = STAGES[kind]
    minutes = float(project.minutes_min or 2)
    ratio = project.aspect_ratio or "9:16"
    head = (f"用 skill `{stage}` 完成流水线这一段；当前工作目录即本段产物目录，所有产物写在这里。")
    lines: List[str] = [head]

    if kind == "outline":
        local_source = source_path(project.id)
        if not local_source.is_file():
            # 兼容历史项目：旧数据可能只存了 project.novel_path，本地没有 source.txt。
            legacy = str(project.novel_path or "").strip()
            if legacy and Path(legacy).is_file():
                try:
                    local_source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(legacy, local_source)
                except OSError as exc:
                    raise RuntimeError("历史小说正文复制失败，先补小说再跑大纲") from exc
            else:
                raise RuntimeError("项目没有小说正文，先补小说再跑大纲")
        # 重跑旧项目时也补建中转副本；历史数据不做批量迁移。
        _publish_source(project.id, local_source)
        src = str(gateway_source_path(project.id))
        lines.append(
            f"把这部小说改编成 {int(project.episodes or 1)} 集竖屏短剧大纲，"
            f"每集约 {minutes:g} 分钟，画面 {ratio}。")
        if project.keeps.strip():
            lines.append(f"必须保留：{project.keeps.strip()}")
        lines.append(f"小说正文：{src}")
    else:
        for label, abs_path in upstream_refs(db, project, kind, ep).items():
            lines.append(f"{label}：{abs_path}")
        if kind == "cast":
            lines.append(f"为这 {int(project.episodes or 1)} 集出角色表（含形象提示词与音色提示词）。"
                         f"单集 {minutes:g} 分钟，{ratio}。")
        elif kind == "art":
            lines.append(f"为这 {int(project.episodes or 1)} 集出场景与道具美术设定"
                         f"（含出图提示词与跨集一致性锚点）。{ratio}。")
        elif kind == "refs":
            lines.append("为角色表里每个角色出分层参考底稿（identity / face / hair / build / "
                         "backCue 与 outfit 分层）与参考图提示词。")
        elif kind == "script":
            lines.append(f"为第 {ep} 集出逐场剧本（场次 / 节拍 / 台词逐字），"
                         f"目标时长约 {int(minutes * 60)} 秒，{ratio}。")
        elif kind == "staging":
            lines.append(f"为第 {ep} 集出站位（场景平面图物品清单 + 人物点位、朝向、群演）。")
        elif kind == "storyboard":
            seg_cap = float(getattr(settings, "hibtv_seg_max_seconds", 13) or 13)
            lines.append(f"为第 {ep} 集出分镜。每段不超过 {seg_cap:g} 秒，"
                         f"段内每 2–5 秒一个镜头，画面 {ratio}。")
    if hint.strip():
        lines.append(f"额外要求：{hint.strip()[:800]}")
    return "\n".join(lines)


# ------------------------------------------------------------------ 幂等复用
MAX_REUSE_AGE_DAYS = 30


def _reuse(db: Session, project: HibtvProject, kind: str, ref: str,
           exclude_id: int = 0) -> Optional[Tuple[HibtvJob, Dict[str, Path]]]:
    """同键且已 done → 直接复用本机产物（不烧 token）。产物被清掉了就返回 None 重跑。"""
    from .hibtv_jobs import jloads

    job = (db.query(HibtvJob)
           .filter(HibtvJob.project_id == project.id, HibtvJob.kind == kind,
                   HibtvJob.ref == ref, HibtvJob.status == "done")
           .order_by(HibtvJob.id.desc()).first())
    if job is None or int(job.id) == int(exclude_id or 0):
        return None
    rows = jloads(job.artifacts, [])
    local = job_dir(project.id, job.codex_job_id)
    files = {str(a.get("path")): local / str(a.get("path")) for a in (rows or [])
             if isinstance(a, dict) and a.get("path") and (local / str(a["path"])).exists()}
    return (job, files) if files else None


# ------------------------------------------------------------------ 执行
async def run(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    """跑一段文本 stage：提交 → 轮询 → 落盘 → 回写。异常交给 run_job 收敛。"""
    from .hibtv_jobs import jdumps, jloads, now, save_artifacts, save_usage, set_stage

    kind = job.kind
    ep = int(job.ep or 0)
    payload = jloads(job.payload, {})
    hint = str(payload.get("hint") or "").strip()
    timeout = int(payload.get("timeout") or getattr(settings, "codex_gw_job_timeout", 1800))
    silence = int(payload.get("silence_timeout")
                  or getattr(settings, "codex_gw_silence_timeout", 600))
    if not gw.status()["available"]:
        raise RuntimeError(f"codex 网关不可用：{gw.status()['note'] or '地址未配置'}"
                           "（本平台无本地兜底，请先恢复网关）")

    text = task_for(db, project, kind, ep, hint)
    # 幂等：同键且已 done → 直接复用产物，不烧 token
    ref = fingerprint(project, kind, ep, hint, upstream_key(db, project, kind, ep))
    job.ref = ref
    db.commit()
    found = _reuse(db, project, kind, ref, exclude_id=job.id)
    if found is not None:
        prev, files = found
        job.codex_job_id = prev.codex_job_id or ""
        job.artifacts = prev.artifacts or "[]"
        reuse_usage = dict(jloads(prev.usage, {}))
        reuse_usage.update({"reused": True, "reusedFrom": prev.id})
        save_usage(db, job, reuse_usage)
        set_stage(db, job, 80, "命中同键产物，直接复用（不烧 token）")
        hibtv_stages.apply(db, project, kind, ep, files,
                           codex_job_id=job.codex_job_id, usage=reuse_usage)
        logger.info("短剧 %s（E%02d）命中幂等键，复用 job #%s", kind, ep, prev.id)
        return

    attempts = 0
    usage: Dict = dict(jloads(job.usage, {}))
    while True:
        attempts += 1
        usage["attempts"] = attempts
        set_stage(db, job, 10, f"提交 {STAGES[kind]}（第 {attempts} 次）")
        try:
            created = await gw.submit(
                stage=STAGES[kind], input=text,
                project_id=gw.gateway_project_id(project.id),
                timeout=timeout, silence_timeout=silence)
            job.codex_job_id = str(created.get("id") or "")
            db.commit()
            info = await _wait(db, job, timeout=timeout, usage=usage)
            artifacts = info.get("artifacts") or []
            local = job_dir(project.id, job.codex_job_id)
            set_stage(db, job, 92, "拉取产物")
            saved = await gw.download_all(job.codex_job_id, local, artifacts=artifacts)
            merged = _merge_abs(saved, artifacts)
            if not any(a.get("bytes") for a in merged):
                raise hibtv_stages.StageError("网关产物全部下载失败（workdir 可能已被清理）")
            save_artifacts(db, job, merged)
            usage.update(_usage_of(info))
            save_usage(db, job, usage)
            set_stage(db, job, 96, "回写文档")
            files = {a["path"]: local / a["path"] for a in merged if a.get("bytes")}
            hibtv_stages.apply(db, project, kind, ep, files,
                               codex_job_id=job.codex_job_id, usage=usage)
            return
        except gw.CodexJobFailed as exc:
            usage["timeout_kind"] = exc.timeout_kind
            save_usage(db, job, usage)
            if attempts >= MAX_ATTEMPTS or not exc.retryable:
                raise
            if exc.timeout_kind == gw.TIMEOUT_SILENCE:
                # 静默超时 = 网关那边太久没输出：放大静默窗口再提一次
                silence = int(silence * 1.5)
                logger.warning("短剧 %s 静默超时，silence_timeout 提到 %ss 重试", kind, silence)
            else:
                logger.warning("短剧 %s 失败（%s），第 %d 次重试", kind, exc.timeout_kind, attempts)
            await asyncio.sleep(3)
        except gw.CodexError:
            raise


def _merge_abs(saved: List[Dict], artifacts: List[Dict]) -> List[Dict]:
    """本机落盘清单 + 网关侧绝对路径（下游 input 引用 abs，不用本机路径）。"""
    abs_by_path = {str(a.get("path") or ""): str(a.get("abs") or "")
                   for a in (artifacts or []) if isinstance(a, dict)}
    out: List[Dict] = []
    for row in saved or []:
        item = dict(row)
        item["abs"] = abs_by_path.get(str(item.get("path") or ""), "")
        out.append(item)
    return out


def _usage_of(info: Dict) -> Dict:
    raw = info.get("usage") or {}
    out: Dict = {}
    if isinstance(raw, dict):
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens"):
            if raw.get(key) is not None:
                out[key] = raw[key]
    for key in ("elapsed_sec", "exit_code", "heartbeat_at", "queue_position", "timeout_kind"):
        if info.get(key) is not None:
            out[key] = info[key]
    return out


async def _wait(db: Session, job: HibtvJob, *, timeout: int, usage: Dict) -> Dict:
    """轮询到 done；failed/cancelled 抛 `CodexJobFailed`（带 timeout_kind 供重试分流）。"""
    from .hibtv_jobs import save_usage, set_stage

    interval = float(getattr(settings, "codex_gw_poll_interval", 5.0) or 5.0)
    grace = int(getattr(settings, "codex_gw_poll_grace", 120) or 120)
    deadline = asyncio.get_event_loop().time() + float(timeout + grace)
    jid = job.codex_job_id
    last = ""
    while True:
        info = await gw.job(jid)
        state = str(info.get("status") or "")
        usage.update({k: v for k, v in _usage_of(info).items() if v is not None})
        queue = info.get("queue_position")
        hb = str(info.get("heartbeat_at") or "")
        note = f"{state}"
        if queue is not None:
            note += f" · 队列第 {queue} 位"
        if hb:
            note += f" · 心跳 {hb}"
        if state != last:
            logger.info("短剧网关 job %s → %s", jid, note)
            last = state
        set_stage(db, job, _progress_of(state), note)
        if state == "done":
            return info
        if state in ("failed", "cancelled", "canceled"):
            kind = str(info.get("timeout_kind") or "")
            tail = str(info.get("stderr_tail") or info.get("last_message") or "")[:600]
            raise gw.CodexJobFailed(
                f"网关 job {jid} {state}（{kind or 'unknown'}）：{tail}",
                timeout_kind=kind, detail=info)
        if asyncio.get_event_loop().time() > deadline:
            try:
                await gw.cancel(jid)
            except gw.CodexError:
                logger.warning("超时取消失败：%s", jid)
            raise gw.CodexJobFailed(
                f"平台轮询超时（{timeout + grace}s），已请求取消网关 job {jid}",
                timeout_kind=gw.TIMEOUT_TOTAL, detail={"job_id": jid})
        await asyncio.sleep(interval)


def _progress_of(state: str) -> int:
    return {"queued": 20, "running": 60, "done": 90}.get(state, 30)


# ------------------------------------------------------------------ 取消 / 自愈
async def cancel(project_id: int, job: HibtvJob) -> None:
    """平台取消 → 同步取消网关 job。不调 DELETE（只归档、不删 workdir，产物还能下载）。"""
    jid = (job.codex_job_id or "").strip()
    if not jid:
        return
    try:
        await gw.cancel(jid)
    except gw.CodexNotFound:
        logger.info("网关 job 已不存在，跳过取消：%s", jid)
    except gw.CodexError as exc:
        logger.warning("取消网关 job 失败 %s：%s", jid, exc)


async def reconcile_orphans(db: Session) -> int:
    """网关重启会把 queued 重新入队（可能重复烧 token）→ 把不认识的 job 全 cancel。"""
    if not gw.status()["available"]:
        logger.warning("网关不可用，跳过启动自愈：%s", gw.status()["note"])
        return 0
    known = {str(j.codex_job_id) for j in
             db.query(HibtvJob).filter(HibtvJob.status.in_(("queued", "running"))).all()
             if (j.codex_job_id or "").strip()}
    rows: List[Dict] = []
    for state in ("queued", "running"):
        try:
            rows.extend(await gw.jobs(limit=200, status=state))
        except gw.CodexError as exc:
            logger.warning("列举网关 job 失败（%s）：%s", state, exc)
    cancelled = 0
    for row in rows:
        jid = str(row.get("id") or "")
        if not jid or jid in known:
            continue
        try:
            await gw.cancel(jid)
            cancelled += 1
            logger.warning("取消不认识的网关 job %s（stage=%s）", jid, row.get("stage"))
        except gw.CodexError as exc:
            logger.warning("取消 %s 失败：%s", jid, exc)
    return cancelled


def summarize(job: HibtvJob) -> Dict:
    """给前端的一行状态：网关 jid / 队列位 / tokens / 耗时 / timeout_kind。"""
    from .hibtv_jobs import jloads

    usage = jloads(job.usage, {})
    return {
        "codexJobId": (job.codex_job_id or ""),
        "artifacts": len(jloads(job.artifacts, [])),
        "usage": usage,
        "tokens": {
            "in": int(usage.get("input_tokens") or 0),
            "cached": int(usage.get("cached_input_tokens") or 0),
            "out": int(usage.get("output_tokens") or 0),
        },
        "elapsedSec": float(usage.get("elapsed_sec") or 0),
        "queuePosition": usage.get("queue_position"),
        "timeoutKind": usage.get("timeout_kind") or "",
    }
