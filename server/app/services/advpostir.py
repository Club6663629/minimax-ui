"""advideo8 · Content-IR 输出的「单镜化」结构化后处理（纯规则 / 确定性 / 0 付费）。

为什么需要（advideo7 实测，非推测）：
  Content-IR 正文自带 `[Shot 1] / [Shot 2] At 00:03.500 / [Shot 3] At 00:06.800` 与
  "the camera cuts to ..." 的切镜句 → ref2v 真的执行成多次硬切：
  scdet threshold=10 检出 B 臂 2 处硬切（t≈3.28s / 6.27s，与时间码 3.5/6.8 对齐），
  而尾缀 `[CAMERA] no scene change, no cuts` 压不住正文（A 臂也出现 1 处单帧尖峰）。
  → 结论：镜头控制必须写在**正文主句**里，且必须把正文里的切镜指令改写成连续运镜。

本模块做的事（只对视觉描述段动手，音效/配乐两段原样保留 —— IR 的真实增益在那）：
  1. 剥掉 `[Shot n]` 标记与 `00:03.500` 时间码，合并成一段连续叙述；
  2. 切镜→运镜："the camera cuts to a close-up" ⇒ "the camera smoothly moves to a close-up"
     （保留原本要展示的内容，只把"切"改为"移"，达成单镜）；
  3. 去抖动："that shakes slightly"/"shaky"/"handheld jitter" ⇒ steady（用户口径：镜头不要抖）；
  4. 句级去重（IR 每镜都会重复 no on-screen text 一句，只保留第一次）+ 保证结尾有无文字/无 logo 否定式；
  5. 正文首尾写死「单镜连续拍摄」硬约束（不依赖尾缀），并写死「以参考图 Picture 1 为准」的保真主句。

对外只暴露 post_process(ir_text) -> dict。纯函数、可单测。
"""

import re
from typing import Dict, List

LABEL_RE = re.compile(
    r"(?im)^\s*(integrated_multimodal_description|overall_soundscape|non_diegetic_music)\s*[:：]\s*"
)
SHOT_RE = re.compile(r"\[?\s*(?:shot|镜头)\s*\d+\s*\]?", re.I)
TIMECODE_RE = re.compile(r"(?:\(?\s*at\s*)?\b\d{1,2}:\d{2}(?:\.\d{1,3})?\s*\)?\s*[,，]?\s*", re.I)

# 切镜 → 连续运镜（顺序敏感，长的在前）
CUT_RULES = [
    (r"\b(?:the\s+)?camera\s+cuts\s+back\s+to\s+a\b", "the camera smoothly moves back to a"),
    (r"\b(?:the\s+)?camera\s+cuts\s+to\s+a\b", "the camera smoothly moves to a"),
    (r"\b(?:the\s+)?camera\s+cuts\s+back\s+to\b", "the camera smoothly moves back to"),
    (r"\b(?:the\s+)?camera\s+cuts\s+to\b", "the camera smoothly moves to"),
    (r"\b(?:hard|quick|fast|sharp|abrupt)\s+cuts?\s+to\s+a\b", "smooth move to a"),
    (r"\b(?:hard|quick|fast|sharp|abrupt)\s+cuts?\s+to\b", "smooth move to"),
    (r"\bcuts?\s+to\s+a\b", "smoothly moves to a"),
    (r"\bcuts?\s+to\b", "smoothly moves to"),
    (r"\bcut\s+away\s+to\b", "smoothly move to"),
    (r"\b(?:the\s+)?(?:shot|scene|frame|view)\s+(?:changes|switches|transitions|jump-?cuts)\s+to\b",
     "the camera smoothly moves to"),
    (r"\bjump\s*cuts?\b", "continuous move"),
    (r"\bcross-?cut(?:s|ting)?\b", "continuous move"),
    (r"\b(?:a\s+)?(?:quick\s+)?montage\s+of\b", "a continuous view of"),
    (r"\bmulti-?shot\b", "single continuous shot"),
    (r"\bshot\s+reverse\s+shot\b", "single continuous angle"),
]
# 抖动/甩镜 → 稳定运镜（用户口径：镜头不要抖）
SHAKE_RULES = [
    (r",?\s*that\s+shakes?\s+slightly\b", ""),
    (r"\bshakes?\s+slightly\b", "stays steady"),
    (r"\bshaky\b", "steady"),
    (r"\bhandheld\s+(?:camera\s+)?jitter\b", "stable camera"),
    (r"\bhandheld\b", "smooth stabilized"),
    (r"\bjitter(?:y|s|ing)?\b", "steady"),
    (r"\bwhip-?pan(?:s|ning)?\b", "slow pan"),
    (r"\bshake\b", "steady hold"),
]
NOTEXT_RE = re.compile(
    r"(no\s+on-?screen\s+text|no\s+text|no\s+captions?|no\s+subtitles?|no\s+logo|no\s+watermark|"
    r"free\s+of\s+any\s+on-?screen\s+text|without\s+text)", re.I)

