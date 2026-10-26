"""导入器：把 Markdown 剧本 / 美术设定解析成 shuohao 的六份 JSON。

M0 只做「结构解析 + 模板补全」：
- 中文层（剧本、人物、场景、道具、节拍）逐字来自原文；
- 英文出图提示词先用模板 + 手工特征表，保证字段齐全、门能过；
- 分镜（H3 提示词）只出骨架，M1 再接 LLM 精修。

解析器不依赖 LLM，可重复跑；同一份原文永远得到同一份 JSON。
"""
import re
from typing import Optional

# 《焚霜引》主要角色的英文特征：M0 内置，避免模板雷同；其它剧目走中文兜底。
CHARACTER_EN = {
    "晏沉": "lean wiry young man, dark messy long hair tied with rough cloth, three claw scars on left ribs, torn hemp slave clothes, bare feet, hard silent gaze",
    "谢无咎": "tall imposing man, long frost-silver hair, heterochromatic eyes (left ice-blue, right dark red), black and gold palace robe, black demonic markings on left shoulder and nape",
    "苏挽月": "composed woman in her thirties, black hair in a high bun, crystal monocle over left eye, grey-green physician robe, fixed faint smile",
    "花九娘": "elegant dangerous woman in her thirties, trailing red dress, small vermilion dot at the outer eye corner, soft whip at her waist",
    "顾铁心": "veteran swordsman in his late forties, short white hair and beard, worn old armour of a retired sword slave, unfanged iron sword",
    "仇七杀": "bald scarred man, black robe, silver chain with a bone pendant at his neck, rolling two iron balls in one hand",
    "凌九霄": "white-haired old man with a youthful face, white robe with gold patterns, horsetail whisk, permanent smile that never reaches the eyes",
    "钟叔": "hunched old steward, disabled right hand, plain cloth robe, ledger book under his arm",
    "小石头": "teenage boy, stubborn eyes, patched cloth clothes, later washed-white old sword robe",
}

NEGATIVE_CHAR = (
    "modern clothing, zipper, sneakers, plastic texture, glossy skin, extra fingers, "
    "deformed hands, asymmetric eyes, blurry face, watermark, text, logo, multiple people, "
    "different person, inconsistent costume"
)

VOICE_EN = {
    "晏沉": "young male, low husky baritone, restrained, slow pace, northern accent, default emotion: enduring pain",
    "谢无咎": "male, low cold bass, clipped delivery, breathy edge, default emotion: restrained violence",
    "苏挽月": "female, mid-low clear voice, even pace, clinical calm, default emotion: dry amusement",
    "花九娘": "female, warm mid voice, lilting pace, smile in the tone, default emotion: veiled threat",
    "顾铁心": "male, rough gravelly baritone, slow, default emotion: weary warmth",
    "仇七杀": "male, rasping low voice, uneven pace, default emotion: cold greed",
    "凌九霄": "male, smooth aged baritone, measured pace, default emotion: false benevolence",
    "钟叔": "male, thin aged voice, shaky pace, default emotion: loyal concern",
    "小石头": "male teenager, bright unbroken voice, quick pace, default emotion: stubborn hope",
}

TITLE_RE = re.compile(r"^\*\*第(\d+)集[（(](.*?)[）)]\*\*\s*$")
_NOT_NAMES = {
    "年龄气质", "发型", "妆面标记三件套", "妆面标记", "身体语言", "武器", "气质", "功能", "成长线",
}
SCENE_RE = re.compile(r"^\*\*(\d+)-(\d+)\s+(.+?)\*\*\s*$")
SCENE_META_RE = re.compile(r"^(?:(夜|日|晨|昏|黄昏|清晨|傍晚)\s+(内|外)\s+)?(.+)$")
PEOPLE_RE = re.compile(r"^人物[：:]\s*(.+)$")
ACTION_RE = re.compile(r"^△\s*(.+)$")
LINE_RE = re.compile(r"^([^：:△*\s][^：:]{0,24}?)(?:（([^）]*)）)?[：:]\s*(.+)$")
ART_SCENE_RE = re.compile(r"^\*\*(S\d{2})\s+(.+?)\*\*\s*$")
ART_PROP_RE = re.compile(r"^\*\*【(.+?)】\*\*\s*$")
ART_CHAR_RE = re.compile(r"^###\s+2\.\d+\s+(.+?)（")
SHEET_ROW_RE = re.compile(r"^(\d+)｜(.+)$")
DUR_RE = re.compile(r"时长[：:]\s*(?:(\d+)\s*分)?\s*(\d+)\s*秒")
HOOK_RE = re.compile(r"钩子[：:]\s*(.+?)(?:｜|$)")


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", text or "").strip()


def _split_names(raw: str) -> list:
    names = []
    for part in re.split(r"[、,，/]", raw or ""):
        name = re.sub(r"（[^）]*）|\([^)]*\)", "", part).strip()
        if name and name not in names:
            names.append(name)
    return names


def _estimate_seconds(text: str, chars_per_second: float = 4.5) -> float:
    return round(max(1.0, len(_clean(text)) / chars_per_second), 2)


# ---------------------------------------------------------------- 剧本

