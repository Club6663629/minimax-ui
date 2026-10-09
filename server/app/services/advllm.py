"""电商广告片「LLM 提示词增强 agent」（DeepSeek，2026-10-09）。

advideo_image_prompt_mode=="llm" 时启用：由 deepseek-flash（关闭思考、支持图像理解）扮演
qwen-image-2.1 提示词改写 agent，**看着商品参考图** + 用户一句大白话 + 商品结构清单，改写成
qwen-image 可直接执行的**图像编辑指令的「镜头语言正文」**（场景/机位/景别/光线/构图/氛围/道具）。

铁律（与 advprompt.py / advenhance.py 同一套，见《电商广告片产品设计文档》§8.3、§9）：
1. 商品一致性 > 用户创作意图：商品参考图是唯一真源，agent 只描述**可见**属性，禁止臆造/改款/改色/加印花；
2. 本模块只产出「镜头语言正文」，**不产保真前缀、不产负向词、不产机位收尾**——那三段由 advimage.py 用代码
   拼装（不可被 LLM 输出覆盖），保证任何 LLM 漂移都不会丢保真；
3. 生图提示词只喂 qwen21 图像模型，绝不回灌视频增强（提示词隔离硬规则）；
4. 失败 / 无 key / 空输出 → 抛 AdvLLMError，由调用方（advimage）静默回退本地规则增强（0 付费）。

DeepSeek 接口事实（官方文档 api-docs.deepseek.com/zh-cn/guides/vision 核对）：
- OpenAI 兼容 POST {base}/chat/completions；model=deepseek-flash；
- 关思考：请求体顶层 "thinking": {"type": "disabled"}；
- vision：图片以 {"type":"image_url","image_url":{"url":"data:<mime>;base64,...","detail":...}} 内容块传入，
  **图片仅允许出现在 user 消息**（system/assistant 带图会 400）；单图 token 上限约 1024。
"""
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from ..config import settings
from . import advprompt

logger = logging.getLogger(__name__)

# vendored skill 目录（server/app/services/skills/qwen_image_2_1，见其 SOURCE.md）
SKILL_DIR = Path(__file__).resolve().parent / "skills" / "qwen_image_2_1"
# 运行时只加载 Edit 轨（本任务是「商品参考图 → 广告图」的图像编辑）
_SKILL_FILES = ("SKILL.md", "references/edit_rules.md", "references/cheat_sheet.md")
_SKILL_FILE_CAP = 20000      # 单文件截断上限（字符），防 token 膨胀
_SKILL_TOTAL_CAP = 60000     # 拼接总上限（字符）

_SKILL_CACHE: Optional[str] = None


class AdvLLMError(Exception):
    """LLM 增强失败（无 key / 网络 / 空输出 / 解析失败）；调用方据此回退本地规则。"""


