"""短剧工作台：确定性解析器（不调模型）。

输入是**已经定稿的剧本**与**美术设定**，本模块只做归类，不做改编：
- `parse_script`：识别「第 N 集 / 场号 / 角色：台词 / 钩子 / 断点」，台词逐字保留；
- `parse_art`：识别角色 / 场景 / 道具 / 特效条目与编号，原文案原样带出。

产物是 skill schema（novel-outline / novel-script / novel-storyboard）的底稿，
供 `hibtv_llm` 在无 LLM 时直接成文，或在有 LLM 时作为唯一事实来源。
"""
import re
from typing import Dict, List, Optional

_CJK_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9}

_EP_RE = re.compile(r"^[\s#>*\-—]*第\s*([0-9０-９零一二三四五六七八九十百千两]+)\s*集[\s·:：、\-—]*(.*)$")
_SCENE_RE = re.compile(
    r"^[\s#>*\-—【\[]*(?:第\s*)?([0-9]{1,3})\s*[-－—.．、]\s*([0-9]{1,3})\s*[场]?[\s】\]]*(.*)$")
_SCENE_ALT_RE = re.compile(r"^[\s#>*\-—【\[]*第?\s*([0-9]{1,3})\s*场[\s:：、】\]]*(.*)$")
_FIELD_RE = re.compile(r"^[\s#>*\-—]*[【\[]?(钩子|悬念|断点|卡点|梗概|summary|hook)[】\]]?\s*[：:]\s*(.+)$")
_CAST_LINE_RE = re.compile(r"^[\s#>*\-—]*[【\[]?(人物|角色|出场人物)[】\]]?\s*[：:]\s*(.+)$")
_PROP_LINE_RE = re.compile(r"^[\s#>*\-—]*[【\[]?(道具|戏用物)[】\]]?\s*[：:]\s*(.+)$")
_DIALOGUE_RE = re.compile(r"^[\s*]*([^\s：:，。！？、“”‘’（）()\[\]【】\d]{1,12})\s*[：:]\s*(.+)$")
_SCENE_DIR_RE = re.compile(r"^\s*[（(【\[]\s*(.*?)\s*[）)】\]]\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s*(.+?)\s*$")
_ART_ENTRY_RE = re.compile(
    r"^[\s#>*\-—]*(?:[（(]?\s*([CSPcs p])\s*[)）]?\s*)?([CSP])\s*([0-9]{1,2})\s*[.、:：\s]*(.*)$")
_BOLD_LEAD_RE = re.compile(r"^[\s*\->]*\*\*(.+?)\*\*\s*[：:]?\s*(.*)$")
_KV_RE = re.compile(r"^[\s*\->•]*([\u4e00-\u9fa5A-Za-z]{2,8})\s*[：:]\s*(.+)$")

# 「谁：台词」里明显不是说话人的前缀（章节/说明类标签）
_NOT_SPEAKER = {
    "场景", "时间", "地点", "人物", "角色", "道具", "服装", "化妆", "特效", "备注", "说明",
    "内景", "外景", "场景说明", "画外音", "旁白", "字幕", "镜头", "景别", "运镜", "提示",
    "注意", "要求", "风格", "主题", "核心", "钩子", "悬念", "断点", "卡点", "梗概", "一句话",
}

_SECTION_KIND = (
    (("角色", "人物", "演员", "cast"), "cast"),
    (("场景", "环境", "地点", "scene"), "scene"),
    (("道具", "物件", "prop"), "prop"),
    (("特效", "妆造", "造型", "氛围", "effect"), "effect"),
)


def _cjk_to_int(raw: str) -> Optional[int]:
    """把「1」「10」「十一」这类集号转成 int；失败返回 None。"""
    s = (raw or "").strip().translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if "十" in s:
        head, _, tail = s.partition("十")
        tens = _CJK_NUM.get(head, 1) if head else 1
        ones = _CJK_NUM.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return _CJK_NUM.get(s)


def _new_scene(index: int, no: str, title: str) -> Dict:
    return {
        "index": index, "no": no, "title": title.strip(),
        "location": title.strip(), "dayNight": "", "inOut": "",
        "characters": [], "props": [], "beats": [], "lighting": "",
    }


def _split_names(raw: str) -> List[str]:
    parts = re.split(r"[、,，/／\s]+", raw or "")
    return [p.strip() for p in parts if p.strip() and len(p.strip()) <= 12][:12]


