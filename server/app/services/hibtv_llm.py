"""短剧工作台：LLM 编排 + 确定性兜底。

两条路径：
1. **LLM 路径**（`settings.hibtv_llm_enabled`）：把 vendored skill（SKILL.md + references/schema.md）
   整段塞进 system，user 侧给「剧本/美术设定解析结果 + 本阶段任务 + 输出约束」，要求严格 JSON；
   解析失败重试一次（第二次把质量门错误清单一起喂回去），仍失败则落 error 交给兜底。
2. **兜底路径**（无 key / HIBTV_LLM=off / LLM 失败）：用 `hibtv_parse` 的确定性结果直接成文，
   结构与 skill schema 一致，保证整条链路在离线环境可跑通、可验收。

铁律：台词逐字保留（只按标点切句，不改写）；禁角色名进 H3 正文（官方规范）；
段 ≤ HIBTV_SEG_MAX_SECONDS 秒、切 2–5 秒。
"""
import json
import logging
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

SKILL_ROOT = Path(__file__).resolve().parent / "skills" / "shuohao-skills" / "skills"

# kind → (skill 目录, 需要装载的参考文件)
SKILL_FILES: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "outline": ("novel-outline", ("SKILL.md", "references/schema.md")),
    "cast": ("novel-characters", ("SKILL.md", "references/schema.md")),
    "art": ("novel-art", ("SKILL.md", "references/schema.md",
                          "references/scene-pass.md", "references/prop-pass.md")),
    "script": ("novel-script", ("SKILL.md", "references/schema.md")),
    "storyboard": ("novel-storyboard", ("SKILL.md", "references/schema.md",
                                       "references/storyboard-pass.md",
                                       "references/shot-writing.md",
                                       "references/h3-prompt.md")),
}

_FILE_CAP = 24000
_TOTAL_CAP = 90000

CHARS_PER_SECOND = 4.5     # 台词语速（字/秒，标点算时间）
ACTION_SECONDS = 2.5       # 一个动作节拍的秒数
MAX_LINE_CHARS = 15        # 单句上限（只切句不改写，保证 2–5 秒的切镜装得下）
MIN_CUT_SECONDS = 2.0
MAX_CUT_SECONDS = 5.0

SHOT_SIZES = {
    "extreme-wide": "大远景", "wide": "全景", "medium": "中景",
    "close": "特写", "extreme-close": "大特写",
}
CAMERA_ZH = {
    "Static Shot": "固定", "Push In": "推", "Pull Out": "拉", "Zoom In": "变焦推",
    "Zoom Out": "变焦拉", "Pan Left": "左摇", "Pan Right": "右摇", "Truck Left": "左移",
    "Truck Right": "右移", "Tilt Up": "仰摇", "Tilt Down": "俯摇", "Pedestal Up": "升",
    "Pedestal Down": "降", "Arc Shot": "环绕", "Tracking Shot": "跟",
}

OUTPUT_RULE = """
【输出约束（最高优先级）】
- 只输出一个 JSON 对象，不要 Markdown 代码围栏、不要解释、不要前后缀。
- 结构必须严格符合随附 schema；缺字段 = 不合格。
- 台词、场次、集号来自 user 给的解析结果，**逐字保留**，禁止改写、禁止新增剧情。
- 不写任何 schema 之外的字段。
"""


class HibtvLLMError(Exception):
    """LLM 调用 / 解析失败；调用方回退确定性骨架。"""


# ------------------------------------------------------------------ skill 装载
_CACHE: Dict[str, str] = {}


def load_skill(kind: str) -> str:
    """把某个 skill 的 SKILL.md + references/schema.md 拼成 system 附件；进程内缓存。"""
    if kind in _CACHE:
        return _CACHE[kind]
    entry = SKILL_FILES.get(kind)
    if not entry:
        _CACHE[kind] = ""
        return ""
    skill_dir, files = entry
    base = SKILL_ROOT / skill_dir
    parts: List[str] = []
    for rel in files:
        fp = base / rel
        try:
            txt = fp.read_text(encoding="utf-8").strip()
        except OSError:
            logger.warning("短剧 skill 文件缺失：%s（跳过）", fp)
            continue
        if len(txt) > _FILE_CAP:
            txt = txt[:_FILE_CAP] + "\n…[truncated]"
        parts.append(f"<!-- {skill_dir}/{rel} -->\n{txt}")
    blob = "\n\n---\n\n".join(parts)
    if len(blob) > _TOTAL_CAP:
        blob = blob[:_TOTAL_CAP] + "\n…[truncated]"
    _CACHE[kind] = blob
    return blob


def _system(kind: str) -> str:
    skill = load_skill(kind)
    head = (
        "# 短剧工作台 · 结构化产物生成器\n\n"
        "你是短剧生产流水线上的结构化文档生成器。输入是**已经定稿的剧本**与**美术设定**，"
        "你的工作只有「归类与结构化」——**不做改编**：不拆书、不砍支线、不新增剧情、不改台词。"
        "所有集号、场号、角色名、台词都必须能在输入里逐字找到出处。\n"
    )
    return head + OUTPUT_RULE + ("\n\n---\n\n# 附：skill 规范\n\n" + skill if skill else "")