TAKE_HEAD = (
    "[SINGLE TAKE] One single continuous take by one camera for the whole clip: the camera keeps rolling "
    "and moves smoothly and steadily between framings, with no cuts, no shot changes, no transitions, "
    "no jump cuts and no camera shake; the location, background, lighting and grading never change."
)
FIDELITY_MAIN = (
    "Whatever is described below, the woman wears exactly the product in the reference picture (Picture 1) - "
    "identical colour, material, construction, pattern, trims and product count, unchanged and fully in frame; "
    "any garment, accessory, colour or styling description below that differs from the reference picture must be "
    "ignored - the reference picture is the ONLY source of truth for the outfit."
)
NOTEXT_MAIN = "There is no on-screen text, no captions, no logo and no watermark anywhere in the frame."
TAKE_TAIL = (
    "The whole clip remains one unbroken shot: no cuts, no shot changes, no transitions, steady camera, no shake."
)


def parse_sections(text: str) -> Dict[str, str]:
    """把 Content-IR 的三段式输出拆成 dict（缺段时把全文当视觉描述）。"""
    text = text or ""
    marks = list(LABEL_RE.finditer(text))
    if not marks:
        return {"integrated_multimodal_description": text.strip()}
    out: Dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        key, body = m.group(1).lower(), text[m.end():end].strip()
        out[key] = (out[key] + "\n" + body).strip() if key in out else body
    return out


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?。！？])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower())


def clean_visual(text: str) -> Dict[str, object]:
    """只清洗视觉描述段：剥分镜/时间码 → 切镜改运镜 → 去抖动 → 句级去重。"""
    raw = text or ""
    stats = {"shots_in": len(SHOT_RE.findall(raw)), "timecodes_in": len(TIMECODE_RE.findall(raw))}
    t = TIMECODE_RE.sub(" ", raw)
    t = SHOT_RE.sub(" Then, ", t)
    t = re.sub(r"^\s*Then,\s*", "", t, flags=re.I)
    t = re.sub(r"\s*Then,\s*Then,\s*", " Then, ", t, flags=re.I)
    t = re.sub(r"([.!?])\s*Then,\s*", r"\1 Then ", t, flags=re.I)
    cuts_hit = 0
    for pat, rep in CUT_RULES:
        t, n = re.subn(pat, rep, t, flags=re.I)
        cuts_hit += n
    shakes_hit = 0
    for pat, rep in SHAKE_RULES:
        t, n = re.subn(pat, rep, t, flags=re.I)
        shakes_hit += n
    t = re.sub(r"\s+", " ", t).replace(" ,", ",").replace(" .", ".")
    t = re.sub(r"^\s*Then\s+", "", t, flags=re.I)
    # 句级去重：整句重复 + 「无文字」类句只留一次
    kept, seen, notext_seen = [], set(), False
    for s in _sentences(t):
        key = _norm(s)
        if key and key in seen:
            continue
        if NOTEXT_RE.search(s):
            if notext_seen:
                continue
            notext_seen = True
        seen.add(key)
        kept.append(s)
    body = " ".join(kept).strip()
    if body and body[-1] not in ".!?":
        body += "."
    stats.update({"cuts_rewritten": cuts_hit, "shake_rewritten": shakes_hit,
                  "sentences_out": len(kept), "notext_kept": notext_seen})
    if not notext_seen:
        body = (body + " " + NOTEXT_MAIN).strip()
    return {"body": body, "stats": stats}


def post_process(ir_text: str, *, en_labels: bool = True) -> Dict[str, object]:
    """Content-IR 原文 → 单镜化正文（可直接交 advprompt.wrap_video_prompt 拼 [FIDELITY]/[SET]/[CAMERA]/[AUDIO]）。"""
    sec = parse_sections(ir_text)
    vis = clean_visual(sec.get("integrated_multimodal_description", ""))
    visual = " ".join(x for x in (TAKE_HEAD, FIDELITY_MAIN, vis["body"], TAKE_TAIL) if x)
    parts = []
    if en_labels:
        parts.append("integrated_multimodal_description: " + visual)
    else:
        parts.append(visual)
    for key in ("overall_soundscape", "non_diegetic_music"):
        if sec.get(key):
            parts.append((key + ": " if en_labels else "") + sec[key].strip())
    text = "\n".join(parts)
    return {
        "text": text,
        "visual": visual,
        "sections": sec,
        "stats": vis["stats"],
        "raw_len": len(ir_text or ""),
        "text_len": len(text),
    }