def _detect_scene_meta(title: str) -> Dict:
    """从场标题里抽 日/夜、内/外 与地点名。"""
    day = ""
    for k in ("清晨", "日", "白天", "夜", "夜晚", "黄昏", "傍晚", "凌晨"):
        if k in title:
            day = k
            break
    inout = "内" if re.search(r"[（(\[]?\s*内\s*[）)\]]?", title) and "内景" in title else (
        "外" if "外景" in title else "")
    loc = re.sub(r"[【】\[\]（）()]", " ", title)
    loc = re.sub(r"\b(内景|外景|日|夜|清晨|黄昏|傍晚|凌晨)\b", " ", loc)
    loc = re.sub(r"\s+", " ", loc).strip() or title.strip()
    return {"dayNight": day, "inOut": inout, "location": loc}


def parse_script(text: str) -> Dict:
    """解析定稿剧本 → 集 / 场 / 拍（动作 + 台词，台词逐字）。"""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    episodes: List[Dict] = []
    cur_ep: Optional[Dict] = None
    cur_scene: Optional[Dict] = None
    beat_no = 0

    def ensure_ep(no: int, title: str = "") -> Dict:
        nonlocal cur_ep
        for ep in episodes:
            if ep["ep"] == no:
                cur_ep = ep
                return ep
        ep = {"ep": no, "title": title.strip(), "scenes": [],
              "hook": "", "suspense": "", "cliff": "", "summary": ""}
        episodes.append(ep)
        cur_ep = ep
        return ep

    def ensure_scene(title: str, no: str = "") -> Dict:
        nonlocal cur_scene
        if cur_ep is None:
            ensure_ep(1)
        assert cur_ep is not None
        meta = _detect_scene_meta(title)
        scene = _new_scene(len(cur_ep["scenes"]) + 1, no or f'{cur_ep["ep"]}-{len(cur_ep["scenes"]) + 1}', title)
        scene.update(meta)
        cur_ep["scenes"].append(scene)
        cur_scene = scene
        return scene

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        m = _EP_RE.match(stripped)
        if m:
            no = _cjk_to_int(m.group(1)) or (len(episodes) + 1)
            ensure_ep(no, m.group(2))
            cur_scene = None
            beat_no = 0
            continue

        m = _SCENE_RE.match(stripped) or _SCENE_ALT_RE.match(stripped)
        if m:
            if cur_ep is None:
                ensure_ep(1)
            if _SCENE_RE.match(stripped):
                no = f"{m.group(1)}-{m.group(2)}"
                title = m.group(3)
            else:
                no = f'{cur_ep["ep"]}-{m.group(1)}'
                title = m.group(2)
            ensure_scene(title, no)
            beat_no = 0
            continue

        m = _FIELD_RE.match(stripped)
        if m and cur_scene is None:
            if cur_ep is None:
                ensure_ep(1)
            key = m.group(1).lower()
            val = m.group(2).strip()
            if key in ("钩子", "hook"):
                cur_ep["hook"] = val
            elif key in ("悬念",):
                cur_ep["suspense"] = val
            elif key in ("断点", "卡点"):
                cur_ep["cliff"] = val
            elif key in ("梗概", "summary"):
                cur_ep["summary"] = val
            continue

        m = _CAST_LINE_RE.match(stripped)
        if m and cur_scene is not None:
            for name in _split_names(m.group(2)):
                if name not in cur_scene["characters"]:
                    cur_scene["characters"].append(name)
            continue

        m = _PROP_LINE_RE.match(stripped)
        if m and cur_scene is not None:
            for name in _split_names(m.group(2)):
                if name not in cur_scene["props"]:
                    cur_scene["props"].append(name)
            continue

        if cur_scene is None:
            # 集标题之前的说明文字（logline / 题材）先归到第 1 集的 summary
            if cur_ep is not None and not cur_ep["summary"] and len(stripped) > 6:
                cur_ep["summary"] = stripped
            continue

        m = _DIALOGUE_RE.match(stripped)
        if m and m.group(1) not in _NOT_SPEAKER and m.group(2).strip():
            who = m.group(1).strip()
            beat_no += 1
            cur_scene["beats"].append({
                "n": beat_no, "kind": "line", "who": who,
                "line": m.group(2).strip(), "note": "",
            })
            if who not in cur_scene["characters"]:
                cur_scene["characters"].append(who)
            continue

        m = _SCENE_DIR_RE.match(stripped)
        if m:
            beat_no += 1
            cur_scene["beats"].append({
                "n": beat_no, "kind": "action", "who": "动作",
                "line": m.group(1).strip(), "note": "导演提示",
            })
            continue

        if stripped.startswith("#"):
            continue
        beat_no += 1
        cur_scene["beats"].append({
            "n": beat_no, "kind": "action", "who": "动作", "line": stripped, "note": "",
        })

    # 空集补齐（剧本里只写了集号、没有场次）
    for ep in episodes:
        if not ep["scenes"]:
            ensure_ep(ep["ep"])
            cur_ep = ep
            ensure_scene(f'第 {ep["ep"]} 集', f'{ep["ep"]}-1')

    episodes.sort(key=lambda e: e["ep"])
    stats = _script_stats(episodes)
    return {"episodes": episodes, "stats": stats}


