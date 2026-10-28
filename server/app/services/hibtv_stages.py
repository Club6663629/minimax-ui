"""skill 产物 → 平台数据（**纯字段搬运**）。

职责边界（这一层最容易悄悄长回手搓，改之前先读）：
- 平台在这里只做三件事：**找主产物、断言、搬字段**；
- 不拼提示词、不补内容、不改写、不做正则解析定稿剧本；
- 结构不对就抛 `StageError`（宁可拦下，也不带坏产物往下走）。

字段口径以 fullchain 的实跑产物为最终依据（outline / cast / art / asset / script /
staging / storyboard / manifest / prompt.md），一张图都没有的部分由平台出像素补位。
"""
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..config import settings
from ..models import HibtvFrame, HibtvProject, HibtvSegment

logger = logging.getLogger(__name__)

# 段长硬上限：H3 只出 5+17n 帧（24fps），13s = 311 帧 = 12.9583s
MAX_SEGMENT_SECONDS = 13.0
# 段秒 vs Σ切秒的对账容差（skill 侧 tolerance 默认 0.15，这里取更严的绝对值）
SECONDS_TOLERANCE = 0.05

DOC_KINDS = ("outline", "cast", "art", "staging", "script", "storyboard")


class StageError(Exception):
    """产物结构不合格（缺主产物 / 缺 h3Prompt / 切数≠图数）。"""


# ------------------------------------------------------------------ 产物定位
def _rel(path) -> str:
    p = Path(path)
    return p.as_posix() if not p.is_absolute() else str(p)


def pick(files: Dict[str, Path], *, contain: str = "", suffix: str = "",
         prefix: str = "", exclude: Tuple[str, ...] = ()) -> Optional[Tuple[str, Path]]:
    """按「包含 / 后缀 / 前缀 / 排除」找第一个产物（忽略目录层级，只看文件名）。"""
    for rel in sorted(files):
        name = Path(rel).name
        if contain and contain not in name:
            continue
        if suffix and not name.endswith(suffix):
            continue
        if prefix and not name.startswith(prefix):
            continue
        if any(bad in rel for bad in exclude):
            continue
        return rel, Path(files[rel])
    return None


def read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StageError(f"产物不是合法 JSON：{Path(path).name}（{exc}）") from exc