# ------------------------------------------------------------------ 系统级保真约束
# 置于 system prompt 最前，声明优先级高于 skill；措辞对齐 advprompt.PE_FIDELITY_SYSTEM 铁律 1/3/7。
FIDELITY_SYSTEM = """# 电商商品保真增强器 v1（E-commerce Product-Fidelity Rewriter，最高优先级）

你是一名电商广告图的提示词改写专家。下面附带的「Qwen-Image-2.1 Prompt Optimizer」skill 是通用改写规范；
**当 skill 与本节冲突时，一律以本节为准**。本次任务**不是自由创作，而是「把指定商品放进指定场景」**，
是**图像编辑**任务，绝不是从零文生图。

【铁律 1｜商品唯一真源（最重要）】
- user 消息里附带的那张图，就是**商品参考图**，是商品的**唯一真源**。请先「看图」再写。
- 商品的全部属性（形状、轮廓、材质与纹理、颜色、件数、标签排版、logo 位置与形状、既有文字）必须与参考图
  完全一致，任何一项**不得增删改**：不得改款、改色、加印花、加图案、加 logo、加文字、改件数、改比例。
- 只描述图中**可见**的属性；看不清或不确定的一律**省略**（写「未见」也不要臆造）。禁止编造参考图里没有的细节。
- **禁止用形容词重新描述商品外观**：文字重述会让下游模型按文字重绘商品，一致性立刻崩。指代商品时用
  「the product from the reference image」这类**指图**措辞，而不是罗列它的颜色/材质/款式。

【铁律 2｜你只负责「镜头语言」】
你可以自由创作的只有：场景、机位、景别、光线、氛围、构图、留白、道具。
商品本体、商品件数保持不变。**不要输出保真前缀、不要输出负向词、不要输出机位编号收尾条款**——
这些由后端用代码统一拼装，你写了也会被丢弃。

【铁律 3｜默认不生成任何文字】
除非 user 消息给出了**确切文案**（引号内原文），否则画面内不得出现任何文字/字母/字幕/水印/logo/标语；
若给了确切文案，只允许**逐字引用**（不改写、不翻译、不追加），同一画面文案单语。

【铁律 4｜单张画面】
最终只描述**一张完整照片**（one single full-frame photograph）。禁止拼贴、多格、分屏、多视角并置、画中画。

【铁律 5｜人物】
若 user 消息说明附带了人物/模特参考：保持其脸型、发型、发色与配饰不变；不得新增珠宝、帽子、包、手机等物；
手部不得遮挡商品关键结构。若没有人物参考：可自行设计模特与姿态，但不得改变商品。

【输出格式（严格遵守）】
只输出**一段连续的英文描述**（决策 A：描述性正文），聚焦场景/机位/景别/光线/构图/氛围/道具。
不要 JSON、不要 Markdown、不要标题、不要分点、不要解释、不要前言后语、不要画幅比例或像素数字、
不要引号包裹整段。就一段纯英文正文。"""


def _load_skill() -> str:
    """装配 vendored qwen-image-2.1 skill（Edit 轨）为文本；进程内缓存。缺文件不致命（返回已加载部分）。"""
    global _SKILL_CACHE
    if _SKILL_CACHE is not None:
        return _SKILL_CACHE
    parts: List[str] = []
    for rel in _SKILL_FILES:
        fp = SKILL_DIR / rel
        try:
            txt = fp.read_text(encoding="utf-8").strip()
        except OSError:
            logger.warning("qwen-image skill 文件缺失或不可读：%s（跳过）", fp)
            continue
        if len(txt) > _SKILL_FILE_CAP:
            txt = txt[:_SKILL_FILE_CAP] + "\n…[truncated]"
        parts.append(f"<!-- {rel} -->\n{txt}")
    blob = "\n\n---\n\n".join(parts)
    if len(blob) > _SKILL_TOTAL_CAP:
        blob = blob[:_SKILL_TOTAL_CAP] + "\n…[truncated]"
    _SKILL_CACHE = blob
    if not blob:
        logger.warning("qwen-image skill 未加载到任何内容（目录 %s）；agent 仅靠保真 system 运行", SKILL_DIR)
    return blob


def _build_system_prompt() -> str:
    """system = 保真约束（最高优先级） + vendored skill（Edit 轨）。system 消息不含图片（DeepSeek 限制）。"""
    skill = _load_skill()
    if skill:
        return FIDELITY_SYSTEM + "\n\n---\n\n# 附：Qwen-Image-2.1 提示词改写 skill（Edit 轨，从属规则）\n\n" + skill
    return FIDELITY_SYSTEM


def _build_user_text(*, scenario: str, specs: str, category: str, aspect_ratio: str) -> str:
    """user 文本块：输入图角色行 + 商品结构清单 + 场景需求 + 画幅（图片另以 image_url 块传入）。"""
    seg: List[str] = [advprompt.image_role_line(images=["商品图（唯一真源：形状/材质/颜色/件数/标签/logo 均不得改变）"])]
    if specs:
        seg.append("[商品结构清单] %s" % specs.strip())
    if category:
        seg.append("[品类] %s" % category)
    seg.append("[场景/需求] %s" % (scenario or "").strip())
    if aspect_ratio:
        seg.append("[画幅] %s（仅供理解构图，不要把比例或像素写进正文）" % aspect_ratio)
    return "\n".join(s for s in seg if s)