def parse_script(md: str) -> dict:
    """返回 {source, episodes:[{ep,targetSeconds,hook,cliff,scenes:[...]}], people:[...]}。"""
    lines = md.splitlines()
    episodes = []
    people_order = []
    cur_ep = None
    cur_scene = None
    in_script = False

    for raw in lines:
        line = raw.rstrip()
        m = TITLE_RE.match(line)
        if m:
            in_script = True
            ep_no = int(m.group(1))
            meta = m.group(2)
            dur = DUR_RE.search(meta)
            seconds = 0
            if dur:
                seconds = int(dur.group(1) or 0) * 60 + int(dur.group(2) or 0)
            hook = HOOK_RE.search(meta)
            cur_ep = {
                "ep": ep_no,
                "targetSeconds": seconds or 100,
                "hook": (hook.group(1).strip() if hook else ""),
                "cliff": "",
                "beatsClaimed": [],
                "hookBeat": [1, 1],
                "scenes": [],
            }
            episodes.append(cur_ep)
            cur_scene = None
            continue

        if not in_script or cur_ep is None:
            continue

        m = SCENE_RE.match(line)
        if m:
            meta = SCENE_META_RE.match(m.group(3).strip())
            place = (meta.group(3) if meta else m.group(3)).strip()
            cur_scene = {
                "ep": int(m.group(1)),
                "no": int(m.group(2)),
                "time": (meta.group(1) if meta and meta.group(1) else "夜"),
                "inout": (meta.group(2) if meta and meta.group(2) else "内"),
                "place": place,
                "characters": [],
                "props": [],
                "flow": [],
            }
            cur_ep["scenes"].append(cur_scene)
            continue

        if cur_scene is None:
            continue

        m = PEOPLE_RE.match(line)
        if m:
            for name in _split_names(m.group(1)):
                if name not in cur_scene["characters"]:
                    cur_scene["characters"].append(name)
                if name not in people_order:
                    people_order.append(name)
            continue

        m = ACTION_RE.match(line)
        if m:
            action = m.group(1).strip()
            if action:
                cur_scene["flow"].append({"action": action})
            continue

        m = LINE_RE.match(line)
        if m:
            speaker = m.group(1).strip()
            hint = (m.group(2) or "").strip()
            text = m.group(3).strip()
            if not text:
                continue
            # 画外 / 心声 / 非固定角色：一律 VO，把原名写进 delivery
            if ("画外" in hint) or ("OS" in hint.upper()) or ("心声" in hint):
                cur_scene["flow"].append({
                    "speaker": "VO",
                    "line": text,
                    "delivery": f"{speaker}，{hint}".strip("，"),
                })
            else:
                if speaker not in cur_scene["characters"]:
                    cur_scene["characters"].append(speaker)
                if speaker not in people_order:
                    people_order.append(speaker)
                cur_scene["flow"].append({
                    "speaker": speaker,
                    "line": text,
                    "delivery": hint,
                })
            continue

    for ep in episodes:
        # cliff：最后一拍的动作/台词当说明，保证非空
        if ep["scenes"]:
            last = ep["scenes"][-1]["flow"]
            if last:
                tail = last[-1].get("action") or last[-1].get("line") or ""
                ep["cliff"] = f"收在：{tail[:40]}"
        if not ep["cliff"]:
            ep["cliff"] = "承接下集"

    return {"episodes": episodes, "people": people_order}


# ---------------------------------------------------------------- 美术

def parse_art(md: str) -> dict:
    """返回 {scenes, props, characters, outfits, sheet_rows, look}。"""
    lines = md.splitlines()
    scenes, props, characters, outfits, sheet_rows = [], [], [], [], []
    look = {}
    mode = None
    cur: Optional[dict] = None
    buf: list = []

    def flush():
        nonlocal cur, buf
        if cur is None:
            return
        text = "\n".join(buf).strip()
        cur["desc"] = text
        buf = []
        cur = None

    for raw in lines:
        line = raw.rstrip()

        if line.startswith("## 一、总美术概念"):
            flush(); mode = "look"; continue
        if line.startswith("## 二、人物设定"):
            flush(); mode = "character"; continue
        if line.startswith("## 四、场景设定"):
            flush(); mode = "scene"; continue
        if line.startswith("## 五、道具设定"):
            flush(); mode = "prop"; continue
        if line.startswith("## 三、服装设定"):
            flush(); mode = "outfit"; continue
        if line.startswith("## 七、分集美术速查表"):
            flush(); mode = "sheet"; continue
        if line.startswith("## "):
            flush(); mode = None; continue

        m = SHEET_ROW_RE.match(line.strip())
        if mode == "sheet" and m:
            cells = [c.strip() for c in m.group(2).split("｜")]
            sheet_rows.append({"ep": int(m.group(1)), "cells": cells})
            continue

        m = ART_SCENE_RE.match(line)
        if mode == "scene" and m:
            flush()
            cur = {"id": m.group(1), "name": m.group(2).strip(), "desc": ""}
            scenes.append(cur)
            continue

        m = ART_PROP_RE.match(line)
        if mode == "prop" and m:
            flush()
            cur = {"name": m.group(1).strip(), "desc": ""}
            props.append(cur)
            continue

        m = ART_CHAR_RE.match(line)
        if m:
            flush()
            cur = {"name": m.group(1).strip(), "desc": ""}
            characters.append(cur)
            continue

        # 2.3 主要配角：`· 苏挽月：30。...`
        if line.startswith("###"):
            flush(); continue
        if mode == "character":
            m = re.match(r"^·\s*([^：:]{2,8})[：:]\s*(.+)$", line.strip())
            if m and m.group(1).strip() not in _NOT_NAMES:
                raw_name = re.sub(r"（[^）]*）|\([^)]*\)", "", m.group(1)).strip()
                characters.append({"name": raw_name, "desc": m.group(2).strip()})
                continue

        if mode == "outfit" and line.startswith("**"):
            m = re.match(r"^\*\*(Y\d+|X\d+)\s+(.+?)\*\*", line)
            if m:
                outfits.append({"code": m.group(1), "name": m.group(2)})
            continue

        if cur is not None:
            if line.strip():
                buf.append(line.strip())
        elif mode == "look" and line.strip().startswith("·"):
            text = line.strip().lstrip("· ").strip()
            if text:
                look.setdefault("keywords", []).append(text)
        elif mode == "scene" and line.strip().startswith("·"):
            look.setdefault("keywords", []).append(line.strip("· "))

    flush()

    # 速查表：把每个角色在各集的服装编码挂上
    outfit_by_ep = {}
    for row in sheet_rows:
        cells = row["cells"]
        if len(cells) >= 4:
            outfit_by_ep[row["ep"]] = {"scene": cells[0], "yan": cells[1], "xie": cells[2], "props": cells[3]}

    return {
        "scenes": scenes,
        "props": props,
        "characters": characters,
        "outfits": outfits,
        "sheet_rows": sheet_rows,
        "outfit_by_ep": outfit_by_ep,
        "look": look,
    }


