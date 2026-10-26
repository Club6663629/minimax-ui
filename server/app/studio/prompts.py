"""设定图提示词：把 shuohao 的视图规范搬到平台侧。

上游 `buildPrompt`（`skills/character-refs/scripts/core.mjs`）吃的是分层数据
（identity / face / hair / top / bottom / backCue），我们的 cast.json 只有一整段
`image.prompt`。MVP 不重建分层结构，只把上游**实测踩过的构图结论**逐条搬过来：

- 改图模型只听第一句，构图要求必须写在开头；
- 锚点正面要写成几何（shoulders/hips/feet square to the camera），否则模型会画成微侧身；
- 大头照开头必须 `Zoom in to an extreme close-up`，写可量化取景，且不写下装和鞋（写了就拉远）；
- 背面必须 `Rotate the camera 180 degrees around ...`；
- 一致性只写一句 keep list，不重复整套服装（列多了镜头又退回全身）。

视图规范与上游一致：front-full 是唯一锚点、唯一文生图；其余只参考锚点。
"""

# ---------------------------------------------------------------- 视图规范

ANCHOR = "front-full"

VIEW_SPECS = {
    "front-full": {"ratio": "2:3", "refs": [], "label": "正面全身（锚点）"},
    "face-front": {"ratio": "4:5", "refs": [ANCHOR], "label": "正脸大头照"},
    "side-full": {"ratio": "2:3", "refs": [ANCHOR], "label": "侧面 90°"},
    "back-full": {"ratio": "2:3", "refs": [ANCHOR], "label": "背面"},
    # 场景 / 道具只有一张设定图；沿用 art.json 里的 sheet 概念
    "sheet": {"ratio": "2:3", "refs": [], "label": "设定图"},
}

# 角色：四视图（默认档 2）；场景 / 道具：单张
ASSET_VIEWS = {
    "character": ["front-full", "face-front", "side-full", "back-full"],
    "scene": ["sheet"],
    "prop": ["sheet"],
}

BACKDROP = "Plain grey background, evenly lit."


def views_of(kind: str) -> list:
    return list(ASSET_VIEWS.get(kind, ["sheet"]))


def is_anchor(view: str) -> bool:
    return view in (ANCHOR, "sheet")


def spec_of(view: str) -> dict:
    return VIEW_SPECS.get(view, VIEW_SPECS["sheet"])


# ---------------------------------------------------------------- 角色描述的剥离

# 锚点专用措辞：派生图要换成各自的构图，这些短语留着会把镜头拽回全身
_ANCHOR_WORDS = (
    "character design",
    "neutral standing pose",
    "plain grey background",
    "full body visible",
    "cinematic costume detail",
    "no text",
    "no watermark",
)


def subject_of(data: dict) -> str:
    """从 `image.prompt` 里剥出「这个人长什么样」，供派生视图复用。

    剥不干净（用户改写过 prompt）时原样返回——宁可多带一点措辞，也不要丢掉外貌。
    """
    prompt = str(((data or {}).get("image") or {}).get("prompt") or "").strip()
    if not prompt:
        return ""
    parts = []
    for piece in prompt.split(","):
        text = piece.strip().rstrip(".")
        low = text.lower()
        if not text or any(word in low for word in _ANCHOR_WORDS):
            continue
        parts.append(text)
    return ", ".join(parts) or prompt


# ---------------------------------------------------------------- 各视图提示词

def _anchor_text(prompt: str) -> str:
    """锚点补几何：上游实测，只写 facing the camera 会被画成微侧身。"""
    pose = (
        "neutral standing pose, shoulders, hips and feet square to the camera, "
        "face pointing straight at the camera, arms relaxed at the sides"
    )
    if "neutral standing pose" in prompt:
        return prompt.replace("neutral standing pose", pose, 1)
    return f"{prompt.rstrip('. ')}. {pose}."


def _face_text(subject: str) -> str:
    who = subject or "this same character"
    return (
        "Zoom in to an extreme close-up head-and-shoulders portrait, passport-photo framing, "
        f"of this same character facing the camera squarely. {who}. "
        "Framing: the top of the head is just below the top edge of the image, the chin sits at "
        "the vertical middle of the image, and the bottom edge of the image cuts across the "
        "collarbones. Keep the same face, eyes, eyebrows and hairstyle as in the reference image. "
        "Framing reminder: the bottom edge cuts across the collarbones, so arms, sleeves and "
        f"cuffs are out of frame. {BACKDROP} No text, no watermark."
    )


def _side_text(subject: str) -> str:
    who = subject or "this same character"
    return (
        "Full-body side profile of this same character: the whole body and the head both face "
        "the right edge of the image, looking straight ahead to the right, not at the camera. "
        "Only the right side of the face is visible. Head to feet in frame. "
        f"{who}. Keep the same face, eyes and eyebrows, hairstyle, top and bottom as in the "
        f"reference image. {BACKDROP} No text, no watermark."
    )


def _back_text(subject: str) -> str:
    who = f" {subject}." if subject else ""
    return (
        "Rotate the camera 180 degrees around this same character: a full-body back view. "
        "The character stands with the back to the camera and the face is completely hidden. "
        "We see the back of the head with the hairstyle as seen from behind, the back of the "
        "top and the bottom, and the heels. Head to feet in frame."
        f"{who} Keep the same hairstyle, top and bottom as in the reference image. "
        f"{BACKDROP} No text, no watermark."
    )


def build(kind: str, data: dict, view: str) -> dict:
    """返回 {view, prompt, negative, ratio, refs, label}。"""
    data = data or {}
    image = data.get("image") or {}
    base = str(image.get("prompt") or "").strip()
    negative = str(image.get("negativePrompt") or "").strip()
    spec = spec_of(view)
    subject = subject_of(data) if kind == "character" else ""

    if view == "front-full":
        text = _anchor_text(base) if base else ""
        negative = ", ".join(x for x in (
            negative,
            "cropped feet, cropped head, three-quarter view, turned body, "
            "head turned to the side, contrapposto, dynamic pose",
        ) if x)
    elif view == "face-front":
        text = _face_text(subject)
        negative = ", ".join(x for x in (
            negative,
            "waist, torso, arms, hands, full body, legs, feet, wide shot, medium shot, "
            "three-quarter view, head turned to the side, profile, tilted head, "
            "multiple people, scenery",
        ) if x)
    elif view == "side-full":
        text = _side_text(subject)
        negative = ", ".join(x for x in (
            negative,
            "looking at camera, front view, three-quarter view, both eyes visible, cropped feet",
        ) if x)
    elif view == "back-full":
        text = _back_text(subject)
        negative = ", ".join(x for x in (
            negative,
            "face, eyes, nose, front view, buttons, pockets, looking at camera",
        ) if x)
    else:  # sheet：场景 / 道具原样用 art.json 的提示词
        text = base
        negative = negative

    return {
        "view": view,
        "prompt": text,
        "negative": negative,
        "ratio": spec["ratio"],
        "refs": list(spec["refs"]),
        "label": spec["label"],
    }
