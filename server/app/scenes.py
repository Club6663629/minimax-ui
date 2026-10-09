"""场景预设（轻量 skill）：通用 / AI 短剧 / 电商 / 音乐创作。

每个场景对应一句轻量 skill 提示词，在提交 Context-IR 编译前拼接到用户原始
提示词之前，只声明创作场景与方向；专业的分镜结构、镜头语言、节奏与文案
交由 H3-Context-IR 自行生成，避免过度约束干扰其输出。
"""

SCENES: dict[str, dict] = {
    "general": {
        "label": "通用",
        "skill": "",
    },
    "drama": {
        "label": "AI 短剧",
        "skill": (
            "【创作场景：AI 短剧】请以专业 AI 短剧的创作范式，将用户需求扩写为可直接用于视频生成的详细提示词，"
            "自主设计角色设定、场景描述、镜头分镜与逐秒画面，并保证角色与场景连贯一致。"
        ),
    },
    "ecommerce": {
        "label": "电商",
        "skill": (
            "【创作场景：电商广告】请以专业极简产品广告的创作范式，将用户需求扩写为可直接用于视频生成的详细提示词，"
            "自主设计产品展示、运镜转场、节奏与画面氛围，突出产品本体质感。\n"
            "【商品一致性硬约束（不可违反）】画面中的商品以参考图（the reference picture / Picture 1）为准："
            "形状、颜色、材质、结构、版型、印花、五金、件数与 logo 位置必须与参考图完全一致，"
            "不得重新设计、换色、改款、加印花或裁切。提示词里必须显式写出对参考图的指代"
            "（如 \"the product in the reference picture (Picture 1)\"）。\n"
            "【画面文字】除非用户明确给出了文案原文，否则禁止在画面中添加任何文字、字幕、标语、水印或 logo，"
            "提示词中必须写明 no on-screen text / no captions / no logo。\n"
            "【表述方式】只描述镜头语言（机位/景别/动作/光线/节奏）与画面氛围，"
            "不要用形容词重新描述商品本身（避免文字压过参考图）。"
        ),
    },
    "music": {
        "label": "音乐创作",
        "skill": (
            "【创作场景：音乐 MV】请以专业音乐 MV 的创作范式，将用户需求扩写为可直接用于视频生成的详细提示词，"
            "自主设计歌词与节拍的画面配合、运镜节奏与镜头衔接。"
        ),
    },
};

DEFAULT_SCENE = "general"


def apply_scene(prompt: str, scene: str) -> str:
    """把场景预设 skill 提示词拼到用户提示词前（无 skill 时原样返回）。"""
    cfg = SCENES.get(scene) or SCENES[DEFAULT_SCENE]
    skill = cfg.get("skill", "")
    if not skill:
        return prompt
    return f"{skill}\n\n用户需求：{prompt}"