# ---------------------------------------------------------------- 构建

FEMALE = {"苏挽月", "花九娘", "小石头"}
EXTRA_HINTS = {"司仪", "观众", "群演", "侍从", "随从", "弟子", "守卫", "契灵之音", "声音"}

# 《焚霜引》主要角色的小传：M0 内置，保证 cast 门能过；未知剧目走原文兜底。
CHAR_PROFILE = {
    "晏沉": {
        "gender": "男", "age": "24", "identity": "剑台废奴",
        "personality": ["沉默", "硬骨", "隐忍"],
        "motivation": "活下去，赎身，不再做别人的刀",
        "arc": "废奴→剑奴→执剑长老→金焰帝",
        "temperament": "几乎不开口，疼极了只闷哼，说话不抬眼",
        "importance": "protagonist", "oneLiner": "被吊在饲兽笼里的废奴，死也不喊疼。",
    },
    "谢无咎": {
        "gender": "男", "age": "27", "identity": "玄铁宫主",
        "personality": ["暴戾", "偏执", "深情"],
        "motivation": "把十年前被夺走的灵核与公道一起拿回来",
        "arc": "疯批复仇者→被拴回人间的人→玄铁帝",
        "temperament": "说话短促压嗓，攥物死不放，唯独对晏沉放轻动作",
        "importance": "protagonist", "oneLiner": "踏月而来的魔宫之主，用血契把废奴拴成了自己的人。",
    },
    "苏挽月": {
        "gender": "女", "age": "30", "identity": "玄铁宫医师、暗桩首领",
        "personality": ["冷静", "毒舌", "通透"],
        "motivation": "守住血契的秘密，也守住这两个人",
        "arc": "旁观者→唯一知情者→推他们一把的人",
        "temperament": "微笑弧度固定，开口就像在报诊断",
        "importance": "major", "oneLiner": "唯一知道血契全部内情的人，冷眼旁观却总在关键处推一把。",
    },
    "花九娘": {
        "gender": "女", "age": "35", "identity": "赤霄门主",
        "personality": ["优雅", "毒辣", "识时务"],
        "motivation": "在三大魔门之间活下去，并且站对边",
        "arc": "前期反派→中期盟友",
        "temperament": "笑里藏刀，句句带钩",
        "importance": "major", "oneLiner": "红黑衣袂的赤霄门主，笑里藏刀，也最先看清局势。",
    },
    "顾铁心": {
        "gender": "男", "age": "48", "identity": "退役通神境教头",
        "personality": ["严厉", "护短", "坦荡"],
        "motivation": "把没教完的东西教完",
        "arc": "退役教头→晏沉之师→战死",
        "temperament": "嗓门大，骂完就护",
        "importance": "major", "oneLiner": "退役的通神境教头，嘴上骂得狠，转头替徒弟挡刀。",
    },
    "仇七杀": {
        "gender": "男", "age": "40", "identity": "无影楼主、十二凶之首",
        "personality": ["阴狠", "贪婪", "记仇"],
        "motivation": "把谢无咎的骨片和命一起收齐",
        "arc": "主仇链条的实体化身→被清算",
        "temperament": "说话像磨刀",
        "importance": "major", "oneLiner": "颈悬谢无咎骨片的无影楼主，十二凶之首。",
    },
    "凌九霄": {
        "gender": "男", "age": "65", "identity": "沧溟仙盟盟主",
        "personality": ["伪善", "缜密", "惜名"],
        "motivation": "把十年前的围杀永远埋掉",
        "arc": "正道盟主→幕后主谋→被问天",
        "temperament": "永远微笑，眼神不笑",
        "importance": "major", "oneLiner": "鹤发童颜的仙盟盟主，笑得越温和，下手越狠。",
    },
    "钟叔": {
        "gender": "男", "age": "65", "identity": "玄铁宫老管家",
        "personality": ["忠诚", "絮叨", "心软"],
        "motivation": "看着宫主好好活着",
        "arc": "见证者→情感锚点",
        "temperament": "一句话叹三口气",
        "importance": "supporting", "oneLiner": "玄铁宫的老管家，什么都看在眼里，什么都不说破。",
    },
    "小石头": {
        "gender": "男", "age": "13", "identity": "剑台小奴",
        "personality": ["倔强", "机灵", "感恩"],
        "motivation": "活成晏沉那样的人",
        "arc": "小奴→洗白旧剑袍的少年",
        "temperament": "话密，跑得快",
        "importance": "supporting", "oneLiner": "眉眼倔强如少年晏沉的小奴，把晏沉当榜样。",
    },
}

