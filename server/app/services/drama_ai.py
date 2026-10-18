"""短剧 AI 辅助能力（P0+P1）：剧本局部重写、分镜改写、返修扩写、定妆图反填/校验、
审片预检、交付文案。

分工：
- P0 文本：rewrite_beat / rewrite_shot / repair_brief；
- P1 视觉：describe_character / check_character / precheck；
- P1 文本：deliver_copy。

所有函数只产出「候选值」，不写库；写回由路由在用户采纳后走原有 PUT/PATCH 接口，
从而保证 spec_hash、缓存与计费语义不变。失败抛 DramaAgentError，由路由转 503/降级。
"""
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Iterable, Optional, Sequence

from . import drama_agent, drama_media

logger = logging.getLogger(__name__)

_FORBIDDEN = re.compile(r"字幕|水印|文字叠加|台标")
_ISSUE_TYPES = {"identity", "artifact", "text", "composition", "lighting", "continuity"}
_SEVERITIES = {"high", "mid", "low"}


# ---------------------------------------------------------------- 通用
def _text(value, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _payload(result: dict) -> dict:
    """统一返回：data + 来源信息。"""
    return {
        "source": "llm",
        "provider": result.get("provider", ""),
        "model": result.get("model", ""),
        "cached": bool(result.get("cached")),
        "data": result.get("data") or {},
    }


def _clean_prompt(value, fallback: str = "") -> str:
    text = _text(value, 2000)
    if len(text) < 6:
        return fallback
    if _FORBIDDEN.search(text):
        raise drama_agent.DramaAgentError("AI 写出了字幕/水印类要求，已放弃本次结果，请重试")
    return text


def _scene_text(scene) -> dict:
    if scene is None:
        return {}
    return {
        "场景名": scene.name or "",
        "地点": scene.location or "",
        "时间": scene.time_of_day or "",
        "环境描述": scene.description or "",
    }


def _character_text(characters: Iterable) -> list:
    rows = []
    for char in characters:
        rows.append({
            "姓名": char.name,
            "定位": char.role,
            "性别": char.gender or "",
            "年龄": char.age or "",
            "外形": char.appearance or "",
            "服装": char.costume or "",
            "声线": char.voice or "",
        })
    return rows


def _shot_text(shot) -> dict:
    return {
        "镜号": shot.shot_no,
        "画面与动作": shot.prompt or "",
        "景别": shot.shot_size or "",
        "机位": shot.camera or "",
        "运镜": shot.movement or "",
        "时长秒": shot.duration_s,
        "台词": shot.dialogue or "",
        "声音": shot.audio_note or "",
        "衔接": shot.continuity_note or "",
    }


# ---------------------------------------------------------------- P0 分镜改写
_SHOT_SYSTEM = """你是短剧分镜提示词改写专家，服务下游 MiniMax H3 视频模型。
只输出严格 JSON，不要解释、不要 Markdown。

铁律：
1. 只写画面可见内容：主体、动作、环境、光线、构图、镜头运动；台词只放 dialogue 字段。
   不得在 prompt 里写“字幕/水印/文字叠加/台标”，也不得要求一张画面出现多角度或拼贴。
2. 角色外形、发型、服装必须与给定角色卡一致；不得新增人物、不得改变身份与人数。
3. 保持原提示词的事件、情绪与镜头意图，不要引入新剧情或新道具。
4. prompt 是一段可直接给视频模型的中文画面描述，80–200 字，一段话，不分点、不加标题、
   不写景别代号与镜头编号（景别/运镜由结构化字段承载）。
5. 输出字段：
   {"prompt": "...", "continuity_note": "...", "audio_note": "...", "dialogue": "...",
    "changes": ["最多三条，说明改了什么"]}
   后三个字段可以为空字符串；changes 仅用于界面提示。"""


def rewrite_shot(
    *,
    project,
    episode,
    shot,
    scene,
    characters: Sequence,
    mode: str = "polish",
    instruction: str = "",
    user_id: int = 0,
) -> dict:
    """单镜提示词改写（P0）。mode=h3 时按 H3 结构化画面重写，polish 时只润色。"""
    mode = "h3" if str(mode or "").lower() == "h3" else "polish"
    user = json.dumps({
        "任务": "改写这一镜的画面提示词" if mode == "polish" else "按 H3 结构化重写这一镜的画面提示词",
        "项目风格": (project.style_bible if project else "") or "",
        "本集梗概": (episode.brief or episode.script or "")[:600],
        "场景": _scene_text(scene),
        "出场角色": _character_text(characters),
        "镜头": _shot_text(shot),
        "用户要求": _text(instruction, 500),
        "模式说明": (
            "润色：保留原有事件与句式结构，只让画面更具体、更可拍。"
            if mode == "polish"
            else "结构化：按 主体动作 → 环境 → 光线 → 构图 → 镜头运动 的顺序重写，信息密度更高。"
        ),
    }, ensure_ascii=False)
    salt = f"{episode.spec_hash}|{shot.shot_no}|{shot.prompt}|{instruction}|{mode}"
    result = drama_agent.chat_json(
        kind="rewrite_shot", system=_SHOT_SYSTEM, user=user,
        user_id=user_id, cache_salt=salt,
    )
    data = result["data"]
    prompt = _clean_prompt(data.get("prompt"), fallback=shot.prompt)
    return _payload({
        **result,
        "data": {
            "prompt": prompt,
            "continuity_note": _text(data.get("continuity_note"), 2000) or shot.continuity_note,
            "audio_note": _text(data.get("audio_note"), 2000) or shot.audio_note,
            "dialogue": _text(data.get("dialogue"), 2000) or shot.dialogue,
            "changes": [_text(item, 120) for item in (data.get("changes") or [])][:3],
        },
    })


# ---------------------------------------------------------------- P0 剧本局部重写
_BEAT_SYSTEM = """你是短剧编剧。只输出严格 JSON，不要 Markdown。
任务：只改写指定的这一拍，保持它在整集中的功能、与前后拍的衔接；不得改角色名单与目标时长。

输出字段：
{"title": "≤8 字标题", "summary": "40–120 字，写清这一拍发生了什么（谁、在哪、做了什么、结果）",
 "mood": "2–6 字情绪", "location": "具体地点", "dialogue_hint": "一句台词或空串"}

要求：
1. 不新增原梗概里没有的人物、场景与关键道具；
2. summary 必须是可拍的画面事件，不写心理活动与抽象评价；
3. 与前拍结尾、后拍开头在时间与空间上连续，避免“突然出现”。"""


def rewrite_beat(
    *,
    project,
    episode,
    beats: Sequence[dict],
    index: int,
    instruction: str = "",
    user_id: int = 0,
) -> dict:
    """剧本节拍局部重写（P0）：只返回该拍的 5 个文本字段，时长/角色/ID 保持原样。"""
    if index < 0 or index >= len(beats):
        raise drama_agent.DramaAgentError("节拍不存在")
    beat = beats[index]
    user = json.dumps({
        "任务": "重写第 %d 拍" % (index + 1),
        "项目风格": (project.style_bible if project else "") or "",
        "本集梗概": (episode.brief or "")[:600],
        "总拍数": len(beats),
        "当前这一拍": beat,
        "前一拍": beats[index - 1] if index > 0 else None,
        "后一拍": beats[index + 1] if index + 1 < len(beats) else None,
        "用户要求": _text(instruction, 500),
    }, ensure_ascii=False)
    salt = f"{episode.id}|{beat.get('id')}|{beat.get('summary')}|{instruction}"
    result = drama_agent.chat_json(
        kind="rewrite_beat", system=_BEAT_SYSTEM, user=user,
        user_id=user_id, cache_salt=salt,
    )
    data = result["data"]
    cleaned = {
        "title": _text(data.get("title"), 60) or beat.get("title", ""),
        "summary": _text(data.get("summary"), 400) or beat.get("summary", ""),
        "mood": _text(data.get("mood"), 40) or beat.get("mood", ""),
        "location": _text(data.get("location"), 80) or beat.get("location", ""),
        "dialogue_hint": _text(data.get("dialogue_hint"), 400),
    }
    return _payload({**result, "data": cleaned})


# ---------------------------------------------------------------- P0 返修意见扩写
_REPAIR_SYSTEM = """你是短剧返修指导。用户对某一镜不满意，只给了一句口语意见。
把它扩写成可直接追加到重采样提示词里的中文返修要求。只输出严格 JSON。

输出：
{"reason": "60–160 字，明确改成什么样（主体动作/表情/构图/光线中要调整的点）",
 "keep": ["必须保留的点，最多 3 条"], "avoid": ["必须避免的点，最多 3 条"]}

要求：
1. 不要重复首尾帧锚定、不要写“保持连续/不要切镜”这类系统已自动处理的约束；
2. 不引入原镜头没有的人物、道具或剧情；
3. 画面级、可执行，避免“更好看/更自然”这类空泛评价。"""


def repair_brief(
    *,
    shot,
    project,
    scene,
    characters: Sequence,
    prev_shot=None,
    next_shot=None,
    reason: str,
    user_id: int = 0,
) -> dict:
    """把用户一句话扩写成可执行的返修要求（P0）。"""
    user = json.dumps({
        "任务": "扩写返修要求",
        "项目风格": (project.style_bible if project else "") or "",
        "当前镜头": _shot_text(shot),
        "场景": _scene_text(scene),
        "出场角色": _character_text(characters),
        "上一镜": _shot_text(prev_shot) if prev_shot is not None else None,
        "下一镜": _shot_text(next_shot) if next_shot is not None else None,
        "用户意见": _text(reason, 1000),
    }, ensure_ascii=False)
    salt = f"{shot.id}|{shot.prompt}|{reason}"
    result = drama_agent.chat_json(
        kind="repair_brief", system=_REPAIR_SYSTEM, user=user,
        user_id=user_id, cache_salt=salt,
    )
    data = result["data"]
    text = _text(data.get("reason"), 1200) or _text(reason, 1000)
    keep = [_text(item, 120) for item in (data.get("keep") or [])][:3]
    avoid = [_text(item, 120) for item in (data.get("avoid") or [])][:3]
    lines = [text]
    if keep:
        lines.append("保留：" + "；".join(keep))
    if avoid:
        lines.append("避免：" + "；".join(avoid))
    return _payload({**result, "data": {"brief": "\n".join(lines), "keep": keep, "avoid": avoid}})


# ---------------------------------------------------------------- P1 定妆图反填
_DESCRIBE_SYSTEM = """你是短剧角色卡整理助手。user 消息里是该角色的定妆图。
只输出严格 JSON：{"appearance": "...", "costume": "...", "gender": "男|女|未知", "age": "..."}

铁律：
1. 只描述图中可见属性；看不清或不确定的一律留空字符串，禁止推测；
2. 不写职业、性格、剧情、情绪；不编造图中没有的饰品、纹身、道具、文字；
3. appearance 40–100 字：年龄段、脸型、发型发色、肤色、明显面部特征、体型；
4. costume 30–80 字：上衣/下装/鞋/配饰的款式与颜色；
5. age 用 “20s/30s/40s/50s” 或空；gender 只允许 男/女/未知。"""


def describe_character(*, char, image_path, project_style: str = "", user_id: int = 0) -> dict:
    """按定妆图反填角色外形/服装（P1 视觉）。"""
    user = json.dumps({
        "任务": "按定妆图补全角色卡文字",
        "角色名": char.name,
        "现有外形": char.appearance or "",
        "现有服装": char.costume or "",
        "项目风格": project_style or "",
    }, ensure_ascii=False)
    result = drama_agent.chat_json(
        kind="describe_character", system=_DESCRIBE_SYSTEM, user=user,
        images=[image_path], user_id=user_id, cache_salt=f"{char.id}|{image_path}",
    )
    data = result["data"]
    gender = _text(data.get("gender"), 8)
    if gender not in ("男", "女", "未知"):
        gender = "未知" if gender else ""
    return _payload({**result, "data": {
        "appearance": _text(data.get("appearance"), 1000),
        "costume": _text(data.get("costume"), 1000),
        "gender": gender,
        "age": _text(data.get("age"), 16),
    }})


_CHECK_SYSTEM = """你是角色卡一致性检查员。user 消息包含定妆图与角色文字描述。
只输出严格 JSON：
{"consistent": true/false, "summary": "一句话结论（≤40 字）",
 "diffs": [{"field": "appearance|costume|gender|age", "in_image": "图中是什么",
            "in_text": "文字写的是什么", "severity": "high|mid|low"}]}

判断标准：
1. 以图为准；只报“文字与图明显冲突”的项：发型/发色、年龄段、性别、服装主色或款式；
2. 看不清、图中被遮挡、文字本身为空都不算冲突；
3. 最多 4 条；没有冲突时 diffs 为空数组、consistent=true。"""


def check_character(*, char, image_path, user_id: int = 0) -> dict:
    """定妆图与角色文字的一致性校验（P1 视觉）。"""
    user = json.dumps({
        "任务": "判断文字描述与定妆图是否一致",
        "角色名": char.name,
        "文字-性别": char.gender or "",
        "文字-年龄": char.age or "",
        "文字-外形": char.appearance or "",
        "文字-服装": char.costume or "",
    }, ensure_ascii=False)
    result = drama_agent.chat_json(
        kind="check_character", system=_CHECK_SYSTEM, user=user,
        images=[image_path], user_id=user_id, cache_salt=f"{char.id}|{image_path}",
    )
    data = result["data"]
    diffs = []
    for item in (data.get("diffs") or [])[:4]:
        if not isinstance(item, dict):
            continue
        diffs.append({
            "field": _text(item.get("field"), 20) or "appearance",
            "in_image": _text(item.get("in_image"), 200),
            "in_text": _text(item.get("in_text"), 200),
            "severity": _text(item.get("severity"), 8) if _text(item.get("severity"), 8) in _SEVERITIES else "mid",
        })
    return _payload({**result, "data": {
        "consistent": bool(data.get("consistent")) and not diffs,
        "summary": _text(data.get("summary"), 80),
        "diffs": diffs,
    }})


# ---------------------------------------------------------------- P1 审片预检
_PRECHECK_SYSTEM = """你是短剧成片质检员。user 消息包含一张联系表（按镜号从左到右、从上到下排列，
每格是该镜的代表帧）以及镜头清单。只输出严格 JSON：
{"results": [{"shot_no": 1, "score": 0, "issues": [
   {"type": "identity|artifact|text|composition|lighting|continuity",
    "severity": "high|mid|low", "evidence": "画面里的具体现象", "suggestion": "可直接写进返修要求的做法"}]}],
 "summary": "整集一句话结论（≤50 字）"}

逐镜检查：
1. 人物是否与角色卡一致（发型/发色/年龄段/服装主色）；同一角色跨镜是否明显换脸；
2. 是否出现多余人物、多余肢体、手部畸形、穿模等明显瑕疵；
3. 画面内是否有字幕、水印、文字叠加、台标；
4. 主体是否完整（不裁头、不裁脚）、构图是否失衡；
5. 光线与色调是否与本集风格一致、是否出现跨镜突变。

约束：
- 看不清或不确定的问题不要报（宁可漏报，避免误报）；
- 每镜最多 3 条；没有问题时 issues 为空、score ≥ 85；
- evidence 必须指向画面中的具体部位或现象；suggestion 要可执行；
- shot_no 必须来自镜头清单，不得编造。"""


def episode_sheet(clip_paths: Sequence, out: Path) -> Optional[Path]:
    """整集联系表：复用 drama_media.make_sheet（每镜抽中间帧 tile 拼图）。"""
    try:
        return drama_media.make_sheet(list(clip_paths), out, cols=4)
    except Exception as exc:  # noqa: BLE001 - 图片准备失败只降级为纯文本
        logger.warning("审片联系表生成失败（降级为纯文本预检）：%s", exc)
        return None


def shot_sheet(clip_path, out: Path, count: int = 3) -> Optional[Path]:
    """单镜多帧拼图：首/中/尾（最多 count 帧）横排，便于看动作连续性。"""
    clip = Path(str(clip_path))
    if not clip.exists():
        return None
    try:
        total = max(1, int(round(drama_media.probe_duration(clip) * 24)))
        picks = [0, total // 2, max(0, total - 1)][:max(1, count)]
        tmp = Path(tempfile.mkdtemp(prefix="drama_ai_frames_"))
        for i, index in enumerate(picks):
            drama_media.extract_frame(clip, index, tmp / f"f_{i:03d}.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        drama_media.run_ffmpeg([
            "ffmpeg", "-y", "-v", "error", "-i", str(tmp / "f_%03d.png"),
            "-vf", f"tile={len(picks)}x1:padding=4:margin=4", "-frames:v", "1",
            "-q:v", "3", str(out),
        ])
        return out if out.exists() else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("单镜抽帧失败（降级为纯文本预检）：%s", exc)
        return None


def precheck(
    *,
    episode,
    shots: Sequence,
    scenes: Sequence,
    characters: Sequence,
    image_paths: Sequence,
    scope: str = "episode",
    user_id: int = 0,
    force: bool = False,
) -> dict:
    """审片 AI 预检（P1 视觉）：返回逐镜 issues；调用方负责按 spec_hash 落库。"""
    scene_by_no = {s.scene_no: s for s in scenes}
    char_by_id = {c.id: c for c in characters}
    char_name = {c.id: c.name for c in characters}
    rows = []
    for shot in shots:
        scene = scene_by_no.get(shot.scene_no)
        ids = []
        try:
            ids = json.loads(shot.character_ids or "[]")
        except (TypeError, ValueError):
            ids = []
        rows.append({
            "镜号": shot.shot_no,
            "时长秒": shot.duration_s,
            "画面": (shot.prompt or "")[:300],
            "场景": scene.name if scene else "",
            "角色": [char_name.get(int(cid), "") for cid in ids if int(cid) in char_by_id],
            "台词": shot.dialogue or "",
        })
    user = json.dumps({
        "任务": "逐镜检查成片质量",
        "检查范围": "整集" if scope == "episode" else "单镜",
        "镜头清单": rows,
        "角色卡": [{"姓名": c.name, "外形": c.appearance or "", "服装": c.costume or ""} for c in characters],
        "图片说明": "联系表按镜头清单顺序排列，每格一镜。",
    }, ensure_ascii=False)
    salt = f"{episode.spec_hash}|{scope}|{len(rows)}"
    result = drama_agent.chat_json(
        kind="precheck", system=_PRECHECK_SYSTEM, user=user,
        images=image_paths, user_id=user_id, cache_salt=salt, force=force,
        max_tokens=2000,
    )
    valid_nos = {shot.shot_no for shot in shots}
    results = []
    for item in (result["data"].get("results") or []):
        if not isinstance(item, dict):
            continue
        try:
            no = int(item.get("shot_no"))
        except (TypeError, ValueError):
            continue
        if no not in valid_nos:
            continue
        issues = []
        for issue in (item.get("issues") or [])[:3]:
            if not isinstance(issue, dict):
                continue
            kind = _text(issue.get("type"), 20)
            severity = _text(issue.get("severity"), 8)
            issues.append({
                "type": kind if kind in _ISSUE_TYPES else "artifact",
                "severity": severity if severity in _SEVERITIES else "mid",
                "evidence": _text(issue.get("evidence"), 200),
                "suggestion": _text(issue.get("suggestion"), 300),
            })
        try:
            score = int(float(item.get("score")))
        except (TypeError, ValueError):
            score = 80
        results.append({"shot_no": no, "score": max(0, min(100, score)), "issues": issues})
    return _payload({**result, "data": {
        "results": results,
        "summary": _text(result["data"].get("summary"), 120),
    }})


# ---------------------------------------------------------------- P1 交付文案
_COPY_SYSTEM = """你是短剧发行文案。只输出严格 JSON：
{"title": "≤14 字，有钩子", "logline": "≤40 字，一句话讲清冲突",
 "synopsis": "80–160 字，不剧透结局", "cover_prompt": "60–120 字封面构图描述"}

要求：
1. 只使用给定剧本里的人物与事件，不编造；
2. cover_prompt 只写主体、场景、光线、构图，不写文字/水印/logo，不写像素与比例；
3. 文案口语化、有具体画面，不用“震撼/炸裂/必看”等空泛词。"""


def deliver_copy(*, project, episode, shots: Sequence, user_id: int = 0) -> dict:
    """交付文案（P1 文本）：标题/一句话简介/梗概/封面提示词，只展示不写库。"""
    user = json.dumps({
        "任务": "写发行文案",
        "项目": project.title if project else "",
        "体裁": project.genre if project else "",
        "风格": project.style_bible if project else "",
        "本集标题": episode.title or "",
        "本集剧本": (episode.script or "")[:3000],
        "镜头摘要": [_text(getattr(s, "prompt", ""), 120) for s in list(shots)[:12]],
        "成片时长秒": round(float(episode.final_duration or 0), 1),
    }, ensure_ascii=False)
    result = drama_agent.chat_json(
        kind="deliver_copy", system=_COPY_SYSTEM, user=user,
        user_id=user_id, cache_salt=f"{episode.id}|{episode.spec_hash}",
    )
    data = result["data"]
    return _payload({**result, "data": {
        "title": _text(data.get("title"), 40),
        "logline": _text(data.get("logline"), 80),
        "synopsis": _text(data.get("synopsis"), 600),
        "cover_prompt": _text(data.get("cover_prompt"), 400),
    }})