def _extract_json(raw: str) -> dict:
    """从模型回复里取 JSON 对象：兼容代码围栏 / 前后缀说明。"""
    t = (raw or "").strip()
    if not t:
        raise HibtvLLMError("LLM 输出为空")
    fence = re.search(r"```[a-zA-Z0-9_-]*\s*([\s\S]*?)```", t)
    if fence:
        t = fence.group(1).strip()
    if not t.startswith("{"):
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            t = t[a:b + 1]
    try:
        obj = json.loads(t)
    except ValueError as exc:
        raise HibtvLLMError(f"LLM 输出不是合法 JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise HibtvLLMError("LLM 输出不是 JSON 对象")
    return obj


async def chat_json(kind: str, user_text: str, *, max_tokens: Optional[int] = None) -> dict:
    """调 deepseek（OpenAI 兼容）产出严格 JSON。失败抛 HibtvLLMError。"""
    provider = settings.hibtv_llm_provider_name
    api_key = settings.hibtv_llm_api_key
    if not api_key:
        raise HibtvLLMError(f"未配置 {provider} API Key")
    body = {
        "model": settings.hibtv_llm_model_name,
        "messages": [
            {"role": "system", "content": _system(kind)},
            {"role": "user", "content": user_text},
        ],
        "temperature": float(settings.hibtv_llm_temperature),
        "max_tokens": int(max_tokens or settings.hibtv_llm_max_tokens),
        "stream": False,
    }
    if provider == "deepseek":
        body["thinking"] = {"type": "disabled"}
    else:
        body["enable_thinking"] = False
    url = f"{settings.hibtv_llm_api_base.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=float(settings.hibtv_llm_timeout)) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HibtvLLMError(f"LLM({provider}) 请求失败: {exc}") from exc
    try:
        content = (payload.get("choices") or [{}])[0].get("message", {}).get("content", "")
    except (AttributeError, IndexError, TypeError) as exc:
        raise HibtvLLMError(f"LLM({provider}) 响应结构异常: {exc}") from exc
    return _extract_json(content if isinstance(content, str) else "")


# ------------------------------------------------------------------ 文本小工具
_PUNCT = "，。！？；：、,.!?;:"
_QUOTES = "「」『』“”\"'"


def split_line(text: str, limit: int = MAX_LINE_CHARS) -> List[str]:
    """按标点把长台词切成 ≤ limit 字的多段（**逐字保留**，只是切句）。"""
    raw = (text or "").strip()
    if not raw:
        return []
    if len(raw) <= limit:
        return [raw]
    chunks: List[str] = []
    buf = ""
    for ch in raw:
        buf += ch
        if ch in _PUNCT and len(buf) >= max(4, limit - 6):
            chunks.append(buf)
            buf = ""
        elif len(buf) >= limit:
            chunks.append(buf)
            buf = ""
    if buf.strip():
        chunks.append(buf)
    # 合并过短的尾段，避免切出一堆 1–2 字的小句
    merged: List[str] = []
    for c in chunks:
        if merged and len(c) <= 3:
            merged[-1] += c
        else:
            merged.append(c)
    return [c.strip() for c in merged if c.strip()]


def strip_quotes(text: str) -> str:
    """去掉引号对白（大纲/动作节拍的叙述体要求）。"""
    out = str(text or "")
    for q in _QUOTES:
        out = out.replace(q, "")
    return out.strip()


def _clip(text: str, n: int) -> str:
    t = strip_quotes(re.sub(r"\s+", " ", str(text or "")).strip())
    return t if len(t) <= n else t[: n - 1] + "…"


def line_seconds(text: str) -> float:
    return round(len(re.sub(r"\s", "", text or "")) / CHARS_PER_SECOND, 2)


def beat_seconds(beat: Dict) -> float:
    if beat.get("line"):
        return max(1.0, line_seconds(beat["line"]))
    return ACTION_SECONDS


# ------------------------------------------------------------------ 兜底：大纲
def ensure_reuse_plan(outline: Dict) -> int:
    """给只出现一次的场景补「规避方案」——单集项目的每个场景天然只用一次。

    novel-outline 的质量门要求 `scenes[].reusePlan` 非空（否则等于要搭一次性布景），
    但 LLM 不会主动写。这里按出现次数确定性补齐，返回补了几条。
    """
    usage: Dict[str, int] = {}
    for ep in outline.get("episodes") or []:
        for sid in ep.get("scenes") or []:
            key = str(sid)
            usage[key] = usage.get(key, 0) + 1
    fixed = 0
    for scene in outline.get("scenes") or []:
        sid = str(scene.get("id") or "")
        if str(scene.get("reusePlan") or "").strip():
            continue
        if usage.get(sid, 0) <= 1:
            scene["reusePlan"] = (
                "本集一次性场景：与主场景共用同一面墙与同一组家具，只换陈设与光位，"
                "不另搭新布景；近景用同一角落反向拍摄。"
            )
            fixed += 1
    return fixed


def ensure_cast_ids(data: Dict) -> int:
    """给角色补稳定 id（C01…）——cast 文档跨章节对齐、资产 code 都靠它。"""
    fixed = 0
    for i, item in enumerate(data.get("characters") or []):
        if str(item.get("id") or "").strip():
            continue
        item["id"] = f"C{i + 1:02d}"
        fixed += 1
    return fixed


def _tier(index: int) -> str:
    if index < 3:
        return "lead"
    if index < 12:
        return "support"
    return "functional"


def outline_from_parse(project, script: Dict, art: Dict) -> Dict:
    """用剧本 + 美术设定解析结果直接成文（skill schema）。"""
    eps = script.get("episodes") or []
    total_eps = max(1, int(project.episodes or len(eps) or 1))
    art_cast = art.get("cast") or []
    art_scene = art.get("scene") or []
    art_prop = art.get("prop") or []

    # ---- 角色表：美术设定优先，剧本里出现但设定集漏掉的角色按「待补设定」补进来 ----
    characters: List[Dict] = []
    seen: Dict[str, str] = {}
    for i, item in enumerate(art_cast):
        cid = item["code"] if re.match(r"^C\d{2}$", item.get("code") or "") else f"C{i + 1:02d}"
        rows = item.get("fields") or {}
        characters.append({
            "id": cid,
            "name": item["name"],
            "role": rows.get("身份") or rows.get("定位") or _tier(i),
            "tier": _tier(i),
            "arc": _clip(item.get("text") or rows.get("弧光") or "", 160) or f"{item['name']}在剧中的转变。",
            "from": ["定稿剧本"],
        })
        seen[item["name"]] = cid
    for name, count in (script.get("stats", {}).get("cast") or []):
        if name in seen or name in ("动作", "旁白", "VO"):
            continue
        cid = f"C{len(characters) + 1:02d}"
        characters.append({
            "id": cid, "name": name,
            "role": "剧本出场角色（待补美术设定）",
            "tier": _tier(len(characters)),
            "arc": "", "from": ["定稿剧本"],
        })
        seen[name] = cid
    if not characters:
        characters.append({"id": "C01", "name": "主角", "role": "主角", "tier": "lead",
                           "arc": "在剧中完成一次身份反转。", "from": ["定稿剧本"]})

    # ---- 场景表 ----
    scenes: List[Dict] = []
    scene_by_name: Dict[str, str] = {}
    for i, item in enumerate(art_scene):
        sid = item["code"] if re.match(r"^S\d{2}$", item.get("code") or "") else f"S{i + 1:02d}"
        scenes.append({"id": sid, "name": item["name"], "primary": i < 4,
                       "reusePlan": "" if i < 4 else "复用同一环境资产，换时段与光线条件改出"})
        scene_by_name[item["name"]] = sid
    for name, _count in (script.get("stats", {}).get("locations") or []):
        if name in scene_by_name:
            continue
        sid = f"S{len(scenes) + 1:02d}"
        scenes.append({"id": sid, "name": name, "primary": False,
                       "reusePlan": "复用同一环境资产，换时段与光线条件改出"})
        scene_by_name[name] = sid
    if not scenes:
        scenes.append({"id": "S01", "name": "主场景", "primary": True, "reusePlan": ""})

    # ---- 道具表 ----
    props: List[Dict] = []
    prop_by_name: Dict[str, str] = {}
    for i, item in enumerate(art_prop):
        pid = item["code"] if re.match(r"^P\d{2}$", item.get("code") or "") else f"P{i + 1:02d}"
        rows = item.get("fields") or {}
        props.append({
            "id": pid, "name": item["name"],
            "function": _clip(rows.get("作用") or rows.get("功能") or item.get("text") or "", 80)
                        or f"{item['name']}承载关键剧情信息。",
            "beatIds": [],
        })
        prop_by_name[item["name"]] = pid

    # ---- 爽点表：每集一个 major，保证间隔与「最早 major 不在最后一集」----
    beat_types = ["身份反转", "当众打脸", "信息揭破", "关系倒转", "赌约兑现"]
    beats: List[Dict] = []
    for ep in eps:
        ep_no = int(ep["ep"])
        beats.append({
            "id": f"B{len(beats) + 1:02d}",
            "type": beat_types[(ep_no - 1) % len(beat_types)],
            "weight": "major",
            "episode": ep_no,
            "setup": _clip(ep.get("summary") or (ep["scenes"][0]["title"] if ep["scenes"] else ""), 80)
                     or f"第 {ep_no} 集开场冲突",
            "payoff": _clip(ep.get("cliff") or ep.get("hook") or "", 80) or f"第 {ep_no} 集情绪落点",
        })

    # ---- 分集 ----
    episodes: List[Dict] = []
    for ep in eps:
        ep_no = int(ep["ep"])
        char_ids: List[str] = []
        scene_ids: List[str] = []
        prop_ids: List[str] = []
        first_line = ""
        last_line = ""
        for sc in ep["scenes"]:
            sid = scene_by_name.get(sc["location"]) or scene_by_name.get(sc["title"])
            if sid and sid not in scene_ids:
                scene_ids.append(sid)
            for name in sc["characters"]:
                cid = seen.get(name)
                if cid and cid not in char_ids:
                    char_ids.append(cid)
            for name in sc["props"]:
                pid = prop_by_name.get(name)
                if pid and pid not in prop_ids:
                    prop_ids.append(pid)
            for b in sc["beats"]:
                if b["kind"] == "line":
                    first_line = first_line or f"{b['who']}开口，{_clip(b['line'], 22)}"
                    last_line = f"{b['who']}说完最后一句话"
        if not scene_ids:
            scene_ids = [scenes[0]["id"]]
        if not char_ids:
            char_ids = [characters[0]["id"]]
        synopsis = (ep.get("summary")
                    or f"第 {ep_no} 集：{_clip(ep['scenes'][0]['title'], 30) if ep['scenes'] else '推进主线'}，"
                       f"共 {len(ep['scenes'])} 场。")
        episodes.append({
            "ep": ep_no,
            "synopsis": _clip(synopsis, 220),
            "hook": _clip(ep.get("hook") or first_line or f"第 {ep_no} 集开场直接进冲突。", 120),
            "suspense": _clip(ep.get("suspense") or last_line or "这一集停在最贵的一拍上。", 120),
            "sceneIds": scene_ids,
            "characterIds": char_ids,
            "propIds": prop_ids,
            "crowdPlan": "三人分处不同场次，无同框，分场拍。" if len(char_ids) >= 3 else "",
            "warnings": [],
        })
    if not episodes:
        episodes.append({
            "ep": 1, "synopsis": "剧本尚未解析出分集信息。",
            "hook": "上传的剧本里没有识别到“第 N 集”标记。",
            "suspense": "补齐集号标记后重新生成大纲。",
            "sceneIds": [scenes[0]["id"]], "characterIds": [characters[0]["id"]],
            "propIds": [], "crowdPlan": "", "warnings": ["未识别到分集"],
        })

    core = _clip(episodes[0]["synopsis"], 160)
    # 只被一集用到的场景必须给出复用/规避方案（skill 的 once-scene 门）
    used: Dict[str, int] = {}
    for row in episodes:
        for sid in row["sceneIds"]:
            used[sid] = used.get(sid, 0) + 1
    for scene in scenes:
        if used.get(scene["id"], 0) <= 1 and not (scene.get("reusePlan") or "").strip():
            scene["reusePlan"] = "复用同一环境资产：换时段与光线改出，机位限制在同一侧墙内"
    keeps = [{"what": _clip(project.keeps, 120), "why": "用户指定必须保留的场次 / 名场面"}] \
        if (project.keeps or "").strip() else \
        [{"what": _clip(episodes[0]["synopsis"], 80), "why": "按定稿剧本原样保留，不做改编"}]

    return {
        "source": project.name,
        "lang": "zh",
        "params": {
            "episodes": max(total_eps, len(episodes)),
            "minutesPerEpisode": max(1, int(project.minutes_min or 2)),
            "genre": project.genre or "都市情感",
            "adaptMode": "忠实",
            "preferences": [],
        },
        "adaptation": {"core": core or project.name, "keep": keeps, "cut": [], "merge": [], "risks": []},
        "characters": characters,
        "scenes": scenes,
        "props": props,
        "beats": beats,
        "episodes": episodes,
    }


# ------------------------------------------------------------------ 兜底：角色 / 美术
def cast_from_parse(project, art: Dict, outline: Dict) -> Dict:
    by_code = {c["id"]: c for c in (outline.get("characters") or [])}
    characters: List[Dict] = []
    for i, item in enumerate(art.get("cast") or []):
        rows = item.get("fields") or {}
        code = item.get("code") if re.match(r"^C\d{2}$", item.get("code") or "") else f"C{i + 1:02d}"
        base = by_code.get(code) or {}
        importance = {"lead": "protagonist", "support": "supporting", "functional": "minor"}.get(
            base.get("tier", "support"), "supporting")
        appearance = _clip(rows.get("外观") or rows.get("外形") or item.get("text") or "", 400)
        characters.append({
            "id": code,
            "code": code,
            "name": item["name"],
            "aliases": [a.strip() for a in re.split(r"[、,，/／]", rows.get("别名") or "") if a.strip()][:6],
            "importance": importance,
            "oneLiner": _clip(base.get("arc") or rows.get("一句话") or "", 80) or f"{item['name']}的关键转变。",
            "persona": {
                "gender": rows.get("性别", ""),
                "ageRange": rows.get("年龄", ""),
                "identity": rows.get("身份", ""),
                "appearance": appearance,
                "personality": _clip(rows.get("性格") or "", 200),
                "temperament": _clip(rows.get("气质") or "", 120),
                "motivation": _clip(rows.get("动机") or "", 200),
                "arc": _clip(base.get("arc") or "", 200),
                "relationships": _clip(rows.get("关系") or "", 300),
                "evidence": "定稿剧本 / 美术设定",
            },
            "image": {
                "prompt": appearance or f"{item['name']}，{rows.get('身份', '')}，当代中国都市写实风格全身像。",
                "promptLocal": "",
                "negativePrompt": "多个人物, 拼贴, 分屏, 文字, 水印",
                "tags": [rows.get("性别", ""), rows.get("年龄", "")],
                "sheet": "三视图：正面全身 / 侧面 / 背面",
            },
            "voice": {
                "timbre": rows.get("音色", ""), "pitch": "", "pace": "",
                "accent": "", "emotion": "", "prompt": rows.get("音色", ""), "referenceHint": "",
            },
        })
    return {
        "source": project.name, "lang": "zh",
        "summary": f"{project.name} 的角色设定集（来自美术设定原文）。",
        "characters": characters,
    }


def art_from_parse(project, art: Dict, outline: Dict) -> Dict:
    scenes: List[Dict] = []
    scene_usage: Dict[str, List[int]] = {}
    for ep in outline.get("episodes") or []:
        for sid in ep.get("sceneIds") or []:
            scene_usage.setdefault(sid, []).append(int(ep.get("ep") or 0))
    for i, item in enumerate(art.get("scene") or []):
        rows = item.get("fields") or {}
        sid = item.get("code") or f"S{i + 1:02d}"
        scenes.append({
            "id": sid,
            "code": sid,
            "name": item["name"],
            "primary": any(s["id"] == sid and s.get("primary") for s in (outline.get("scenes") or [])),
            "summary": _clip(rows.get("功能") or item.get("text") or "", 240),
            "anchors": [{"name": "空间骨架", "desc": _clip(item.get("text") or "", 240)}],
            "lighting": [{"state": rows.get("光线") or "默认", "prompt": _clip(item.get("text") or "", 200)}],
            "image": {
                "prompt": _clip(item.get("text") or "", 500) or f"{item['name']}，空无一人，当代中国都市写实场景。",
                "negativePrompt": "多个人物, 文字, 水印",
                "sheet": "正面 / 俯视 / 背面 / 左侧 / 右侧",
                "tags": ["场景"],
            },
            "usage": {"episodes": sorted(set(scene_usage.get(sid, []))), "beats": []},
        })
    props: List[Dict] = []
    for i, item in enumerate(art.get("prop") or []):
        rows = item.get("fields") or {}
        pid = item.get("code") or f"P{i + 1:02d}"
        props.append({
            "id": pid,
            "code": pid,
            "name": item["name"],
            "scale": rows.get("尺度") or "手持级",
            "summary": _clip(rows.get("作用") or item.get("text") or "", 200),
            "anchors": [{"name": "本体", "desc": _clip(item.get("text") or "", 200)}],
            "states": [{"state": rows.get("状态") or "主状态", "prompt": _clip(item.get("text") or "", 200)}],
            "relatedScenes": [],
            "carriedBy": [],
            "image": {
                "prompt": _clip(item.get("text") or "", 400) or f"{item['name']}，单独置于纯色背景正中，无人无手。",
                "negativePrompt": "人物, 手, 文字, 水印",
                "sheet": "正面 / 背面 / 左侧 / 右侧 / 俯视",
                "tags": ["道具"],
            },
            "usage": {"episodes": [], "beats": []},
        })
    return {"source": project.name, "lang": "zh", "scenes": scenes, "props": props}


# ------------------------------------------------------------------ 兜底：剧本（结构层）
def script_from_parse(project, ep: Dict, outline: Dict) -> Dict:
    """把一集的解析结果结构化成 script.json（台词逐字，只按标点切句）。"""
    char_by_name = {c["name"]: c["id"] for c in (outline.get("characters") or [])}
    scene_by_name = {s["name"]: s["id"] for s in (outline.get("scenes") or [])}

    scenes: List[Dict] = []
    for sc in ep["scenes"]:
        sid = scene_by_name.get(sc["location"]) or scene_by_name.get(sc["title"]) or (
            (outline.get("scenes") or [{"id": "S01"}])[0]["id"])
        chars: List[str] = []
        for name in sc["characters"]:
            cid = char_by_name.get(name)
            if cid and cid not in chars:
                chars.append(cid)
        flow: List[Dict] = []
        for b in sc["beats"]:
            if b["kind"] == "line":
                speaker = char_by_name.get(b["who"], "VO")
                if speaker == "VO" and speaker not in chars:
                    pass
                elif speaker not in chars:
                    chars.append(speaker)
                for piece in split_line(b["line"]):
                    flow.append({
                        "speaker": speaker,
                        "line": piece,
                        "delivery": _clip(b.get("note") or "", 40),
                    })
            else:
                flow.append({"action": strip_quotes(b["line"])[:200]})
        if not flow:
            flow.append({"action": f"{sc['title']}，空镜过渡。"})
        scenes.append({
            "sceneId": sid,
            "lighting": "",
            "characters": chars,
            "props": [],
            "flow": flow,
        })
    if not scenes:
        scenes = [{"sceneId": (outline.get("scenes") or [{"id": "S01"}])[0]["id"],
                   "characters": [], "flow": [{"action": "本集无场次内容。"}]}]

    est = 0.0
    for sc in scenes:
        for b in sc["flow"]:
            est += beat_seconds(b)
    target = max(30.0, round(est, 1))

    return {
        "source": project.name,
        "params": {"charsPerSecond": CHARS_PER_SECOND, "actionSeconds": ACTION_SECONDS,
                   "tolerance": 0.15, "maxLineChars": MAX_LINE_CHARS},
        "episodes": [{
            "ep": int(ep["ep"]),
            "targetSeconds": target,
            "hook": _clip(ep.get("hook") or "开场直接进冲突。", 80),
            "cliff": _clip(ep.get("cliff") or ep.get("suspense") or "结尾停在最贵的一拍。", 80),
            "beatsClaimed": [b["type"] for b in (outline.get("beats") or [])
                             if int(b.get("episode") or 0) == int(ep["ep"])],
            "hookBeat": [1, 1],
            "scenes": scenes,
        }],
    }


# ------------------------------------------------------------------ 兜底：分镜
def _cut_starts(cuts: Sequence[Dict]) -> List[float]:
    starts: List[float] = []
    t = 0.0
    for c in cuts:
        starts.append(round(t, 1))
        t = round(t + float(c.get("seconds") or 0), 1)
    return starts


def h3_cut_time(t: float) -> str:
    m = int(t // 60)
    s = int(t % 60)
    ms = int(round((t - math.floor(t)) * 1000))
    return f"{m:02d}:{s:02d}.{ms:03d}"


def h3_alignment_line(cuts: Sequence[Dict]) -> str:
    """首行对齐指令（promptLang=zh），与 skill 的 h3AlignmentLine 逐字一致。"""
    if not cuts or len(cuts) <= 1:
        return "目标视频在 0.00 秒处完全参照图 1（来自镜头 1）。"
    starts = _cut_starts(cuts)
    parts = [f"图 {i + 1}（来自镜头 {i + 1}）对齐目标视频 {starts[i]:.2f} 秒处"
             for i in range(len(cuts))]
    return "参考图与目标视频的对齐——" + "；".join(parts) + "。"


def cut_body(cut: Dict) -> str:
    """镜头正文 = 运镜词 + 景别 + 画面描述。

    `camera` / `size` 改了必须能改变提示词，否则「改镜头」只是改表格；
    描述里已经写了同一个词时不重复叠加。
    """
    move = CAMERA_ZH.get(str(cut.get("camera") or "Static Shot"), "固定")
    size = SHOT_SIZES.get(str(cut.get("size") or "medium"), "")
    body = str(cut.get("shot") or "").strip()
    head = body[:24]
    lead: List[str] = []
    if move and move not in head:
        lead.append(f"{move}镜头")
    if size and size not in head:
        lead.append(size)
    if lead:
        return f"{'，'.join(lead)}。{body}" if body else f"{'，'.join(lead)}。"
    return body or "空镜。"


def _shot_text(cut: Dict, scene_name: str) -> str:
    """镜头正文：不写角色名、不写秒数、不写图片引用。"""
    move = CAMERA_ZH.get(cut.get("camera") or "Static Shot", "固定")
    parts = [f"{move}镜头。"]
    if cut.get("_has_dialogue"):
        parts.append("主体开口，语速压住，视线不回避。")
    else:
        parts.append("主体完成一个动作，动作停在半途，情绪从压抑转向决断。")
    parts.append(f"背景保持在{strip_quotes(scene_name) or '同一空间'}内，空间关系不变。")
    return "".join(parts)


def _frame_text(cut: Dict, scene_name: str, names: Sequence[str]) -> str:
    size = SHOT_SIZES.get(cut.get("size") or "medium", "中景")
    who = "、".join(names[:2]) or "空镜"
    return f"{size}，{who}在{strip_quotes(scene_name) or '场景'}里，{cut.get('_frame_action')}"


def storyboard_from_script(project, script_doc: Dict, outline: Dict) -> Dict:
    """由 script.json 生成 storyboard.json（段 ≤ maxSegmentSeconds、切 2–5 秒）。"""
    max_seg = max(5.0, float(settings.hibtv_seg_max_seconds))
    ep_doc = (script_doc.get("episodes") or [{}])[0]
    ep_no = int(ep_doc.get("ep") or 1)
    char_name = {c["id"]: c["name"] for c in (outline.get("characters") or [])}
    scene_name_by_id = {s["id"]: s["name"] for s in (outline.get("scenes") or [])}

    segments: List[Dict] = []
    seg_no = 0

    for scene_index, sc in enumerate(ep_doc.get("scenes") or [], start=1):
        flow = sc.get("flow") or []
        if not flow:
            continue
        beats = []
        for i, b in enumerate(flow, start=1):
            beats.append({"n": i, **b, "sec": beat_seconds(b)})

        # ---- 切镜：贪心累积到 3.6s 或再加就超 5s ----
        cuts: List[Dict] = []
        cur: List[Dict] = []
        cur_sec = 0.0
        for b in beats:
            if cur and cur_sec + b["sec"] > MAX_CUT_SECONDS:
                cuts.append({"beats": (cur[0]["n"], cur[-1]["n"]), "rows": cur, "sec": cur_sec})
                cur, cur_sec = [], 0.0
            cur.append(b)
            cur_sec += b["sec"]
            if cur_sec >= 3.6:
                cuts.append({"beats": (cur[0]["n"], cur[-1]["n"]), "rows": cur, "sec": cur_sec})
                cur, cur_sec = [], 0.0
        if cur:
            cuts.append({"beats": (cur[0]["n"], cur[-1]["n"]), "rows": cur, "sec": cur_sec})

        cut_rows: List[Dict] = []
        for idx, c in enumerate(cuts, start=1):
            seconds = round(min(MAX_CUT_SECONDS, max(MIN_CUT_SECONDS, c["sec"])), 1)
            has_dlg = any(r.get("line") for r in c["rows"])
            names = [char_name.get(r.get("speaker") or "", "") for r in c["rows"]]
            names = [n for n in names if n]
            chars = sorted({r.get("speaker") for r in c["rows"] if r.get("speaker") and r["speaker"] != "VO"})
            size = "close" if has_dlg else ("wide" if idx == 1 else "medium")
            camera = "Push In" if has_dlg else ("Static Shot" if idx % 2 else "Pan Right")
            action = "，".join(strip_quotes(r.get("action") or "")[:40] for r in c["rows"] if r.get("action"))
            cut = {
                "beats": [c["beats"][0], c["beats"][1]],
                "seconds": seconds,
                "size": size,
                "camera": camera,
                "characters": chars[:3],
                "props": [],
                "frame": "",
                "shot": "",
                "lens": "50mm 标准，中浅景深" if size in ("medium", "wide") else "85mm 长焦，浅景深",
                "cameraPosition": f"{names[0] if names else '主体'} + 平视正面",
                "composition": "三分法" if idx % 2 else "中心构图",
                "eyeline": "对方面部" if has_dlg else "手中物件",
                "focus": f"锁定{names[0] if names else '主体'}上半身",
                "stability": "stable" if camera == "Static Shot" else "slight-shake",
                "_has_dialogue": has_dlg,
                "_rows": c["rows"],
                "_frame_action": action or "动作停在这一拍，情绪落在眼神上。",
            }
            cut["frame"] = _frame_text(cut, scene_name_by_id.get(sc.get("sceneId") or "", ""), names)
            cut["shot"] = _shot_text(cut, scene_name_by_id.get(sc.get("sceneId") or "", ""))
            cut_rows.append(cut)

        # ---- 组段：同场次内，Σ分镜 ≤ maxSegmentSeconds ----
        group: List[Dict] = []
        total = 0.0
        for cut in cut_rows:
            if group and total + cut["seconds"] > max_seg:
                seg_no += 1
                segments.append(_build_segment(seg_no, ep_no, scene_index, group, sc,
                                               scene_name_by_id, char_name))
                group, total = [], 0.0
            group.append(cut)
            total += cut["seconds"]
        if group:
            seg_no += 1
            segments.append(_build_segment(seg_no, ep_no, scene_index, group, sc,
                                           scene_name_by_id, char_name))

    if not segments:
        segments = [{
            "id": f"E{ep_no:02d}-01",
            "sceneIndex": 1,
            "cuts": [],
            "h3Prompt": "目标视频在 0.00 秒处完全参照图 1（来自镜头 1）。\n整体视听描述：\n[镜头 1] 固定镜头，空镜。\n整体音景：室内环境底噪。\n非叙事配乐：N/A",
            "blocking": "空镜，无人物。",
            "soundscape": "室内环境底噪。",
        }]

    # 去掉内部字段
    for seg in segments:
        for cut in seg["cuts"]:
            for k in [k for k in cut if k.startswith("_")]:
                cut.pop(k, None)

    return {
        "source": project.name,
        "aspectRatio": project.aspect_ratio or "9:16",
        "promptLang": "zh",
        "params": {"maxSegmentSeconds": max_seg, "minCutSeconds": MIN_CUT_SECONDS,
                   "maxCutSeconds": MAX_CUT_SECONDS, "maxOnScreen": 3, "tolerance": 0.15},
        "episodes": [{"ep": ep_no, "segments": segments}],
    }


def _build_segment(seg_no: int, ep_no: int, scene_index: int, cuts: List[Dict], scene: Dict,
                   scene_name_by_id: Dict[str, str], char_name: Dict[str, str]) -> Dict:
    scene_label = scene_name_by_id.get(scene.get("sceneId") or "", "") or "场景"
    # 台词逐字进 <d> 块，其余为中文镜头正文
    desc: List[str] = []
    starts = _cut_starts(cuts)
    for k, cut in enumerate(cuts, start=1):
        mark = "[镜头 1]" if k == 1 else f"[镜头 {k}] 于 {h3_cut_time(starts[k - 1])}，"
        desc.append(f"{mark} {cut_body(cut)}")
    h3 = h3_alignment_line(cuts) + "\n整体视听描述：\n" + "\n".join(desc)

    # 台词块追加在各自镜头段落里（逐字），说话人身份/语气写在 <d> 外（英文）
    dialogue_by_cut: List[str] = []
    for cut in cuts:
        rows = cut.get("_rows") or []
        blocks = []
        for r in rows:
            if not r.get("line"):
                continue
            speaker = r.get("speaker") or "VO"
            voice = "says in an off-screen voiceover" if speaker == "VO" else "says"
            note = f" ({r.get('delivery')})" if r.get("delivery") else ""
            blocks.append(f"The character {voice}{note}: <d>[Chinese] {r['line']}</d>")
        dialogue_by_cut.append(" ".join(blocks))

    if any(dialogue_by_cut):
        lines = h3.split("\n")
        out: List[str] = []
        k = 0
        for ln in lines:
            out.append(ln)
            if ln.startswith("[镜头 "):
                if k < len(dialogue_by_cut) and dialogue_by_cut[k]:
                    out.append(dialogue_by_cut[k])
                k += 1
        h3 = "\n".join(out)

    soundscape = f"{scene_label}的环境底噪与衣料摩擦声，人群低声交谈停在远处。"
    h3 += f"\n整体音景：{soundscape}\n非叙事配乐：低频弦乐铺底，节奏随冲突推进。"

    names = [char_name.get(c, "") for c in (cuts[0].get("characters") or [])]
    names = [n for n in names if n]
    blocking = (f"{'、'.join(names[:2]) or '空镜'}：主动作人站在画面左三分之一，"
                f"对手在画面右三分之一，两人相距约一米，均侧身朝向对方；"
                f"身后留出{scene_label}的空间纵深。")

    for cut in cuts:
        cut.pop("_rows", None)

    return {
        "id": f"E{ep_no:02d}-{seg_no:02d}",
        "sceneIndex": scene_index,
        "cuts": cuts,
        "h3Prompt": h3,
        "blocking": blocking,
        "soundscape": soundscape,
        "note": f"{scene_label} · {len(cuts)} 镜",
    }


# ------------------------------------------------------------------ 镜头重修
DIALOGUE_TAIL = "\n整体音景：{}\n非叙事配乐：低频弦乐铺底，节奏随冲突推进。"


def _beat_range(cut: Dict) -> tuple:
    beats = cut.get("beats") or []
    if not isinstance(beats, (list, tuple)) or not beats:
        return 0, 0
    first = int(beats[0] or 0)
    last = int(beats[1] if len(beats) > 1 else first) or first
    return first, last


def rebuild_h3_prompt(seg: Dict, script_ep: Dict, outline: Dict) -> str:
    """按当前镜头清单确定性地重算一段的 h3Prompt（改镜头后用）。

    与 `_build_segment` 同口径：对齐行由切点秒数逐字推导，逐镜分段带切点时刻，
    台词从剧本节拍逐字取回（cut.beats 指向 script.flow 的拍号，1 起）。
    """
    cuts = seg.get("cuts") or []
    if not cuts:
        return seg.get("h3Prompt") or ""
    scene_index = int(seg.get("sceneIndex") or 1)
    scenes = script_ep.get("scenes") or []
    scene = scenes[scene_index - 1] if 0 < scene_index <= len(scenes) else {}
    scene_id = scene.get("sceneId") or ""
    scene_name = ""
    for item in outline.get("scenes") or []:
        if scene_id and item.get("id") == scene_id:
            scene_name = strip_quotes(item.get("name") or "")
            break
    flow = scene.get("flow") or []

    starts = _cut_starts(cuts)
    desc: List[str] = []
    for k, cut in enumerate(cuts, start=1):
        mark = "[镜头 1]" if k == 1 else f"[镜头 {k}] 于 {h3_cut_time(starts[k - 1])}，"
        desc.append(f"{mark} {cut_body(cut)}".rstrip())
        first, last = _beat_range(cut)
        blocks = []
        for n in range(first, last + 1):
            if not (0 < n <= len(flow)):
                continue
            row = flow[n - 1]
            if not row.get("line"):
                continue
            speaker = row.get("speaker") or "VO"
            voice = "says in an off-screen voiceover" if speaker == "VO" else "says"
            note = f" ({row.get('delivery')})" if row.get("delivery") else ""
            blocks.append(f"The character {voice}{note}: <d>[Chinese] {row['line']}</d>")
        if blocks:
            desc.append(" ".join(blocks))

    soundscape = seg.get("soundscape") or f"{scene_name or '场景'}的环境底噪。"
    return (h3_alignment_line(cuts) + "\n整体视听描述：\n" + "\n".join(desc)
            + DIALOGUE_TAIL.format(soundscape))


def _flow_of(script_ep: Dict, scene_index: int) -> tuple:
    scenes = script_ep.get("scenes") or []
    if 0 < int(scene_index) <= len(scenes):
        scene = scenes[int(scene_index) - 1]
        return (scene.get("flow") or scene.get("beats") or []), scene
    return [], {}


def fit_dialogue_seconds(data: Dict, script_ep: Dict) -> int:
    """把「一句台词装不进镜头秒数」的镜头拉长到台词自然时长。

    skill 的 dialogue-fit 用 4.5 字/秒换算，LLM 切镜时经常按 5s 一刀切，
    27 字的台词（6s）就装不下。台词逐字优先于节奏，所以这里按台词反推秒数，
    并把 params.maxCutSeconds 同步到真实最大值（否则 cut-length 会反过来报错）。
    """
    changed = 0
    max_cut = 0.0
    for ep_doc in data.get("episodes") or []:
        for seg in ep_doc.get("segments") or []:
            flow, _scene = _flow_of(script_ep, int(seg.get("sceneIndex") or 1))
            for cut in seg.get("cuts") or []:
                first, last = _beat_range(cut)
                need = 0.0
                for n in range(first, last + 1):
                    if 0 < n <= len(flow) and flow[n - 1].get("line"):
                        need += beat_seconds(flow[n - 1])
                if need > float(cut.get("seconds") or 0) + 0.01:
                    cut["seconds"] = round(min(8.0, need), 1)
                    changed += 1
                max_cut = max(max_cut, float(cut.get("seconds") or 0))
    params = data.setdefault("params", {})
    if max_cut > float(params.get("maxCutSeconds") or 0):
        params["maxCutSeconds"] = round(max_cut, 1)
    return changed


def prune_foreign_props(data: Dict, script_ep: Dict) -> int:
    """镜头引用本场不存在的道具时直接摘掉——道具对账以剧本为准。"""
    changed = 0
    for ep_doc in data.get("episodes") or []:
        for seg in ep_doc.get("segments") or []:
            _flow, scene = _flow_of(script_ep, int(seg.get("sceneIndex") or 1))
            allowed = {str(p) for p in (scene.get("props") or [])}
            if not allowed:
                continue
            for cut in seg.get("cuts") or []:
                props = cut.get("props") or []
                keep = [p for p in props if str(p) in allowed]
                if len(keep) != len(props):
                    cut["props"] = keep
                    changed += 1
    return changed


def split_oversized(data: Dict, max_seconds: float, ep_no: int) -> int:
    """按镜头边界把超过单次生成上限的段拆开，并重编段号（E01-01…连号）。

    不拆的话，出片会把 12s 吸附成 10s 并丢掉尾台词（静默截断）。
    返回新增的段数（拆一段 +1）。
    """
    cap = max(5.0, float(max_seconds))
    added = 0
    for ep_doc in data.get("episodes") or []:
        if int(ep_doc.get("ep") or 0) != int(ep_no):
            continue
        groups: List[Dict] = []
        for seg in ep_doc.get("segments") or []:
            bucket: List[Dict] = []
            total = 0.0
            for cut in seg.get("cuts") or []:
                sec = float(cut.get("seconds") or 0)
                if bucket and total + sec > cap:
                    groups.append({**seg, "cuts": bucket})
                    added += 1
                    bucket, total = [], 0.0
                bucket.append(cut)
                total += sec
            if bucket:
                groups.append({**seg, "cuts": bucket})
        for i, seg in enumerate(groups, start=1):
            seg["id"] = f"E{int(ep_no):02d}-{i:02d}"
            seg.pop("h3Prompt", None)   # 切点变了，提示词必须重算
        ep_doc["segments"] = groups
    return added


def ensure_segment_prompts(data: Dict, script_ep: Dict, outline: Dict) -> int:
    """逐段补齐/校正 h3Prompt，返回改动段数。

    LLM 直出的分镜常常不带 h3Prompt（或只写半截），而「出视频」直接把它交给
    视频模型。这里用 `rebuild_h3_prompt` 做确定性补全，保证：首行对齐指令、
    [镜头 k] 切点时刻、台词 <d> 块、整体音景 / 非叙事配乐四个骨架都在。
    """
    fixed = 0
    for ep_doc in data.get("episodes") or []:
        for seg in ep_doc.get("segments") or []:
            h3 = str(seg.get("h3Prompt") or "")
            complete = ("[镜头 " in h3 and "整体视听描述：" in h3
                        and "整体音景：" in h3 and "非叙事配乐：" in h3)
            if complete:
                continue
            prompt = rebuild_h3_prompt(seg, script_ep, outline)
            if prompt and prompt != h3:
                seg["h3Prompt"] = prompt
                fixed += 1
    return fixed


def clamp_target_seconds(data: Dict, script_parse: Dict, ep_no: int) -> Optional[float]:
    """把剧本的 `targetSeconds` 拉回台词量算出来的真实时长。

    LLM 会把「单集 1–2 分钟」直接写成 120s，而本集台词折算只有 38s，
    质量门的「总时长 ±15%」因此必挂。目标值应当由定稿剧本决定，不是由
    项目设置的上限决定。偏差 ≤ 25% 时保留原值（尊重模型的分场节奏）。
    返回最终采用的值；无法估算时返回 None。
    """
    ep = None
    for item in data.get("episodes") or []:
        if int(item.get("ep") or 0) == int(ep_no):
            ep = item
            break
    if ep is None:
        return None
    src = None
    for item in script_parse.get("episodes") or []:
        if int(item.get("ep") or 0) == int(ep_no):
            src = item
            break
    flow = []
    if src:
        for scene in src.get("scenes") or []:
            flow.extend(scene.get("flow") or scene.get("beats") or [])
    if not flow:
        for scene in ep.get("scenes") or []:
            flow.extend(scene.get("flow") or [])
    if not flow:
        return None
    est = round(sum(beat_seconds(b) for b in flow), 1)
    if est <= 0:
        return None
    old = ep.get("targetSeconds")
    try:
        oldf = float(old)
    except (TypeError, ValueError):
        oldf = 0.0
    if oldf > 0 and abs(oldf - est) / est <= 0.25:
        return oldf
    ep["targetSeconds"] = est
    return est


# ------------------------------------------------------------------ 对外入口
async def generate(kind: str, *, user_text: str, fallback, gates_hint: str = "") -> Tuple[Dict, str]:
    """生成一份文档：先试 LLM，失败/关闭时用 fallback()。

    返回 (data, source)，source ∈ {llm, skeleton}。
    """
    if not settings.hibtv_llm_enabled:
        return fallback(), "skeleton"
    text = user_text
    if gates_hint:
        text += f"\n\n【上次质量门未通过的项，请逐条修正】\n{gates_hint}"
    try:
        data = await chat_json(kind, text)
        if not isinstance(data, dict) or not data:
            raise HibtvLLMError("LLM 产出为空对象")
        return data, "llm"
    except HibtvLLMError as exc:
        logger.warning("短剧 %s 走 LLM 失败，回退确定性骨架：%s", kind, exc)
        return fallback(), "skeleton"


def outline_prompt(project, script: Dict, art: Dict) -> str:
    return (
        "【项目】\n"
        f"- 名称：{project.name}\n- 题材：{project.genre}\n- 总集数：{project.episodes}\n"
        f"- 单集时长：{project.minutes_min}–{project.minutes_max} 分钟\n"
        f"- 画面比例：{project.aspect_ratio}\n"
        f"- 必须保留：{project.keeps or '（无）'}\n\n"
        "【剧本解析结果（唯一事实来源，台词逐字）】\n"
        f"{json.dumps(script, ensure_ascii=False)[:24000]}\n\n"
        "【美术设定解析结果】\n"
        f"{json.dumps(art, ensure_ascii=False)[:16000]}\n\n"
        "【任务】按 novel-outline 的 schema 产出 outline.json。adaptMode 固定为「忠实」"
        "（输入是定稿剧本，不做改编），cut/merge/risks 留空数组；"
        "characters / scenes / props 必须覆盖上面解析出的全部条目（id 用 C01 / S01 / P01 编号）；"
        "episodes 数量等于总集数，synopsis / hook / suspense 三栏都是叙述体、不带引号对白。"
    )


def cast_prompt(project, art: Dict, outline: Dict) -> str:
    return (
        f"【项目】{project.name} / {project.genre} / {project.episodes} 集\n\n"
        "【美术设定解析结果】\n"
        f"{json.dumps(art.get('cast') or [], ensure_ascii=False)[:20000]}\n\n"
        "【大纲里的角色表】\n"
        f"{json.dumps(outline.get('characters') or [], ensure_ascii=False)[:8000]}\n\n"
        "【任务】按 novel-characters 的 schema 产出 cast.json。每个角色补齐 persona / image / voice；"
        "image.prompt 是喂 qwen-image 的**中文**全身像提示词，必须包含可辨识的外貌锚点；"
        "美术设定里已有的原文案原样保留，缺项留空字符串，不要臆造。"
    )


def art_prompt(project, art: Dict, outline: Dict) -> str:
    return (
        f"【项目】{project.name} / 画面比例 {project.aspect_ratio}\n\n"
        "【美术设定解析结果】\n"
        f"{json.dumps({'scenes': art.get('scene') or [], 'props': art.get('prop') or []}, ensure_ascii=False)[:22000]}\n\n"
        "【大纲里的场景 / 道具表】\n"
        f"{json.dumps({'scenes': outline.get('scenes') or [], 'props': outline.get('props') or []}, ensure_ascii=False)[:8000]}\n\n"
        "【任务】按 novel-art 的 schema 产出 art.json。scenes[].image.prompt 与 props[].image.prompt "
        "是喂 qwen-image 的**中文**空镜提示词：场景必须“空无一人”，道具必须“单独置于纯色背景正中、无人无手”。"
    )


def script_prompt(project, ep: Dict, outline: Dict) -> str:
    return (
        f"【项目】{project.name} / 第 {ep['ep']} 集\n\n"
        "【本集剧本解析结果（台词逐字）】\n"
        f"{json.dumps(ep, ensure_ascii=False)[:26000]}\n\n"
        "【大纲（角色 / 场景 / 道具 id）】\n"
        f"{json.dumps({k: outline.get(k) for k in ('characters', 'scenes', 'props')}, ensure_ascii=False)[:10000]}\n\n"
        "【任务】按 novel-script 的 schema 产出这一集的 script.json。硬约束："
        "flow 里动作节拍不出现引号台词、台词节拍逐字照抄解析结果（可只按标点切句）；"
        f"单句 ≤ {MAX_LINE_CHARS} 字；targetSeconds 按 charsPerSecond={CHARS_PER_SECOND} 折算后填；"
        "speaker 必须是本场 characters 里的 C 编号或 VO。"
    )


def storyboard_prompt(project, script_doc: Dict, outline: Dict) -> str:
    return (
        f"【项目】{project.name} / 画幅 {project.aspect_ratio} / promptLang=zh\n\n"
        "【本集剧本 script.json】\n"
        f"{json.dumps(script_doc, ensure_ascii=False)[:26000]}\n\n"
        "【大纲角色 / 场景】\n"
        f"{json.dumps({'characters': outline.get('characters'), 'scenes': outline.get('scenes')}, ensure_ascii=False)[:8000]}\n\n"
        "【任务】按 novel-storyboard 的 schema 产出这一集的 storyboard.json。硬约束："
        f"段总时长 ≤ {settings.hibtv_seg_max_seconds} 秒且不跨场次；分镜 2–5 秒；"
        "每个节拍被且仅被一个分镜认领、按顺序连续；"
        "h3Prompt 首行对齐指令与切点时刻必须由分镜秒数逐字推导；台词逐字进 <d>[Chinese] …</d>；"
        "H3 正文不出现角色名（用通用身份）；blocking 必填；frame 是中文分镜图提示词且含景别中文词。"
    )