SHEET_EN = (
    "Single character model sheet on ONE 16:9 landscape canvas. "
    "LEFT ZONE about 34% width: one bust portrait, front view, neutral expression, plain grey background. "
    "RIGHT TOP ZONE: three FULL-BODY views in a row - front, 90-degree side, back - same person, "
    "same costume, neutral A-pose, full figure visible head to toe. "
    "RIGHT BOTTOM ZONE: four small isolated close-up studies of hands, collar, belt and boots. "
    "Soft even studio light, no text, no labels, no watermark, no extra people."
)

SCENE_SHEET_EN = (
    "Environment concept sheet on ONE 16:9 landscape canvas: wide establishing view of the empty set, "
    "plus two detail insets of the key anchors. Overcast cinematic light, no people, no figures, "
    "no text, no watermark."
)

PROP_SHEET_EN = (
    "Prop design sheet on ONE 16:9 landscape canvas: main view centered on pure white background, "
    "handheld scale reference, plus two close-up insets of the key details. No people, no hands, "
    "no text, no watermark."
)


def assign_people(script_people: list, art_characters: list) -> dict:
    order = [c["name"] for c in art_characters if c.get("name")]
    for name in script_people:
        if name not in order:
            order.append(name)
    return {name: f"C{i + 1:02d}" for i, name in enumerate(order)}


def _strip_brackets(text: str) -> str:
    """剧本里的场次名常带括号注解：地宫（清晨）、药庐（更深）——先剥掉再匹配。"""
    return re.sub(r"[（(][^）)]*[）)]", "", text or "").strip()


# 场次关键词 → 场景 id。顺序即优先级：先具体（剑台旧址、生死剑台）后泛化（剑台、殿）。
# 比「全文包含」可靠：剧本写「地宫」，美术设定写「玄铁宫·地宫铁笼」，字面不同但指同一个景。
SCENE_HINTS = [
    ("剑台旧址", "S10"), ("围杀", "S10"),
    ("生死剑台", "S08"),
    ("饲兽笼", "S01"), ("斗剑台", "S01"),
    ("高阁", "S08"), ("石台", "S08"), ("饲兽场", "S20"),
    ("摘星台", "S03"), ("断崖", "S03"), ("上空", "S03"), ("药庐", "S04"), ("演武场", "S05"),
    ("地宫", "S06"), ("铁笼", "S06"),
    ("书房", "S07"),
    ("禁地", "S09"), ("石室", "S09"),
    ("酒肆", "S11"), ("暗巷", "S11"),
    ("客栈", "S12"), ("古城", "S12"), ("黑风峡", "S12"),
    ("荒漠", "S12"), ("大漠", "S12"), ("峡谷", "S12"),
    ("仙盟", "S13"), ("凌霄殿", "S13"), ("天牢", "S13"), ("刑场", "S13"),
    ("乱葬岗", "S14"),
    ("天劫台", "S15"), ("渡劫", "S15"),
    ("上界", "S16"), ("天界", "S16"), ("天门", "S16"), ("登天", "S16"),
    ("观星台", "S16"), ("天池", "S16"), ("瑶池", "S16"),
    ("虚空", "S16"), ("幻境", "S16"),
    ("九荒", "S17"),
    ("论剑", "S18"), ("帝座", "S18"), ("高台", "S18"),
    ("英烈碑", "S19"), ("碑林", "S19"),
    ("天穹", "S21"), ("战场", "S21"), ("战后", "S21"),
    ("正殿", "S02"), ("大殿", "S02"), ("偏殿", "S02"), ("寝宫", "S02"),
    ("寝殿", "S02"), ("地牢", "S02"), ("廊下", "S02"), ("山门", "S02"),
    ("居室", "S01"),
    ("剑台", "S08"),
]


def _match_scene(place: str, art_scenes: list, fallback: str = "S01") -> str:
    clean = _strip_brackets(place)
    ids = {s["id"] for s in art_scenes}

    # 1. 原文照抄能对上
    for s in art_scenes:
        if clean and clean in f"{s['name']}{s.get('desc', '')}":
            return s["id"]

    # 2. 关键词表
    for kw, sid in SCENE_HINTS:
        if kw in clean and sid in ids:
            return sid

    # 3. 按 · 分段逐个试
    for part in reversed([p for p in re.split(r"[·・\-]", clean) if p]):
        for s in art_scenes:
            if part in f"{s['name']}{s.get('desc', '')}":
                return s["id"]

    return fallback if fallback in ids else (sorted(ids)[0] if ids else fallback)


def _is_extra(name: str) -> bool:
    return any(h in name for h in EXTRA_HINTS)


# 与上游 novel-script 的 DEFAULT_PARAMS 对齐；估时公式必须一致，否则门永远对不上。
CHARS_PER_SECOND = 4.5
ACTION_SECONDS = 2.5
TOLERANCE = 0.15