def read_text(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def sha8(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]


def episode_of(rows: Optional[List[Dict]], ep: int) -> Dict:
    for row in rows or []:
        if int(row.get("ep") or 0) == int(ep):
            return row
    return {}


def cut_seconds(seg: Dict) -> float:
    return round(sum(float(c.get("seconds") or 0) for c in (seg.get("cuts") or [])), 3)


# ------------------------------------------------------------------ 入口
def apply(db: Session, project: HibtvProject, kind: str, ep: int,
          files: Dict[str, Path], *, codex_job_id: str = "",
          usage: Optional[Dict] = None) -> Dict:
    """把一段 stage 的全部产物搬进 docs / assets / segments / frames。返回摘要。"""
    from .hibtv_jobs import jdumps, now, save_doc, sync_assets, sync_segments

    from .codex_gateway import STAGES as _GW_STAGES   # 纯常量表，无 IO

    files = {_rel(k): Path(v) for k, v in (files or {}).items()}
    if not files:
        raise StageError("网关没有产物，无法回写")
    base_meta = {
        "stage": _GW_STAGES.get(kind, kind),   # 网关 stage 名（novel-outline…），UI 直接展示
        "kind": kind,                          # 平台 job kind（outline…）
        "codex_job_id": codex_job_id,
        "usage": usage or {},
        "artifacts": [{"path": k, "bytes": p.stat().st_size if p.exists() else 0}
                      for k, p in sorted(files.items())],
        "applied_at": now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    handler = {
        "outline": _apply_outline, "cast": _apply_cast, "art": _apply_art,
        "refs": _apply_refs, "script": _apply_script, "staging": _apply_staging,
        "storyboard": _apply_storyboard,
    }.get(kind)
    if handler is None:
        raise StageError(f"未知文本阶段 {kind}")

    summary = handler(db, project, ep, files)
    doc_kind = summary.get("doc_kind") or kind
    doc_ep = int(summary.get("ep", 0 if kind in ("outline", "cast", "art") else ep))
    data = summary.pop("data", None)
    if data is not None:
        gates = summary.pop("gates", {"status": "skipped", "problems": [],
                                      "notes": ["产物里没有结构化门结果"], })
        meta = dict(base_meta)
        meta["mainSha8"] = sha8(jdumps(data))
        meta.update(summary.pop("meta", {}) or {})
        save_doc(db, project.id, doc_kind, doc_ep, data, gates=gates, meta=meta)
    else:
        summary.pop("gates", None)
        summary.pop("meta", None)
    db.commit()
    logger.info("短剧阶段 %s（E%02d）已回写：%s", kind, ep, jdumps(summary)[:400])
    return summary


# ------------------------------------------------------------------ outline
def _apply_outline(db, project, ep, files) -> Dict:
    from . import hibtv_jobs

    hit = pick(files, suffix="-outline.json") or pick(files, contain="outline", suffix=".json")
    if hit is None:
        raise StageError("找不到 `*-outline.json` 主产物")
    data = read_json(hit[1])
    if not isinstance(data, dict):
        raise StageError("大纲产物不是对象")
    params = data.get("params") or {}
    # ① 定集数 / 时长 / 比例（回填项目）
    try:
        episodes = int(params.get("episodes") or project.episodes or 1)
    except (TypeError, ValueError):
        episodes = int(project.episodes or 1)
    project.episodes = max(1, episodes)
    minutes = params.get("minutesPerEpisode")
    if isinstance(minutes, (int, float)) and minutes > 0:
        project.minutes_min = max(1, int(minutes))
        project.minutes_max = max(project.minutes_min, int(round(minutes + 0.5)))
    if params.get("genre"):
        project.genre = str(params["genre"])[:64]
    ratio = str(params.get("aspectRatio") or "").strip()
    if ratio.startswith("16:9") or ratio.startswith("9:16"):
        project.aspect_ratio = "16:9" if ratio.startswith("16") else "9:16"
    project.status = "ready"
    project.updated_at = hibtv_jobs.now()
    # ② 解析汇总改为由大纲回填（不再正则解析定稿剧本）
    project.detected = hibtv_jobs.jdumps({
        "episodes": project.episodes,
        "minutesPerEpisode": minutes or 0,
        "scenes": len(data.get("scenes") or []),
        "cast": len(data.get("characters") or []),
        "prop": len(data.get("props") or []),
        "beats": len(data.get("beats") or []),
        "source": "outline",
    })
    return {
        "doc_kind": "outline", "ep": 0, "data": data,
        "gates": _gates_from(files, "outline.json"),
        "episodes": project.episodes,
        "beats": len(data.get("beats") or []),
        "meta": {"report": _report_rel(files, "outline"), "md": _md_rel(files, "outline")},
    }


# ------------------------------------------------------------------ cast
def _apply_cast(db, project, ep, files) -> Dict:
    from .hibtv_jobs import sync_assets

    hit = pick(files, suffix="-cast.json")
    if hit is None:
        raise StageError("找不到 `*-cast.json` 主产物")
    data = read_json(hit[1])
    rows = data.get("characters") or []
    if not rows:
        raise StageError("角色表为空（characters[] 没有条目）")
    for i, row in enumerate(rows):
        if not str(row.get("id") or "").strip():
            row["id"] = f"C{i + 1:02d}"
        if not (row.get("image") or {}).get("prompt"):
            raise StageError(f"角色 {row['id']} 缺 image.prompt（资产图无处可出）")
    sync_assets(db, project, "cast", rows)
    return {
        "doc_kind": "cast", "ep": 0, "data": data,
        "gates": _gates_from(files, "cast.json", skipped="角色表没有结构化门结果"),
        "characters": len(rows),
        "meta": {"report": _report_rel(files, "cast")},
    }


# ------------------------------------------------------------------ art
def _apply_art(db, project, ep, files) -> Dict:
    from .hibtv_jobs import sync_assets

    hit = pick(files, suffix="art.json")
    if hit is None:
        raise StageError("找不到 `art.json` 主产物")
    data = read_json(hit[1])
    scenes = data.get("scenes") or []
    props = data.get("props") or []
    if not scenes:
        raise StageError("美术设定没有场景（scenes[] 为空）")
    for i, row in enumerate(scenes):
        row["id"] = str(row.get("id") or f"S{i + 1:02d}")
        if not (row.get("image") or {}).get("prompt"):
            raise StageError(f"场景 {row['id']} 缺 image.prompt")
    for i, row in enumerate(props):
        row["id"] = str(row.get("id") or f"P{i + 1:02d}")
        if not (row.get("image") or {}).get("prompt"):
            raise StageError(f"道具 {row['id']} 缺 image.prompt")
    sync_assets(db, project, "scene", scenes)
    sync_assets(db, project, "prop", props)
    return {
        "doc_kind": "art", "ep": 0, "data": data,
        "gates": _gates_from(files, "art.json", skipped="美术设定没有结构化门结果"),
        "scenes": len(scenes), "props": len(props),
        "meta": {"report": _report_rel(files, "art")},
    }


# ------------------------------------------------------------------ refs
def _apply_refs(db, project, ep, files) -> Dict:
    """`character-refs` 只产分层底稿与提示词（本机无出图通道，零 PNG，正好由平台补位）。"""
    from . import hibtv_jobs

    cast_doc = hibtv_jobs.get_doc(db, project.id, "cast")
    cast = hibtv_jobs.jloads(cast_doc.data, {}) if cast_doc else {}
    name_to_code = {str(c.get("name") or ""): str(c.get("id") or "")
                    for c in (cast.get("characters") or [])}

    refs: Dict[str, Dict] = {}
    prompts: Dict[str, str] = {}
    for rel in sorted(files):
        name = Path(rel).name
        if name.endswith("_asset.json") or name == "asset.json":
            try:
                payload = json.loads(Path(files[rel]).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue      # 同名的 report.html 之类不是 JSON，跳过
            if not isinstance(payload, dict) or not payload.get("layers"):
                continue
            who = str(payload.get("name") or "").strip()
            code = name_to_code.get(who) or _code_from_name(name) or ""
            if not code:
                code = f"C{len(refs) + 1:02d}"
            refs[code] = payload
        elif name.startswith("refs-prompts") and name.endswith(".md"):
            prompts[name] = read_text(Path(files[rel]))
    if not refs and not prompts:
        raise StageError("参考图阶段没有产出 asset.json / refs-prompts.md")

    if cast_doc is None:
        # 允许乱序重跑：没有 cast 文档时把 refs 单独落到 cast 文档骨架里
        data = {"characters": [], "refs": refs, "refsPrompts": prompts}
    else:
        data = cast
        data["refs"] = refs
        data["refsPrompts"] = prompts
    _merge_assets(db, project, refs)
    return {
        "doc_kind": "cast", "ep": 0, "data": data,
        "gates": _gates_from(files, "refs-report.html", skipped="参考图阶段没有结构化门结果"),
        "refs": len(refs), "prompts": len(prompts),
        "meta": {"report": _report_rel(files, "refs")},
    }


def _code_from_name(name: str) -> str:
    """`林晚_asset.json` → 从 cast 里匹配不到时留空（由调用方补号）。"""
    stem = name[: -len("_asset.json")] if name.endswith("_asset.json") else ""
    return "" if stem in ("", "asset") else ""


def _merge_assets(db, project, refs: Dict[str, Dict]) -> None:
    """把分层底稿挂到对应资产卡上（`data.refs`），角色页的「分层」抽屉直接读它。"""
    from ..models import HibtvAsset
    from .hibtv_jobs import jdumps, jloads, now

    for code, payload in (refs or {}).items():
        asset = (db.query(HibtvAsset)
                 .filter(HibtvAsset.project_id == project.id, HibtvAsset.kind == "cast",
                         HibtvAsset.code == code)
                 .first())
        if asset is None:
            continue
        data = jloads(asset.data, {})
        data["refs"] = payload
        asset.data = jdumps(data)
        asset.updated_at = now()
    db.flush()


# ------------------------------------------------------------------ script
def _apply_script(db, project, ep, files) -> Dict:
    hit = pick(files, suffix="-script.json")
    if hit is None:
        raise StageError("找不到 `*-script.json` 主产物")
    data = read_json(hit[1])
    row = episode_of(data.get("episodes"), ep)
    if not row:
        raise StageError(f"剧本产物里没有第 {ep} 集")
    if not row.get("scenes"):
        raise StageError(f"第 {ep} 集剧本没有场次（scenes[] 为空）")
    return {
        "doc_kind": "script", "ep": ep, "data": data,
        "gates": _gates_from(files, "script.json"),
        "scenes": len(row.get("scenes") or []),
        "targetSeconds": row.get("targetSeconds") or 0,
        "meta": {"report": _report_rel(files, "script"), "md": _md_rel(files, "script")},
    }


# ------------------------------------------------------------------ staging
def _apply_staging(db, project, ep, files) -> Dict:
    hit = pick(files, suffix="staging.json")
    if hit is None:
        raise StageError("找不到 `staging.json` 主产物")
    data = read_json(hit[1])
    rows = [s for s in (data.get("scenes") or []) if int(s.get("ep") or 0) == int(ep)]
    if not rows:
        raise StageError(f"站位产物里没有第 {ep} 集")
    return {
        "doc_kind": "staging", "ep": ep, "data": data,
        "gates": _gates_from(files, "staging.json", skipped="站位没有结构化门结果"),
        "scenes": len(rows),
        "locations": len(data.get("locations") or []),
        "meta": {"report": _report_rel(files, "staging")},
    }


# ------------------------------------------------------------------ storyboard
def _apply_storyboard(db, project, ep, files) -> Dict:
    from .hibtv_jobs import jdumps, sync_segments

    hit = pick(files, suffix="-storyboard.json")
    if hit is None:
        raise StageError("找不到 `*-storyboard.json` 主产物")
    data = read_json(hit[1])
    row = episode_of(data.get("episodes"), ep)
    segments = row.get("segments") or []
    if not segments:
        raise StageError(f"分镜产物里没有第 {ep} 集的段")

    manifest = _load_manifest(files)
    prompts = _load_prompts(files)

    checked: List[Dict] = []
    for seg in segments:
        seg_id = str(seg.get("id") or "").strip()
        if not seg_id:
            raise StageError("分镜段缺 id")
        cuts = seg.get("cuts") or []
        if not cuts:
            raise StageError(f"{seg_id} 没有镜头（cuts[] 为空）")
        item = manifest.get(seg_id) or {}
        pictures = item.get("pictures") or []
        pmd = prompts.get(seg_id) or ""
        picture_n = _count_pictures(pmd)
        # ① 图数对账：cuts == manifest.pictures == prompt.md 的 Picture N 条数
        if pictures and len(pictures) != len(cuts):
            raise StageError(
                f"{seg_id} 切数 {len(cuts)} ≠ 图位数 {len(pictures)}（ref2v 少一张图就会切到空白）")
        if pmd and picture_n != len(cuts):
            raise StageError(
                f"{seg_id} 切数 {len(cuts)} ≠ prompt.md 的 Picture 数 {picture_n}")
        # ② 出片提示词：prompt.md 原文优先（它就是「切点↔图」的契约）
        h3 = (pmd.strip() or str(seg.get("h3Prompt") or "").strip())
        if not h3:
            raise StageError(f"{seg_id} 缺 h3Prompt / prompt.md（段出片没有入参）")
        cast_doc = get_doc(db, project.id, "cast")
        cast_rows = (jloads(cast_doc.data, {}).get("characters") or []) if cast_doc else []
        line_speakers = _script_line_speakers(db, project, ep)
        h3 = _bind_h3_prompt(h3, seg, cast_rows, line_speakers)
        secs = cut_seconds(seg)
        manifest_secs = float(item.get("seconds") or 0)
        if manifest_secs and abs(manifest_secs - secs) > SECONDS_TOLERANCE * max(1, len(cuts)):
            checked.append({"seg": seg_id, "note": f"manifest {manifest_secs}s ≠ Σ切 {secs}s"})
        oversize = max(secs, manifest_secs) > MAX_SEGMENT_SECONDS + 1e-6
        seg["h3Prompt"] = h3
        seg["promptSource"] = (("prompt.md" if pmd else "storyboard.h3Prompt")
                               + "+identity-binding")
        seg["promptSha8"] = sha8(h3)
        seg["frameCount"] = len(cuts)
        seg["pictureCount"] = len(pictures) or len(cuts)
        seg["manifestSeconds"] = manifest_secs or secs
        seg["oversize"] = oversize
        if oversize:
            checked.append({"seg": seg_id,
                            "note": f"段长 {max(secs, manifest_secs):.1f}s 超过 "
                                    f"{MAX_SEGMENT_SECONDS:g}s 上限，需重跑本集 storyboard"})

    sync_segments(db, project, ep, data)
    frames = sync_frames(db, project, ep, manifest, segments)
    return {
        "doc_kind": "storyboard", "ep": ep, "data": data,
        "gates": _gates_from(files, "storyboard.json"),
        "segments": len(segments),
        "frames": frames,
        "warnings": checked,
        "oversize": [w["seg"] for w in checked if "上限" in w["note"]],
        "manifest": manifest,
        "meta": {"report": _report_rel(files, "storyboard"),
                 "manifest": bool(manifest)},
    }


_H3_LOCK_MARK = "[[hibtv_identity_speaker_lock_v1]]"
_DIALOGUE_RE = re.compile(r"<d>(.*?)</d>", re.IGNORECASE | re.DOTALL)
_SPEAKER_RE = re.compile(r"\(S(\d+)\)", re.IGNORECASE)


def _dialogue_key(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = re.sub(r"^\s*\[[^\]]+\]", "", text)
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE).casefold()


def _script_line_speakers(db: Session, project: HibtvProject, ep: int) -> Dict[str, str]:
    """台词原文 → 角色 ID；用于给没有 (S#) 的 <d> 补回明确归属。"""
    from .hibtv_jobs import get_doc, jloads

    doc = get_doc(db, project.id, "script", ep)
    row = episode_of(jloads(doc.data, {}).get("episodes") if doc else [], ep)
    found: Dict[str, set] = {}
    for scene in row.get("scenes") or []:
        for beat in scene.get("flow") or []:
            line = _dialogue_key(str(beat.get("line") or ""))
            speaker = str(beat.get("speaker") or "").strip()
            if line and speaker:
                found.setdefault(line, set()).add(speaker)
    return {key: next(iter(codes)) for key, codes in found.items() if len(codes) == 1}


def _bind_h3_prompt(h3: str, seg: Dict, cast_rows: List[Dict],
                    line_speakers: Optional[Dict[str, str]] = None) -> str:
    """把孤立 S# 展开为角色身份，并为每个 Shot 写入人数/口型/台词归属锁。"""
    if not h3 or _H3_LOCK_MARK in h3:
        return h3
    cast = [row for row in (cast_rows or []) if str(row.get("id") or "").strip()]
    code_to_speaker = {str(row["id"]): f"S{i + 1}" for i, row in enumerate(cast)}
    by_code = {str(row["id"]): row for row in cast}
    line_speakers = line_speakers or {}
    cuts = seg.get("cuts") or []

    legend = []
    for row in cast:
        code = str(row["id"])
        persona = row.get("persona") or {}
        legend.append(
            f"{code_to_speaker[code]} = {code} {row.get('name') or code}"
            f"（{persona.get('gender') or '性别见角色卡'}；"
            f"{persona.get('identity') or '身份见角色卡'}；"
            "位置按对应 Shot 的角色顺序确定）")

    shot_locks: Dict[int, str] = {}
    for match in re.finditer(r"(?m)^\[Shot\s+(\d+)\]", h3):
        shot_no = int(match.group(1))
        if shot_no > len(cuts):
            raise StageError(f"{seg.get('id') or '分镜段'} Shot {shot_no} 没有对应 cut")
        cut = cuts[shot_no - 1] or {}
        chars = list(dict.fromkeys(
            str(code) for code in (cut.get("characters") or []) if str(code)))
        unknown = [code for code in chars if code not in by_code]
        if unknown:
            raise StageError(
                f"{seg.get('id') or '分镜段'} Shot {shot_no} 引用未知角色 {unknown}")
        try:
            declared_max = int(cut.get("maxOnScreen") or 0)
        except (TypeError, ValueError):
            declared_max = 0
        # 画面人数 = 去重具名角色数与导演声明上限取大者：具名角色必须全部出场；
        # 正数 maxOnScreen 可能含未具名群演，需保留；负数按 0 处理避免非法人数。
        max_on_screen = max(len(chars), max(0, declared_max))

        next_shot = re.search(r"(?m)^\[Shot\s+\d+\]", h3[match.end():])
        end = match.end() + next_shot.start() if next_shot else len(h3)
        soundscape = re.search(r"(?m)^overall_soundscape:", h3[match.end():end])
        if soundscape:
            end = match.end() + soundscape.start()
        body = h3[match.start():end]

        speakers: List[str] = []
        for dialogue in _DIALOGUE_RE.finditer(body):
            code = line_speakers.get(_dialogue_key(dialogue.group(1)), "")
            if not code:
                prior = body[:dialogue.start()]
                labels = _SPEAKER_RE.findall(prior)
                if labels:
                    label = f"S{labels[-1]}"
                    code = next((cid for cid, sid in code_to_speaker.items()
                                 if sid == label), "")
            if not code and len(chars) == 1:
                code = chars[0]
            if not code:
                raise StageError(
                    f"{seg.get('id') or '分镜段'} Shot {shot_no} 台词无法绑定角色："
                    f"{dialogue.group(1)[:80]}")
            if code not in chars:
                raise StageError(
                    f"{seg.get('id') or '分镜段'} Shot {shot_no} 说话人 {code} "
                    f"不在 cut.characters {chars} 中")
            if code not in speakers:
                speakers.append(code)

        people = "、".join(
            f"{code} {by_code[code].get('name') or code}（第 {i + 1} 位）"
            for i, code in enumerate(chars)) or "无完整人物"
        if speakers:
            speaker_text = " and ".join(
                f"{code_to_speaker[code]} ({code} {by_code[code].get('name') or code})"
                for code in speakers)
            speech = (f"Only {speaker_text} speaks; all other characters keep "
                      "their mouths closed.")
        else:
            speech = "No character speaks; all characters keep their mouths closed."
        shot_locks[shot_no] = (
            f"Character/speaker lock: maxOnScreen={max_on_screen}; "
            f"exactly {max_on_screen} different people in frame ({people}); "
            "the same character must never appear twice; no clones, twins, duplicate "
            f"faces, extra people, character sheet or multi-view grid. {speech}")

    if not shot_locks:
        raise StageError(f"{seg.get('id') or '分镜段'} 的 h3Prompt 没有 [Shot N] 结构")

    def add_lock(match):
        no = int(match.group(2))
        body = match.group(0).rstrip()
        return f"{body} {shot_locks[no]}\n"

    bound = re.sub(
        r"(?ms)^(\[Shot\s+(\d+)\].*?)(?=^\[Shot\s+\d+\]|\noverall_soundscape:|\Z)",
        add_lock, h3)
    block = "\n".join([
        _H3_LOCK_MARK,
        "Character and speaker binding (authoritative):",
        *legend,
        "Every S# is permanently bound to the listed character ID and name. "
        "Never transfer a line to another character.",
        "",
        "",
    ])
    marker = "integrated_multimodal_description:"
    if marker not in bound:
        raise StageError(f"{seg.get('id') or '分镜段'} 的 h3Prompt 缺 {marker}")
    return bound.replace(marker, block + marker, 1)


def _load_manifest(files: Dict[str, Path]) -> Dict[str, Dict]:
    hit = pick(files, contain="manifest", suffix=".json")
    if hit is None:
        return {}
    data = read_json(hit[1])
    rows = data.get("segments") if isinstance(data, dict) else data
    out: Dict[str, Dict] = {}
    for row in rows or []:
        if isinstance(row, dict) and row.get("segment"):
            out[str(row["segment"])] = row
    return out


def _load_prompts(files: Dict[str, Path]) -> Dict[str, str]:
    """`<seg>/prompt.md` → {seg_id: 原文}。目录名就是段号，是最可靠的对应关系。"""
    out: Dict[str, str] = {}
    for rel, path in files.items():
        if Path(rel).name != "prompt.md":
            continue
        seg_id = Path(rel).parent.name
        if seg_id and seg_id not in (".", ""):
            out[seg_id] = read_text(path)
    return out


_PICTURE_RE = re.compile(r"^\s*(?:[-*#>\u2022]+\s*)?Picture\s+(\d+)\b", re.IGNORECASE)


def _count_pictures(prompt_md: str) -> int:
    """数「Picture N」条数（首行对齐段的逐切钉点）；同号重复只算一次。"""
    seen = set()
    for line in (prompt_md or "").splitlines():
        m = _PICTURE_RE.match(line)
        if m:
            seen.add(int(m.group(1)))
    return len(seen)


def sync_frames(db: Session, project: HibtvProject, ep: int,
                manifest: Dict[str, Dict], segments: List[Dict]) -> int:
    """按 `manifest.pictures` 生成/刷新关键帧行（每切一张，f1..fN）。"""
    from .hibtv_jobs import jdumps, jloads, now

    by_id = {str(s.get("id")): s for s in segments}
    existing = {(f.seg_id, int(f.cut_index)): f
                for f in db.query(HibtvFrame).filter(
                    HibtvFrame.project_id == project.id, HibtvFrame.ep == int(ep)).all()}
    count = 0
    for seg_id, seg in by_id.items():
        cuts = seg.get("cuts") or []
        item = manifest.get(seg_id) or {}
        pictures = item.get("pictures") or []
        for i, cut in enumerate(cuts, start=1):
            row = existing.get((seg_id, i))
            if row is None:
                row = HibtvFrame(project_id=project.id, ep=int(ep), seg_id=seg_id,
                                 cut_index=i, status="empty", refs="[]")
                db.add(row)
            meta = jloads(row.meta, {})
            meta.update({
                "beats": cut.get("beats"),
                "size": cut.get("size") or "",
                "camera": cut.get("camera") or "",
                "characters": cut.get("characters") or [],
                "maxOnScreen": cut.get("maxOnScreen"),
                "props": cut.get("props") or [],
                "startsAt": (item.get("cutStarts") or [])[i - 1] if i - 1 < len(item.get("cutStarts") or []) else None,
                "expected": pictures[i - 1] if i - 1 < len(pictures) else "",
            })
            row.meta = jdumps(meta)
            if str(cut.get("frame") or "").strip():
                row.prompt = str(cut["frame"]).strip()
            row.updated_at = now()
            count += 1
    # 段没了（重跑后重编号）的帧行：从未出图就删，出过图保留
    keep = {(seg_id, i) for seg_id in by_id for i in range(1, len(by_id[seg_id].get("cuts") or []) + 1)}
    for key, row in existing.items():
        if key in keep or row.path:
            continue
        db.delete(row)
    db.flush()
    return count


# ------------------------------------------------------------------ 门 / 附带产物
def _gates_from(files: Dict[str, Path], doc_name: str, *, skipped: str = "") -> Dict:
    """门结果去 `hibtv_gates` 里读（那边只认产物，不跑脚本）。"""
    from . import hibtv_gates

    return hibtv_gates.from_artifacts(files, doc_name, skipped=skipped)


def _report_rel(files: Dict[str, Path], kind: str) -> str:
    hit = pick(files, contain="report", suffix=".html")
    return hit[0] if hit else ""


def _md_rel(files: Dict[str, Path], kind: str) -> str:
    hit = pick(files, suffix=".md", exclude=("refs-prompts",))
    return hit[0] if hit else ""