def _extract_body(raw: str) -> str:
    """从 LLM 原始回复里提取「一段英文正文」：兼容误输出的 JSON / Markdown 代码块 / 分节标题。"""
    t = (raw or "").strip()
    if not t:
        return ""
    # 误输出 JSON：{"rewritten_prompt": "...", ...}
    if t.startswith("{") and "rewritten_prompt" in t:
        try:
            obj = json.loads(t)
            val = obj.get("rewritten_prompt")
            if isinstance(val, str) and val.strip():
                return val.strip()
        except (ValueError, TypeError):
            pass
    # 去 Markdown 代码围栏
    fence = re.search(r"```[a-zA-Z0-9_-]*\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    # 去掉 skill 默认模式可能带的分节标题行（#### 💡 / #### 📋 / #### 🎨）与其后引导语
    lines = [ln for ln in t.splitlines() if not re.match(r"^\s*#{1,6}\s", ln)]
    t = "\n".join(lines).strip()
    # 压成单段（去多余空行）
    t = re.sub(r"\n{2,}", " ", t).strip()
    return t


async def enhance_scene_body(
    *,
    scenario: str,
    product_image_b64: str,
    specs: str = "",
    category: str = "",
    aspect_ratio: str = "",
    mime: str = "image/png",
) -> Dict[str, object]:
    """调 DeepSeek deepseek-flash（关思考 + vision）产出「镜头语言正文」。

    每任务只应调用 1 次（正文供 N 张候选共用，保证「一套图」一致性并把调用成本从 N 降到 1）。
    失败/无 key/空输出 → 抛 AdvLLMError，由调用方回退本地规则增强。
    返回：{"body": <英文正文>, "category": category, "source": "llm", "usage": {...}}。
    """
    if not settings.deepseek_api_key:
        raise AdvLLMError("未配置 DEEPSEEK_API_KEY，LLM 增强臂不可用")
    if not product_image_b64:
        raise AdvLLMError("缺少商品参考图 base64，无法做图像理解")

    system_prompt = _build_system_prompt()
    user_text = _build_user_text(scenario=scenario, specs=specs, category=category, aspect_ratio=aspect_ratio)
    detail = str(getattr(settings, "advideo_llm_vision_detail", "high") or "high")
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{product_image_b64}", "detail": detail},
                },
            ],
        },
    ]
    body: Dict[str, object] = {
        "model": str(getattr(settings, "advideo_llm_model", "deepseek-flash") or "deepseek-flash"),
        "messages": messages,
        "temperature": float(getattr(settings, "advideo_llm_temperature", 0.4)),
        "max_tokens": int(getattr(settings, "advideo_llm_max_tokens", 1200)),
        "stream": False,
    }
    if not bool(getattr(settings, "advideo_llm_thinking", False)):
        body["thinking"] = {"type": "disabled"}   # 关闭思考模式（请求体顶层，等价 SDK extra_body）

    base = str(settings.deepseek_api_base or "https://api.deepseek.com").rstrip("/")
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"}
    timeout = float(getattr(settings, "advideo_llm_timeout", 60) or 60)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AdvLLMError(f"DeepSeek 请求失败: {exc}") from exc

    try:
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    except (AttributeError, IndexError, TypeError) as exc:
        raise AdvLLMError(f"DeepSeek 响应结构异常: {exc}") from exc
    text = _extract_body(content if isinstance(content, str) else "")
    if not text:
        raise AdvLLMError("DeepSeek 输出为空或无法解析出正文")

    usage = data.get("usage") or {}
    logger.info(
        "LLM 增强 agent 产出正文 %d 字符（model=%s，thinking=%s，tokens=%s）",
        len(text), body["model"], "on" if "thinking" not in body else "off",
        usage.get("total_tokens", "?"),
    )
    return {"body": text, "category": category, "source": "llm", "usage": usage}