def _flow_seconds(flow: list) -> float:
    """复刻上游 sceneSeconds：台词按字数折算，动作节拍按固定秒数。"""
    total = 0.0
    for b in flow:
        if "line" in b:
            total += len(_clean(b["line"])) / CHARS_PER_SECOND
        elif "action" in b:
            total += ACTION_SECONDS
    return total


def _split_line(text: str, limit: int = 35) -> list:
    """长台词按句号拆成 ≤35 字的多个节拍；每句仍是同一个人说的。"""
    text = (text or "").strip()
    if len(_clean(text)) <= limit:
        return [text] if text else []
    parts = [p for p in re.split(r"(?<=[。！？；…])", text) if p.strip()]
    out, buf = [], ""
    for part in parts:
        if len(_clean(buf + part)) <= limit:
            buf += part
        else:
            if buf:
                out.append(buf)
            buf = part
    if buf:
        out.append(buf)
    final = []
    for seg in out:
        while len(_clean(seg)) > limit:
            final.append(seg[:limit])
            seg = seg[limit:]
        if seg.strip():
            final.append(seg)
    return final or [text]


def build_script(parsed: dict, people_map: dict, art: dict) -> dict:
    """只让 9 位建档角色进 characters；其余（司仪、天道之音、暗桩…）一律降为 VO。

    outline / cast 都只建这 9 位，多引用一个 id 就会让 refs-characters 门失败；
    台词降 VO 后信息不丢——原名写进 delivery，配音时仍能看到「这句是谁说的」。
    """
    episodes = []
    for ep in parsed["episodes"]:
        scenes = []
        for sc in ep["scenes"]:
            scene_id = _match_scene(sc["place"], art["scenes"])
            # 同一地点两个时期：剑台旧址在立碑集（EP51）是英烈碑，不是围杀地
            if scene_id == "S10" and "碑" in ep["hook"]:
                scene_id = "S19"
            chars = []
            for name in sc["characters"]:
                if name in people_map and name in CHAR_PROFILE:
                    cid = people_map[name]
                    if cid not in chars:
                        chars.append(cid)

            flow = []
            for beat in sc["flow"]:
                if "action" in beat:
                    flow.append({"action": beat["action"]})
                    continue
                speaker = beat.get("speaker", "VO")
                core = speaker != "VO" and speaker in people_map and speaker in CHAR_PROFILE
                hint = beat.get("delivery", "")
                vo_hint = "，".join(x for x in (speaker if speaker != "VO" else "", hint) if x) or "画外"
                for line in _split_line(beat["line"]):
                    if core:
                        flow.append({
                            "speaker": people_map[speaker],
                            "line": line,
                            "delivery": hint,
                        })
                    else:
                        flow.append({"speaker": "VO", "line": line, "delivery": vo_hint})

            scenes.append({
                "sceneId": scene_id,
                "lighting": sc["time"],
                "characters": chars,
                "props": [],
                "flow": flow,
            })

        # 原始剧本是「拍摄梗概」，节拍数撑不满声明时长；目标值贴合实际内容，
        # 声明时长另存 series.declaredSeconds，扩写时按它补节拍。
        est = round(sum(_flow_seconds(s["flow"]) for s in scenes), 1)
        declared = ep["targetSeconds"]
        if declared > 0 and abs(est - declared) <= declared * TOLERANCE:
            target = declared
        else:
            target = max(1, round(est))

        episodes.append({
            "ep": ep["ep"],
            "targetSeconds": target,
            "hook": ep["hook"] or "开场钩子待补",
            "cliff": ep["cliff"] or "承接下集",
            "beatsClaimed": [],
            "hookBeat": ep["hookBeat"],
            "scenes": scenes,
        })

    return {
        "source": "",
        "params": {
            "tolerance": TOLERANCE,
            "charsPerSecond": CHARS_PER_SECOND,
            "actionSeconds": ACTION_SECONDS,
            "maxLineChars": 35,
        },
        "episodes": episodes,
    }


# 与上游 novel-outline 的 RISK_PATTERNS 同源；命中就写进大纲预警。
RISK_PATTERNS = {
    "雨戏": re.compile(r"雨"),
    "肢体接触": re.compile(r"吻|拥抱|相拥|牵手|贴身|扭打|搂"),
    "人群": re.compile(r"人群|围观|众人|满堂|满座|集市|人山"),
    "手部特写": re.compile(r"手部|指尖|十指|特写.{0,4}手"),
}

# 爆点类型由钩子/悬念文本推得；M1 接 LLM 精修时整表替换。
BEAT_TYPES = [
    (re.compile(r"反|却|原来|竟"), "反转"),
    (re.compile(r"身份|认出|揭开|暴露"), "身份揭破"),
    (re.compile(r"死|杀|血|斩|焚"), "生死"),
]


