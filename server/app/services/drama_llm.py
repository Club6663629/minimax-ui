"""短剧 LLM：剧本节拍 / 角色场景抽取 / 分镜规划 + 严格 JSON 校验 + 本地模板回退。

LLM 失败（无 key、超时、非法 JSON、字段缺失）一律降级为本地模板并返回 source=local，
不向前端抛 5xx：剧本阶段最怕“点了没反应”，降级也能量出完整链路。
"""
import json
import logging
import re
from typing import Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_ROLE_ORDER = {"main": 0, "support": 1, "extra": 2}
_SHOT_SIZES = ["全景", "中景", "近景", "特写"]
_CAMERAS = ["平视", "稍俯", "稍仰", "侧逆光"]
_MOVEMENTS = ["固定", "缓推", "缓拉", "横移"]
_LOCATION_FALLBACK = ["主场景"]


# ---------------------------------------------------------------- 通用
def llm_available() -> bool:
    provider = str(getattr(settings, "drama_llm_provider", "qwen") or "qwen").strip().lower()
    return provider not in ("off", "local", "none") and bool(settings.qwen_api_key)


def _extract_json(text: str) -> dict:
    """从模型输出中提取第一个 JSON 对象（容忍 ```json 围栏与前后说明）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM 输出不含 JSON 对象")
    return json.loads(raw[start:end + 1])


def _chat_json(system: str, user: str) -> dict:
    base = str(settings.qwen_api_base or "").rstrip("/")
    url = f"{base}/chat/completions"
    payload = {
        "model": settings.qwen_llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {settings.qwen_api_key}"}
    with httpx.Client(timeout=settings.drama_llm_timeout) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return _extract_json(content)


def _safe_str(value, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _clamp_seconds(value, low: float, high: float, default: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    return round(min(high, max(low, num)), 2)


# ---------------------------------------------------------------- 剧本
_BEAT_TITLES = ["开端", "暗涌", "转折", "对峙", "高潮", "余波", "抉择", "收束", "尾声", "伏笔", "回响", "终局"]
_BEAT_MOODS = ["压抑", "紧张", "动摇", "决绝", "爆发", "余韵"]

# 本地模板的地点词表：命中梗概里的实景词，按出现顺序分给各拍
_LOCATION_WORDS = [
    ("便利店", "便利店"), ("旧楼", "旧楼"), ("天台", "天台"), ("出租屋", "出租屋"),
    ("公寓", "公寓"), ("办公室", "办公室"), ("公司", "公司"), ("医院", "医院"),
    ("学校", "学校"), ("教室", "教室"), ("地铁", "地铁站"), ("车站", "车站"),
    ("咖啡馆", "咖啡馆"), ("餐厅", "餐厅"), ("电梯", "电梯"), ("车库", "车库"),
    ("走廊", "走廊"), ("公园", "公园"), ("江边", "江边"), ("码头", "码头"),
    ("仓库", "仓库"), ("街道", "街道"), ("小巷", "小巷"), ("家门口", "家门口"),
    ("客厅", "客厅"), ("卧室", "卧室"), ("厨房", "厨房"), ("浴室", "浴室"),
    ("暴雨", "雨夜街道"), ("雨夜", "雨夜街道"), ("路口", "路口"),
]


def _pick_locations(story: str, count: int) -> list:
    """按地点词在梗概里的出现顺序分给各拍（不再拿剧情句当地点）。"""
    hits = []
    seen: set = set()
    for keyword, name in _LOCATION_WORDS:
        pos = story.find(keyword)
        if pos >= 0 and name not in seen:
            hits.append((pos, name))
            seen.add(name)
    hits.sort()
    found = [name for _, name in hits]
    if not found:
        found = list(_LOCATION_FALLBACK)
    return [found[i % len(found)] for i in range(count)]


def _fallback_script(brief: str, logline: str, style: str, beat_count: int, target_seconds: int) -> dict:
    story = (brief or logline or "").strip()
    fragments = [s.strip() for s in re.split(r"[。！？!?；;\n]+", story) if s.strip()]
    if not fragments:
        fragments = [story or "一个尚未展开的故事"]
    per = round(float(target_seconds) / beat_count, 2)
    locations = _pick_locations(story, beat_count)
    beats = []
    for i in range(beat_count):
        fragment = fragments[i % len(fragments)]
        beats.append({
            "id": f"b{i + 1}",
            "title": _BEAT_TITLES[i % len(_BEAT_TITLES)],
            "summary": fragment[:160],
            "mood": _BEAT_MOODS[i % len(_BEAT_MOODS)],
            "location": locations[i],
            "characters": ["主角"] if i % 3 != 1 else ["主角", "对手"],
            "target_seconds": per,
            "dialogue_hint": "" if i % 3 == 2 else ("你是谁？" if i == 0 else "别过来。"),
        })
    # 余数并入最后一拍，保证总时长与目标一致
    total = sum(b["target_seconds"] for b in beats)
    beats[-1]["target_seconds"] = round(beats[-1]["target_seconds"] + (target_seconds - total), 2)
    script = "\n\n".join(
        f"第 {i + 1} 场｜{b['location']} · {b['title']}\n{b['summary']}"
        for i, b in enumerate(beats)
    )
    return {"source": "local", "beats": beats, "script": script}


def generate_script(brief: str, logline: str, style: str, beat_count: int, target_seconds: int) -> dict:
    """生成节拍表与剧本正文；LLM 失败回退本地模板。"""
    if not llm_available():
        return _fallback_script(brief, logline, style, beat_count, target_seconds)
    system = (
        "你是短剧编剧。只输出严格 JSON，不要 markdown 围栏。"
        "beats 数组长度必须等于用户要求；每个 beat 字段齐全且 target_seconds 之和接近目标总时长。"
    )
    user = json.dumps({
        "任务": "把梗概拆成节拍并写剧本正文",
        "目标时长秒": target_seconds,
        "节拍数": beat_count,
        "风格": style,
        "梗概": brief or logline,
        "输出格式": {
            "beats": [{
                "id": "b1", "title": "四字标题", "summary": "本拍事件",
                "mood": "情绪", "location": "地点", "characters": ["角色名"],
                "target_seconds": 10, "dialogue_hint": "一句台词或空串",
            }],
            "script": "剧本正文",
        },
    }, ensure_ascii=False)
    try:
        data = _chat_json(system, user)
        beats = data.get("beats") or []
        if len(beats) != beat_count:
            raise ValueError(f"节拍数不符：期望 {beat_count}，实际 {len(beats)}")
        clean = []
        for i, beat in enumerate(beats):
            clean.append({
                "id": _safe_str(beat.get("id") or f"b{i + 1}", 16),
                "title": _safe_str(beat.get("title"), 60) or _BEAT_TITLES[i % len(_BEAT_TITLES)],
                "summary": _safe_str(beat.get("summary"), 400) or "情节推进",
                "mood": _safe_str(beat.get("mood"), 40),
                "location": _safe_str(beat.get("location"), 80) or _LOCATION_FALLBACK[0],
                "characters": [_safe_str(n, 60) for n in (beat.get("characters") or []) if _safe_str(n, 60)],
                "target_seconds": _clamp_seconds(beat.get("target_seconds"), 2, 60, target_seconds / beat_count),
                "dialogue_hint": _safe_str(beat.get("dialogue_hint"), 400),
            })
        for beat in clean:
            if not beat["characters"]:
                beat["characters"] = ["主角"]
        return {
            "source": "llm",
            "beats": clean,
            "script": _safe_str(data.get("script"), 8000) or "\n\n".join(b["summary"] for b in clean),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("短剧剧本 LLM 失败，回退本地模板: %s", exc)
        return _fallback_script(brief, logline, style, beat_count, target_seconds)


# ---------------------------------------------------------------- 角色 / 场景
def _fallback_cast(beats: list, project_style: str) -> dict:
    names: list = []
    locations: list = []
    for beat in beats:
        for name in beat.get("characters") or []:
            name = _safe_str(name, 60)
            if name and name not in names:
                names.append(name)
        loc = _safe_str(beat.get("location"), 80)
        if loc and loc not in locations:
            locations.append(loc)
    if not names:
        names = ["主角"]
    if not locations:
        locations = list(_LOCATION_FALLBACK)
    characters = []
    for i, name in enumerate(names):
        main = i == 0
        characters.append({
            "name": name,
            "role": "main" if main else "support",
            "gender": "男" if main else "",
            "age": "28" if main else "",
            "appearance": ("短发，清瘦，眉骨有旧伤" if main else "身形普通，面部特征清晰"),
            "personality": ("克制、执拗" if main else "谨慎、话少"),
            "costume": ("深色连帽外套，肩头常湿" if main else "日常便装"),
            "voice": ("低沉、语速偏慢" if main else ""),
        })
    scenes = []
    for i, loc in enumerate(locations):
        scenes.append({
            "scene_no": i + 1,
            "name": loc,
            "location": loc,
            "time_of_day": "夜" if ("夜" in loc or "雨" in loc) else "",
            "description": f"{loc}，{project_style[:40] or '写实环境光'}",
        })
    return {"source": "local", "characters": characters, "scenes": scenes}


def extract_cast(beats: list, project_style: str) -> dict:
    """从 beats 抽取角色与场景；LLM 失败回退本地模板。"""
    if not llm_available():
        return _fallback_cast(beats, project_style)
    system = (
        "你是短剧制片。只输出严格 JSON。角色用于全项目跨集复用，同名角色必须合并；"
        "场景按本集出现顺序编号。"
    )
    user = json.dumps({
        "任务": "从节拍表抽取角色与场景",
        "风格": project_style,
        "beats": beats,
        "输出格式": {
            "characters": [{
                "name": "姓名", "role": "main|support|extra", "gender": "", "age": "",
                "appearance": "外形（进提示词）", "personality": "性格",
                "costume": "服装", "voice": "声线",
            }],
            "scenes": [{
                "scene_no": 1, "name": "场景名", "location": "地点",
                "time_of_day": "夜/日/黄昏", "description": "环境描述",
            }],
        },
    }, ensure_ascii=False)
    try:
        data = _chat_json(system, user)
        chars = data.get("characters") or []
        scenes = data.get("scenes") or []
        if not chars or not scenes:
            raise ValueError("角色或场景为空")
        clean_chars = []
        seen = set()
        for item in chars:
            name = _safe_str(item.get("name"), 60)
            if not name or name in seen:
                continue
            seen.add(name)
            role = _safe_str(item.get("role"), 16).lower()
            clean_chars.append({
                "name": name,
                "role": role if role in _ROLE_ORDER else "support",
                "gender": _safe_str(item.get("gender"), 16),
                "age": _safe_str(item.get("age"), 16),
                "appearance": _safe_str(item.get("appearance"), 1000),
                "personality": _safe_str(item.get("personality"), 1000),
                "costume": _safe_str(item.get("costume"), 1000),
                "voice": _safe_str(item.get("voice"), 80),
            })
        clean_scenes = []
        for i, item in enumerate(scenes):
            loc = _safe_str(item.get("location") or item.get("name"), 80) or _LOCATION_FALLBACK[0]
            clean_scenes.append({
                "scene_no": int(item.get("scene_no") or i + 1),
                "name": _safe_str(item.get("name"), 80) or loc,
                "location": loc,
                "time_of_day": _safe_str(item.get("time_of_day"), 80),
                "description": _safe_str(item.get("description"), 1000),
            })
        if not clean_chars or not clean_scenes:
            raise ValueError("角色或场景清洗后为空")
        return {"source": "llm", "characters": clean_chars, "scenes": clean_scenes}
    except Exception as exc:  # noqa: BLE001
        logger.warning("短剧角色抽取 LLM 失败，回退本地模板: %s", exc)
        return _fallback_cast(beats, project_style)


# ---------------------------------------------------------------- 分镜
def _split_durations(target: float, max_shots: int = 3) -> list:
    """把一个 beat 的目标时长拆成 1–3 镜；每镜落在 2–10s。"""
    target = max(2.0, float(target or 2))
    count = 1
    while count < max_shots and target / count > 10.0:
        count += 1
    per = round(target / count, 2)
    if per < 2.0:
        count = max(1, int(target // 2))
        per = round(target / count, 2)
    durations = [per] * count
    durations[-1] = round(target - per * (count - 1), 2)
    # 收口到合法区间（浮点余数可能越界）
    for i, value in enumerate(durations):
        durations[i] = round(min(10.0, max(2.0, value)), 2)
    return durations


def _fallback_shots(beats: list, characters: list, scenes: list, max_shots: int) -> dict:
    char_by_name = {c["name"]: c["id"] for c in characters}
    scene_by_loc = {s.get("location") or s.get("name"): s["scene_no"] for s in scenes}
    default_scene = scenes[0]["scene_no"] if scenes else 1
    rows = []
    shot_no = 1
    for beat in beats:
        durations = _split_durations(beat.get("target_seconds") or 5)
        names = [n for n in (beat.get("characters") or []) if n in char_by_name]
        char_ids = [char_by_name[n] for n in names]
        scene_no = scene_by_loc.get(beat.get("location"), default_scene)
        for j, duration in enumerate(durations):
            if len(rows) >= max_shots:
                break
            size = _SHOT_SIZES[(shot_no - 1) % len(_SHOT_SIZES)]
            camera = _CAMERAS[(shot_no - 1) % len(_CAMERAS)]
            movement = _MOVEMENTS[(shot_no - 1) % len(_MOVEMENTS)]
            summary = _safe_str(beat.get("summary"), 200) or "情节推进"
            prompt = f"{size}，{camera}，{movement}。{summary}"
            if j == 0:
                prompt += "，人物进入画面并停留"
            else:
                prompt += "，延续上一镜的动作与光线"
            rows.append({
                "shot_no": shot_no,
                "scene_no": scene_no,
                "beat_id": _safe_str(beat.get("id"), 16),
                "prompt": prompt,
                "duration_s": duration,
                "shot_size": size,
                "camera": camera,
                "movement": movement,
                "character_ids": char_ids,
                "ref_upload_ids": [],
                "continuity_note": "保持同一场景光线与人物服装连续",
                "dialogue": _safe_str(beat.get("dialogue_hint"), 300) if j == 0 else "",
                "audio_note": _safe_str(beat.get("mood"), 60) + "环境声" if beat.get("mood") else "环境声",
            })
            shot_no += 1
        if len(rows) >= max_shots:
            break
    return {"source": "local", "shots": rows[:max_shots]}


def plan_shots(beats: list, characters: list, scenes: list, max_shots: int) -> dict:
    """按 beats 规划镜头表（不落库）；LLM 失败回退本地模板。"""
    if not llm_available():
        return _fallback_shots(beats, characters, scenes, max_shots)
    system = (
        "你是短剧分镜师。只输出严格 JSON。每个 beat 生成 1–3 镜，单镜 2–10 秒，"
        "总镜数不超过上限；镜号从 1 连续；scene_no 必须来自给定场景；"
        "character_ids 必须来自给定角色 id。"
    )
    user = json.dumps({
        "任务": "规划分镜表",
        "镜数上限": max_shots,
        "beats": beats,
        "characters": [{"id": c["id"], "name": c["name"], "role": c["role"]} for c in characters],
        "scenes": [{"scene_no": s["scene_no"], "name": s["name"], "location": s["location"]} for s in scenes],
        "输出格式": {"shots": [{
            "shot_no": 1, "scene_no": 1, "beat_id": "b1", "prompt": "画面与动作",
            "duration_s": 5, "shot_size": "中景", "camera": "平视", "movement": "固定",
            "character_ids": [1], "dialogue": "", "audio_note": "", "continuity_note": "",
        }]},
    }, ensure_ascii=False)
    try:
        data = _chat_json(system, user)
        shots = data.get("shots") or []
        if not shots:
            raise ValueError("分镜为空")
        valid_chars = {c["id"] for c in characters}
        valid_scenes = {s["scene_no"] for s in scenes}
        clean = []
        for i, shot in enumerate(shots[:max_shots]):
            scene_no = int(shot.get("scene_no") or 1)
            if scene_no not in valid_scenes:
                scene_no = min(valid_scenes) if valid_scenes else 1
            char_ids = [int(cid) for cid in (shot.get("character_ids") or []) if int(cid) in valid_chars]
            clean.append({
                "shot_no": i + 1,
                "scene_no": scene_no,
                "beat_id": _safe_str(shot.get("beat_id"), 16),
                "prompt": _safe_str(shot.get("prompt"), 2000),
                "duration_s": _clamp_seconds(
                    shot.get("duration_s"),
                    settings.drama_min_shot_seconds,
                    settings.drama_max_shot_seconds,
                    5.0,
                ),
                "shot_size": _safe_str(shot.get("shot_size"), 24),
                "camera": _safe_str(shot.get("camera"), 24),
                "movement": _safe_str(shot.get("movement"), 24),
                "character_ids": char_ids,
                "ref_upload_ids": [],
                "continuity_note": _safe_str(shot.get("continuity_note"), 500),
                "dialogue": _safe_str(shot.get("dialogue"), 500),
                "audio_note": _safe_str(shot.get("audio_note"), 500),
            })
        if not clean:
            raise ValueError("分镜清洗后为空")
        return {"source": "llm", "shots": clean}
    except Exception as exc:  # noqa: BLE001
        logger.warning("短剧分镜 LLM 失败，回退本地模板: %s", exc)
        return _fallback_shots(beats, characters, scenes, max_shots)
