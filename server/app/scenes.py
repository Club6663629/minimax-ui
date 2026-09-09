"""场景预设（skills 轻量落地）：通用 / AI 短剧 / 电商。

每个场景对应一段 skill 提示词模板，在提交 Context-IR 编译前拼接到用户原始
提示词之前，引导 H3-Context-IR 按对应创作范式编译（命名分节、镜头语言、
对齐 17n+5 帧网格的 cut times）。模板只做方向性引导，不覆盖用户具体内容。
"""

SCENES: dict[str, dict] = {
    "general": {
        "label": "通用",
        "skill": "",
    },
    "drama": {
        "label": "AI 短剧",
        "skill": (
            "【创作范式：AI 短剧】请按影视短剧的分镜语言编译："
            "明确角色、场景、情绪与冲突，镜头按叙事节奏切分（远景定场→中景对话→特写情绪），"
            "每段给出画面动作、运镜方向与台词/旁白提示，保持人物与场景一致性，"
            "结尾留钩子。"
        ),
    },
    "ecommerce": {
        "label": "电商",
        "skill": (
            "【创作范式：电商广告】请按产品广告片范式编译："
            "突出产品主体与卖点，镜头干净利落（产品特写→功能演示→场景使用），"
            "强调质感、光影与品牌调性，节奏明快，结尾给出行动号召。"
        ),
    },
}

DEFAULT_SCENE = "general"


def apply_scene(prompt: str, scene: str) -> str:
    """把场景预设 skill 提示词拼到用户提示词前（无 skill 时原样返回）。"""
    cfg = SCENES.get(scene) or SCENES[DEFAULT_SCENE]
    skill = cfg.get("skill", "")
    if not skill:
        return prompt
    return f"{skill}\n\n用户需求：{prompt}"