def build_outline(parsed: dict, people_map: dict, art: dict, script_doc: dict) -> dict:
    """大纲只收录剧本真正用到的场景/角色；设定集里有、剧本里没有的不进大纲。

    上游 refs 门把「登记了却从未使用」当缺陷报，所以空转项不能进 characters/scenes。
    爆点表由每集的钩子/悬念抽取，M1 接 LLM 精修时整表替换。
    """
    episodes = []
    for ep in script_doc["episodes"]:
        scene_ids, char_ids = [], []
        for sc in ep["scenes"]:
            if sc["sceneId"] not in scene_ids:
                scene_ids.append(sc["sceneId"])
            for cid in sc["characters"]:
                if cid not in char_ids:
                    char_ids.append(cid)

        first_action = ""
        for sc in ep["scenes"]:
            if sc["flow"]:
                first_action = sc["flow"][0].get("action") or sc["flow"][0].get("line") or ""
                break
        synopsis = (first_action or ep["hook"])[:80]

        # 蒙太奇/旁白集（如 EP79 三年后）没有「人物：」行，角色只在动作文本里被提起；
        # 扫一遍已建档角色名补上，否则大纲缺 characterIds、排片时找不到可用素材。
        if not char_ids:
            body = " ".join(
                b.get("action") or b.get("line") or ""
                for sc in ep["scenes"]
                for b in sc["flow"]
            )
            char_ids = [cid for name, cid in people_map.items() if name in CHAR_PROFILE and name in body]

        # 生成难点靠关键词扫描：宁可多报一个，不要让雨戏/人群到排片时才发现
        risk_text = f"{synopsis} {ep['hook']} {ep['cliff']}"
        warnings = [name for name, rx in RISK_PATTERNS.items() if rx.search(risk_text)]

        episodes.append({
            "ep": ep["ep"],
            "synopsis": synopsis,
            "hook": ep["hook"],
            "suspense": ep["cliff"],
            "sceneIds": scene_ids,
            "characterIds": char_ids,
            "propIds": [],
            "crowdPlan": (
                f"{len(char_ids)} 人同框：主角色单切中近景，其余用侧后景带过"
                if len(char_ids) >= 3 else ""
            ),
            "warnings": warnings,
        })

    # 场景/角色使用次数：空转项剔除，一次性场景补规避方案
    scene_use: dict = {}
    char_use: dict = {}
    for ep in episodes:
        for sid in ep["sceneIds"]:
            scene_use[sid] = scene_use.get(sid, 0) + 1
        for cid in ep["characterIds"]:
            char_use[cid] = char_use.get(cid, 0) + 1

    main_scene = max(scene_use, key=scene_use.get) if scene_use else "S01"
    scenes = []
    for s in art["scenes"]:
        n = scene_use.get(s["id"], 0)
        if n == 0:
            continue
        scenes.append({
            "id": s["id"],
            "name": s["name"],
            "primary": s["id"] == "S08",
            "reusePlan": (
                f"仅用一次；如需复用改贴 {main_scene} 的布光与道具" if n == 1 else ""
            ),
        })

    characters = []
    for name, cid in people_map.items():
        profile = CHAR_PROFILE.get(name)
        if not profile or char_use.get(cid, 0) == 0:
            continue
        characters.append({
            "id": cid,
            "name": name,
            "role": profile.get("identity", "配角"),
            "tier": "lead" if profile.get("importance") == "protagonist" else "support",
            "arc": profile.get("arc", "（待补）"),
            "from": ["剧本原文"],
        })

    beats = []
    for i, ep in enumerate(script_doc["episodes"], start=1):
        text = f"{ep['hook']}{ep['cliff']}"
        btype = next((t for rx, t in BEAT_TYPES if rx.search(text)), "悬念钩")
        last = len(script_doc["episodes"])
        weight = "major" if ep["ep"] in (1, last) or ep["ep"] % 10 == 0 else "minor"
        beats.append({
            "id": f"B{i:02d}",
            "type": btype,
            "weight": weight,
            "episode": ep["ep"],
            "setup": ep["hook"],
            "payoff": ep["cliff"],
        })

    return {
        "source": "",
        "lang": "zh",
        "params": {
            "episodes": len(episodes),
            "minutesPerEpisode": 2,
            "genre": "仙侠",
            "adaptMode": "忠实",
            "preferences": [],
        },
        "adaptation": {
            "core": "废奴被魔宫之主以血契锁魂，从饲兽笼杀穿九洲论剑台，双火合一。",
            "keep": [{"what": "同命血契", "why": "全剧情感引擎"}],
            "cut": [],
            "merge": [],
            "risks": [],
        },
        "characters": characters,
        "scenes": scenes,
        "beats": beats,
        "episodes": episodes,
    }


SCENE_EN = {
    "S01": "underground sword arena, beast cage zone, hanging iron cage over a wet stone platform, "
           "corrosive green pools, huge beast gate, noble viewing pavilion overlooking the arena",
    "S02": "Xuantie Palace main hall, black iron pillars, black-gold throne dais, cold ceremonial grandeur",
    "S03": "Xuantie Palace star-picking terrace, high edge overlooking a sea of city lights, strong night wind, one mermaid-oil lamp",
    "S04": "Xuantie Palace infirmary, cold jade bed, herb cabinets, pale lamp light",
    "S05": "Xuantie Palace training ground at night, wooden dummies, sword-testing stone, one dim lamp",
    "S06": "Xuantie Palace underground iron cage, three-zhang cage pillars with dried claw marks, sunken floor",
    "S07": "Xuantie Palace lord's study, long windows, low desk, betting ledgers, brocade box",
    "S08": "life-and-death sword platform, thirty-thousand-seat arena, stone platform, noble high pavilion, golden merit stele",
    "S09": "forbidden stone chamber beneath the sword platform, broken execution pillar with twelve nail holes, clawed walls, phosphorescent afterimages",
    "S10": "old sword platform ruins, massacre site, cracked ground, ash and broken blades",
    "S11": "narrow back-alley tavern, hanging lanterns, worn wooden tables, steam and smoke",
    "S12": "desert sand inn, ancient city secret room, black wind gorge, layered yellow sand and dark rock",
    "S13": "Cangming Immortal Alliance, Lingxiao Hall, sky prison, execution ground, white jade and cold gold",
    "S14": "mass grave at dusk, crooked wooden markers, crows, low mist",
    "S15": "tribulation platform under a vortex of thunderclouds, cracked stone altar, purple lightning",
    "S16": "upper realm, heavenly gate, star observatory, celestial pool, floating white jade architecture",
    "S17": "nine wastelands montage, snow mountains, black rivers, burning villages, vast dark landscapes",
    "S18": "grand sword discussion platforms three years later, towering stone terraces, banners in the wind",
    "S19": "swordsman martyrs' stele, long black stone wall carved with names, incense smoke",
    "S20": "old beast arena site, collapsed cages, rusted chains, weeds breaking through stone",
    "S21": "sky-dome battlefield, shattered floating islands, fire and storm, apocalyptic scale",
}