def _script_stats(episodes: List[Dict]) -> Dict:
    lines = 0
    beats = 0
    cast: Dict[str, int] = {}
    locs: Dict[str, int] = {}
    for ep in episodes:
        for sc in ep["scenes"]:
            beats += len(sc["beats"])
            locs[sc["location"] or sc["title"]] = locs.get(sc["location"] or sc["title"], 0) + 1
            for b in sc["beats"]:
                if b["kind"] == "line":
                    lines += 1
                    cast[b["who"]] = cast.get(b["who"], 0) + 1
    return {
        "episodes": len(episodes),
        "scenes": sum(len(e["scenes"]) for e in episodes),
        "beats": beats,
        "lines": lines,
        "cast": sorted(cast.items(), key=lambda kv: -kv[1]),
        "locations": sorted(locs.items(), key=lambda kv: -kv[1]),
    }


def _section_of(title: str) -> Optional[str]:
    low = title
    for keys, kind in _SECTION_KIND:
        for k in keys:
            if k in low:
                return kind
    return None


def parse_art(text: str) -> Dict:
    """解析美术设定 → 角色 / 场景 / 道具 / 特效条目（编号 + 原文案）。"""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    buckets: Dict[str, List[Dict]] = {"cast": [], "scene": [], "prop": [], "effect": []}
    section: Optional[str] = None
    cur: Optional[Dict] = None

    def close():
        nonlocal cur
        if cur is not None and section in buckets:
            buckets[section].append(cur)
        cur = None

    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        m = _HEADING_RE.match(stripped)
        if m:
            title = m.group(2).strip()
            kind = _section_of(title)
            if kind and len(title) <= 20:
                close()
                section = kind
                continue
            if section:
                close()
                cur = {"name": re.sub(r"^[（(【\[]?\s*[CSP]\s*\d{1,2}\s*[)）】\]]?\s*", "", title),
                       "code": "", "raw": [], "fields": {}}
                code = re.search(r"\b([CSP])(\d{1,2})\b", title)
                if code:
                    cur["code"] = f"{code.group(1)}{int(code.group(2)):02d}"
                continue
            continue

        if section is None:
            continue

        m = _ART_ENTRY_RE.match(stripped)
        if m and m.group(3) and len(stripped) <= 40 and not stripped.startswith(("-", "*")):
            close()
            code = f"{m.group(2).upper()}{int(m.group(3)):02d}"
            cur = {"name": m.group(4).strip(), "code": code, "raw": [], "fields": {}}
            continue

        m = _BOLD_LEAD_RE.match(stripped)
        if m and len(m.group(1)) <= 24:
            name = m.group(1).strip()
            code = re.search(r"\b([CSP])(\d{1,2})\b", name)
            if cur is None or (code and code.group(1).upper() == {"cast": "C", "scene": "S", "prop": "P"}.get(section, "?")):
                close()
                cur = {"name": re.sub(r"^[（(【\[]?\s*[CSP]\s*\d{1,2}\s*[)）】\]]?\s*", "", name),
                       "code": (f"{code.group(1).upper()}{int(code.group(2)):02d}" if code else ""),
                       "raw": [], "fields": {}}
                if m.group(2).strip():
                    cur["raw"].append(m.group(2).strip())
                continue

        if cur is None:
            continue
        kv = _KV_RE.match(stripped)
        if kv and len(kv.group(1)) <= 8:
            cur["fields"].setdefault(kv.group(1), kv.group(2).strip())
        cur["raw"].append(stripped.lstrip("*-• "))

    close()

    out: Dict[str, List[Dict]] = {}
    prefix = {"cast": "C", "scene": "S", "prop": "P", "effect": "F"}
    for kind, items in buckets.items():
        rows: List[Dict] = []
        for i, item in enumerate(items):
            code = item.get("code") or f"{prefix[kind]}{i + 1:02d}"
            rows.append({
                "code": code,
                "name": (item.get("name") or "").strip() or f"{kind}{i + 1}",
                "fields": item.get("fields") or {},
                "text": "\n".join(item.get("raw") or []).strip(),
            })
        out[kind] = rows
    out["stats"] = {k: len(v) for k, v in out.items() if k != "stats"}
    return out


def detect_summary(script: Dict, art: Dict) -> Dict:
    """新建项目弹窗里的「已自动识别」汇总。"""
    s = script.get("stats") or {}
    a = art.get("stats") or {}
    return {
        "episodes": s.get("episodes", 0),
        "scenes": s.get("scenes", 0),
        "lines": s.get("lines", 0),
        "beats": s.get("beats", 0),
        "cast": a.get("cast", 0),
        "scene": a.get("scene", 0),
        "prop": a.get("prop", 0),
        "effect": a.get("effect", 0),
    }