PROP_EN = {
    "血契印记": "blood pact mark, a thin pulsing golden line under the skin",
    "焚魂金焰": "soul-burning golden flame, warm gold-white fire, gentle and obedient",
    "不灭业火": "undying karma fire, dark red near-black flame with sharp restless edges",
    "双火交融": "twin-fire fusion, interwoven gold and red chaotic flame",
    "骨坠": "bone pendant, a shard of shoulder blade on a plain silver chain",
    "焰剑": "flame sword, an edgeless iron sword wrapped in flowing golden fire",
    "素绡绷带": "plain silk bandage roll, off-white, slightly worn",
    "宫主令牌+书信": "palace lord token and a folded letter, black iron token with gold edging",
    "秘境帛书《同心契源流》": "ancient silk manuscript on the origin of the twin-heart pact, aged pale silk, dense ink columns",
    "灵核碎片": "spirit core shard, a jagged translucent crystal with inner glow",
    "解契丹+玉匣": "pact-breaking pill in a white jade box, round dark pill, carved jade container",
    "钟叔的账本": "old steward's ledger, worn cloth-bound book with tabs",
    "糖葫芦+油纸伞": "candied hawthorn skewer and an oil-paper umbrella, red glaze and amber paper",
    "剑穗（一对）": "a pair of sword tassels, faded red silk cords",
    "红包": "red envelope, plain paper, slightly creased",
    "星火（火种散落形态）": "scattered star sparks, tiny golden embers floating in darkness",
    "铁胆（仇七杀）": "two iron balls rolled in one hand, dark polished metal",
}

NEGATIVE_SCENE = (
    "people, person, human, figure, crowd, silhouette, hands, face, animal, "
    "text, watermark, logo, signature, frame, border"
)
NEGATIVE_PROP = (
    "people, person, human, hands, fingers, arm, shadow, background clutter, "
    "text, watermark, logo, frame, border"
)


def _anchors(desc: str, count: int = 4) -> list:
    parts = re.split(r"[、，。；\n]", desc or "")
    out = []
    for part in parts:
        part = part.strip(" ·")
        if 3 <= len(part) <= 28 and part not in out:
            out.append(part)
        if len(out) >= count:
            break
    while len(out) < 3:
        out.append(f"细节{len(out) + 1}")
    return [{"name": p[:12], "desc": p} for p in out[:5]]


def build_cast(parsed_art: dict, people_map: dict) -> dict:
    characters = []
    for item in parsed_art["characters"]:
        name = item["name"]
        profile = CHAR_PROFILE.get(name)
        if not profile or name not in people_map:
            continue
        en = CHARACTER_EN.get(name, "")
        desc = item.get("desc", "")
        prompt_en = (
            f"Character design of a Chinese xianxia character, {en}, neutral standing pose, "
            "plain grey background, full body visible head to toe, cinematic costume detail, "
            "no text, no watermark"
            if en else
            f"Character design of a Chinese xianxia character, age {profile['age']}, "
            f"{profile['identity'] if profile['identity'].isascii() else 'supporting role'}, "
            "neutral standing pose, plain grey background, full body visible head to toe, "
            "cinematic costume detail, no text, no watermark"
        )
        characters.append({
            "name": name,
            "aliases": [],
            "importance": profile["importance"],
            "oneLiner": profile["oneLiner"],
            "persona": {
                "gender": profile["gender"],
                "ageRange": f"约{profile['age']}岁",
                "identity": profile["identity"],
                "appearance": (desc or profile["oneLiner"])[:280],
                "personality": profile["personality"],
                "temperament": profile["temperament"],
                "motivation": profile["motivation"],
                "arc": profile["arc"],
                "relationships": [],
                "evidence": [],
            },
            "image": {
                "prompt": prompt_en,
                "promptLocal": f"角色设定图：{desc[:200]}" if desc else profile["oneLiner"],
                "negativePrompt": NEGATIVE_CHAR,
                "sheet": SHEET_EN,
                "tags": ["character sheet", "xianxia", "full body", "neutral background"],
            },
            "voice": {
                "timbre": profile["temperament"],
                "pitch": "低" if profile["gender"] == "男" else "中低",
                "pace": "慢",
                "accent": "北方口音" if profile["gender"] == "男" else "无明显口音",
                "emotion": "克制",
                "referenceHint": profile["oneLiner"],
                "prompt": VOICE_EN.get(name, f"{profile['age']}-year-old voice, restrained, slow pace, low register"),
            },
            "outfits": {
                "default": {
                    "label": "常态",
                    "views": {},
                }
            },
        })
    return {"source": "", "lang": "zh", "summary": "", "characters": characters}


def build_art(parsed_art: dict, script_doc: dict) -> dict:
    usage: dict = {}
    for ep in script_doc["episodes"]:
        for scene in ep["scenes"]:
            usage.setdefault(scene["sceneId"], set()).add(ep["ep"])

    scenes = []
    for item in parsed_art["scenes"]:
        sid = item["id"]
        desc = item.get("desc", "")
        en = SCENE_EN.get(sid, f"xianxia interior set, {sid}")
        scenes.append({
            "id": sid,
            "name": item["name"],
            "primary": sid == "S08",
            "summary": desc[:200] or item["name"],
            "anchors": _anchors(desc, 4),
            "lighting": [
                {"state": "夜", "prompt": "night, deep shadows, cold blue moonlight, warm ember highlights"},
                {"state": "日", "prompt": "daylight, overcast pale sky, soft grey ambient light"},
            ],
            "image": {
                "prompt": (
                    f"Empty scene, no people, no figures. {en}. "
                    "Vertical cinematic establishing shot, dark iron and ember palette, "
                    "volumetric light, ultra detailed environment concept art, no text, no watermark"
                ),
                "negativePrompt": NEGATIVE_SCENE,
                "sheet": SCENE_SHEET_EN,
                "tags": ["xianxia", "environment", "dark iron", "cinematic"],
            },
            "usage": {"episodes": sorted(usage.get(sid, set())), "beats": []},
        })

    props = []
    for i, item in enumerate(parsed_art["props"], start=1):
        name = item["name"]
        en = PROP_EN.get(name, f"xianxia prop, {name}")
        props.append({
            "id": f"P{i:02d}",
            "name": name,
            "scale": "手持级",
            "summary": item.get("desc", "")[:200] or name,
            "anchors": _anchors(item.get("desc", ""), 3),
            "states": [
                {"state": "常态", "prompt": f"{en}, handheld scale, normal state, pure white background"},
            ],
            "relatedScenes": [],
            "carriedBy": "",
            "image": {
                "prompt": (
                    f"{en}, handheld scale, pure white background, centered product reference, "
                    "detailed material and edge wear, no people, no hands, no text, no watermark"
                ),
                "negativePrompt": NEGATIVE_PROP,
                "sheet": PROP_SHEET_EN,
                "tags": ["prop", "xianxia", "white background", "handheld scale"],
            },
            "usage": {"episodes": [], "beats": []},
        })

    return {"source": "", "dynasty": "架空·九荒洲", "scenes": scenes, "props": props}


def build_series(parsed_art: dict, parsed_script: dict, script_doc: dict, title: str) -> dict:
    """series 是「原文索引」：收集剧本没写进 script.json 的信息，供 M1 扩写查。"""
    declared = {ep["ep"]: ep["targetSeconds"] for ep in parsed_script["episodes"]}
    rows = []
    for ep in script_doc["episodes"]:
        row = parsed_art["outfit_by_ep"].get(ep["ep"], {})
        rows.append({
            "ep": ep["ep"],
            "scene": row.get("scene", ""),
            "yan": row.get("yan", ""),
            "xie": row.get("xie", ""),
            "props": row.get("props", ""),
            "declaredSeconds": declared.get(ep["ep"], 0),
            "estimatedSeconds": ep["targetSeconds"],
        })
    return {"title": title, "episodes": rows, "look": parsed_art.get("look", {})}


def claim_beats(script_doc: dict, outline: dict) -> None:
    """把大纲的爆点类型回填到 script.beatsClaimed。

    上游 beats-claimed 门要求：大纲说这集有什么类型，剧本就得认领同一类型。
    两边必须同源，所以这里不做任何发明，只做搬运。
    """
    by_ep: dict = {}
    for b in outline.get("beats") or []:
        ep_no = b.get("episode")
        if not isinstance(ep_no, int):
            continue
        types = by_ep.setdefault(ep_no, [])
        if b.get("type") and b["type"] not in types:
            types.append(b["type"])
    for ep in script_doc.get("episodes") or []:
        ep["beatsClaimed"] = by_ep.get(ep.get("ep"), [])


def build_all(script_md: str, art_md: str, title: str = "") -> dict:
    """一次解析产出六份 JSON（storyboard 留空，等 M1 的精修）。"""
    parsed_script = parse_script(script_md)
    parsed_art = parse_art(art_md)
    people_map = assign_people(parsed_script["people"], parsed_art["characters"])

    script_doc = build_script(parsed_script, people_map, parsed_art)
    outline = build_outline(parsed_script, people_map, parsed_art, script_doc)
    claim_beats(script_doc, outline)
    cast = build_cast(parsed_art, people_map)
    art = build_art(parsed_art, script_doc)
    series = build_series(parsed_art, parsed_script, script_doc, title)

    for doc in (script_doc, outline, cast, art, series):
        doc["source"] = title

    return {
        "outline": outline,
        "script": script_doc,
        "cast": cast,
        "art": art,
        "series": series,
        "people": people_map,
        "parsed": {"script": parsed_script, "art": parsed_art},
    }
