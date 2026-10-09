"""视频生成「LLM 提示词增强 agent」（2026-10-09；默认 qwen3.8-flash）。

作为标准创作流程（general/drama/ecommerce/music）与 advideo 广告片的**主增强方案**：
由 flash 模型扮演 MiniMax-H3 提示词改写 agent，遵循官方 `h3-prompt-writing` skill 的输出结构
（integrated_multimodal_description / overall_soundscape / non_diegetic_music），并按场景装载对应
官方风格 skill 作为创作方向引导；广告场景（ecommerce / advideo）在最前叠加**不可被覆盖的系统级
保真约束**（纯文本改写，保留 `<Picture N>` 参考占位符）。

与 advllm.py（生图 agent）的关系：
- 复用同一 provider 凭证（settings.llm_provider / advideo_llm_api_key / advideo_llm_api_base）与
  OpenAI 兼容 POST {base}/chat/completions。
- 支持**视觉增强**：调用方可传入任务参考图（image_paths），以 base64 image_url 内容块附在 user 消息，
  让 qwen3.8-flash「看图」再改写；无图或 video_llm_vision=False 时退回纯文本改写。
- 输出**保留 H3 多段结构**（不像生图 agent 压成单段），以便下游 ComfyUI 与 Content-IR 同构消费。

铁律：
1. 失败 / 无 key / 空输出 / 解析失败 → 抛 VideoLLMError，由调用方（worker._run_enhance）回退 Content-IR；
2. 广告场景保真约束优先级高于 skill；skill 与本模块 system 冲突时以 system 为准；
3. 充分遵循官方 skill 原文，不额外堆砌本地规则（避免过度约束干扰 H3 输出）。
"""
import base64
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

# vendored 官方 H3 skill 目录（server/app/services/skills/minimax_h3，见其 SOURCE.md）
SKILL_DIR = Path(__file__).resolve().parent / "skills" / "minimax_h3"

# h3-prompt-writing：所有场景都加载（SKILL.md + 对应模式 reference）
_H3_SKILL = "h3_prompt_writing/SKILL.md"
_H3_REF_BASE = "h3_prompt_writing/references/base-en.txt"   # T2VA / I2VA / FL2VA / L2VA
_H3_REF_REF = "h3_prompt_writing/references/ref-en.txt"     # Ref2VA（全参考模式）

# 场景 → 官方风格 skill（仅作创作方向引导；general 不加载风格 skill）
_SCENE_SKILL: Dict[str, str] = {
    "ecommerce": "minimalist_product_ad_generator/SKILL.md",
    "drama": "3d_animation_short_generator/SKILL.md",
    "music": "music_video_subtitle_generator/SKILL.md",
}

_FILE_CAP = 24000       # 单文件截断上限（字符），防 token 膨胀
_SCENE_CAP = 20000      # 场景风格 skill 截断上限（比核心结构更可从简）
_TOTAL_CAP = 60000      # 拼接总上限（字符）

_SKILL_CACHE: Dict[str, str] = {}


class VideoLLMError(Exception):
    """LLM 视频增强失败（无 key / 网络 / 空输出 / 解析失败）；调用方据此回退 Content-IR。"""


# 允许作为视觉输入的参考图扩展名 → mime（其它类型如参考视频/音频不传图）
_IMG_MIME: Dict[str, str] = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
}


def _encode_images(paths: Optional[Sequence[str]], cap: int) -> List[Dict[str, str]]:
    """把参考图本地路径读成 base64（仅图片、最多 cap 张）；读取失败或非图片的跳过，不致命。"""
    out: List[Dict[str, str]] = []
    for p in paths or []:
        if len(out) >= cap:
            break
        try:
            fp = Path(p)
            mime = _IMG_MIME.get(fp.suffix.lower())
            if not mime or not fp.is_file():
                continue
            out.append({"b64": base64.b64encode(fp.read_bytes()).decode("ascii"), "mime": mime})
        except OSError:
            logger.warning("视频参考图读取失败，跳过：%s", p)
    return out


# ------------------------------------------------------------------ 系统级保真约束（广告场景）
# 置于 system 最前，声明优先级高于 skill；纯文本改写，商品参考图为唯一真源，用 <Picture N> 指代。
FIDELITY_SYSTEM_VIDEO = """# 电商商品保真增强器（视频 · 最高优先级）

你在为 MiniMax-H3 视频模型改写一条**电商广告片**提示词。画面中的商品以用户提供的参考图为准
（在 H3 提示词中用 `<Picture 1>`（及 `<Picture 2>`…）指代）。下面附带的 skill 是通用创作规范；
**当 skill 与本节冲突时，一律以本节为准**。

【最高优先级 · 商品系统级保真铁律】
1. 参考图是唯一真源：商品的形状/轮廓/材质与纹理/颜色/件数/标签排版/logo 位置与既有文字必须与参考图完全一致，
   任何一项不得增删改——不得改款、改色、加印花、加图案、加 logo、改件数、改比例。
2. 提示词里必须显式写出对参考图的指代（如 the product in `<Picture 1>`），并保持 `<Picture N>` 标签在各段一致。
3. 不要用形容词重新描述商品外观（文字重述会让模型按文字重绘商品，一致性立刻崩）；只描述**镜头语言**：
   场景、机位、景别、运镜、光线、节奏、氛围、道具与转场，商品本体与件数保持不变。
4. 画面文字：除非用户给出了**引号内确切文案**（此时只允许逐字引用，不改写/不翻译），否则画面中禁止出现任何
   文字、字幕、标语、水印或额外 logo；必须在提示词中写明 no on-screen text / no captions / no logo。
5. 广告片默认单一连续镜头、运镜平稳；不要设计多主体并置或商品复刻多份。"""


# ------------------------------------------------------------------ skill 装载
def _read(rel: str, cap: int = _FILE_CAP) -> str:
    fp = SKILL_DIR / rel
    try:
        txt = fp.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("H3 skill 文件缺失或不可读：%s（跳过）", fp)
        return ""
    if len(txt) > cap:
        txt = txt[:cap] + "\n…[truncated]"
    return f"<!-- {rel} -->\n{txt}"


def _load_skill(scene: str, is_ref_mode: bool) -> str:
    """装配 system 所需的 skill 文本：h3-prompt-writing（+ 对应模式 reference）+ 场景风格 skill。进程内缓存。"""
    key = f"{scene}|{int(is_ref_mode)}"
    if key in _SKILL_CACHE:
        return _SKILL_CACHE[key]
    parts: List[str] = []
    h3 = _read(_H3_SKILL)
    if h3:
        parts.append(h3)
    ref = _read(_H3_REF_REF if is_ref_mode else _H3_REF_BASE)
    if ref:
        parts.append(ref)
    scene_rel = _SCENE_SKILL.get(scene)
    if scene_rel:
        sc = _read(scene_rel, cap=_SCENE_CAP)
        if sc:
            parts.append(sc)
    blob = "\n\n---\n\n".join(p for p in parts if p)
    if len(blob) > _TOTAL_CAP:
        blob = blob[:_TOTAL_CAP] + "\n…[truncated]"
    _SKILL_CACHE[key] = blob
    if not blob:
        logger.warning("H3 skill 未加载到任何内容（目录 %s）；agent 仅靠 system 运行", SKILL_DIR)
    return blob


def _build_system_prompt(scene: str, is_ref_mode: bool, fidelity: bool) -> str:
    """system = [保真约束（广告场景，最高优先级）] + 身份/输出规范 + 官方 skill。system 消息不含图片。"""
    head = (
        "# MiniMax-H3 视频提示词改写 agent\n\n"
        "你是 MiniMax-H3 视频生成提示词改写专家。请把用户需求改写为**可直接用于 H3 视频生成**的提示词，"
        "严格遵循下面附带的官方 `h3-prompt-writing` skill 的**字段名、段落顺序、标签与时间标记**，"
        "并按当前创作场景参考对应风格 skill 的创作范式。\n\n"
        "【输出格式（严格遵守）】\n"
        "- 只输出 H3 最终提示词正文本身（含 integrated_multimodal_description / overall_soundscape / "
        "non_diegetic_music 三段，及 keyframe/reference 模式要求的首行对齐指令）；\n"
        "- 不要 JSON、不要 Markdown 代码围栏、不要标题前言、不要解释、不要「以下是提示词」这类话；\n"
        "- 改写正文用英文；对白、歌词、画面内可见文字保留其原始语言并逐字保留；\n"
        "- 总时长必须与用户请求的视频时长一致；`<Picture N>` / `<Video N>` / `<Audio N>` 标签在各段保持一致；\n"
        "- 优先具体的视觉与音频细节，避免 cinematic / beautiful 这类空泛词。"
    )
    seg: List[str] = []
    if fidelity:
        seg.append(FIDELITY_SYSTEM_VIDEO)
    seg.append(head)
    skill = _load_skill(scene, is_ref_mode)
    if skill:
        seg.append("# 附：MiniMax-H3 官方 skill（h3-prompt-writing + 场景风格 skill，从属规则）\n\n" + skill)
    return "\n\n---\n\n".join(seg)


def _build_user_text(*, prompt: str, gen_mode: str, ref_hint: str, duration: int, ratio: str,
                     n_images: int = 0) -> str:
    """user 文本块：生成模式 + 参考素材提示 + 时长/画幅 + 用户原始需求；附图时给出看图指引。"""
    seg: List[str] = [f"[生成模式] {gen_mode}"]
    if ref_hint:
        seg.append(f"[参考素材] {ref_hint}")
    if n_images > 0:
        seg.append(
            "[已附参考图] 共 %d 张，按顺序对应 <Picture 1>..<Picture %d>。请先「看图」再改写：\n"
            "- 严格保持参考图中商品/人物的形状、颜色、材质、件数、logo 与既有文字一致，不得改款改色、臆造细节；\n"
            "- 若某张是拼贴图/多视图（如同一人物的不同姿势、不同机位或表情参考表），把它当作**证据**而非画面主体，"
            "最终画面只合成**一个**连贯一致的主体，绝不把九宫格/多格并置搬进成片；\n"
            "- 用 <Picture N> 标签指代参考图，不要用形容词重述其外观。" % (n_images, n_images)
        )
    seg.append(f"[视频时长] {duration} 秒（改写正文的总时长必须与此一致）")
    if ratio:
        seg.append(f"[画幅] {ratio}")
    seg.append("[用户需求]\n%s" % (prompt or "").strip())
    return "\n".join(seg)


def _extract_prompt(raw: str) -> str:
    """从 LLM 原始回复里提取 H3 提示词正文：**保留多段结构**（不压成单段、不剥段落字段名）。

    仅做：整体去空白；若整段被 ```/```text 代码围栏包裹则取围栏内内容；去掉明显的开场白行。
    """
    t = (raw or "").strip()
    if not t:
        return ""
    # 误输出 JSON：{"prompt": "..."} / {"rewritten_prompt": "..."}
    if t.startswith("{"):
        try:
            obj = json.loads(t)
            for k in ("prompt", "rewritten_prompt", "integrated_multimodal_description"):
                val = obj.get(k)
                if isinstance(val, str) and val.strip():
                    return val.strip()
        except (ValueError, TypeError):
            pass
    # 整体被代码围栏包裹：取围栏内内容（保留其内部换行结构）
    fence = re.match(r"^```[a-zA-Z0-9_-]*\s*\n(.*?)\n?```$", t, re.S)
    if fence:
        t = fence.group(1).strip()
    # 去掉常见开场白首行（如 "Here is the prompt:" / "以下是提示词："），仅当其后仍有正文
    lines = t.splitlines()
    if lines and re.match(r"^\s*(here is|below is|以下是|如下|提示词[:：])", lines[0], re.I) and len(lines) > 1:
        lines = lines[1:]
        t = "\n".join(lines).strip()
    return t


async def enhance_video_prompt(
    *,
    prompt: str,
    scene: str = "general",
    gen_mode: str = "T2VA",
    ref_hint: str = "",
    duration: int = 5,
    ratio: str = "16:9",
    fidelity: Optional[bool] = None,
    image_paths: Optional[Sequence[str]] = None,
) -> Dict[str, object]:
    """调 qwen3.8-flash 视频增强 agent，产出 H3 结构化提示词正文。

    - scene：general / drama / ecommerce / music（决定装载哪个官方风格 skill）；
    - gen_mode：T2VA / I2VA / FL2VA / L2VA / Ref2VA（决定加载 base-en 还是 ref-en 结构指南）；
    - fidelity：是否注入系统级商品保真约束；None=按场景自动判定（ecommerce 时为 True）；
    - image_paths：可选参考图本地路径（首/尾帧、参考区图片、advideo 商品/确认图）；非空且
      video_llm_vision=True 时以 base64 image_url 附图做视觉增强（仅图片、最多 video_llm_max_images 张）；
    - 失败 / 无 key / 空输出 → 抛 VideoLLMError，由调用方回退 Content-IR。
    返回：{"text": <H3 提示词>, "source": "llm", "scene": scene, "gen_mode": gen_mode, "fidelity": ..., "images": N, "usage": {...}}。
    """
    provider = settings.llm_provider                      # qwen | deepseek
    api_key = settings.advideo_llm_api_key                # 当前 provider 的 key（与生图 agent 共用）
    if not api_key:
        raise VideoLLMError(f"未配置 {provider} API Key，视频 LLM 增强臂不可用")
    if not (prompt or "").strip():
        raise VideoLLMError("用户提示词为空，无法增强")

    scene = (scene or "general").strip().lower()
    if scene not in ("general", "drama", "ecommerce", "music"):
        scene = "general"
    gen_mode = (gen_mode or "T2VA").strip().upper()
    is_ref_mode = gen_mode == "REF2VA"
    if fidelity is None:
        fidelity = scene == "ecommerce"

    system_prompt = _build_system_prompt(scene, is_ref_mode, fidelity)
    # 视觉增强：参考图读成 base64（仅 video_llm_vision 开启且有图时）
    imgs: List[Dict[str, str]] = []
    if image_paths and bool(getattr(settings, "video_llm_vision", True)):
        cap = int(getattr(settings, "video_llm_max_images", 4) or 4)
        imgs = _encode_images(image_paths, cap)
    user_text = _build_user_text(
        prompt=prompt, gen_mode=gen_mode, ref_hint=ref_hint, duration=duration, ratio=ratio,
        n_images=len(imgs),
    )
    if imgs:
        detail = str(getattr(settings, "advideo_llm_vision_detail", "high") or "high")
        content: List[dict] = [{"type": "text", "text": user_text}]
        for im in imgs:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{im['mime']};base64,{im['b64']}", "detail": detail},
            })
        user_msg: Dict[str, object] = {"role": "user", "content": content}
    else:
        user_msg = {"role": "user", "content": user_text}
    messages = [
        {"role": "system", "content": system_prompt},
        user_msg,
    ]
    model = str(settings.advideo_llm_model or ("qwen3.8-flash" if provider == "qwen" else "deepseek-flash"))
    body: Dict[str, object] = {
        "model": model,
        "messages": messages,
        "temperature": float(getattr(settings, "video_llm_temperature", 0.5)),
        "max_tokens": int(getattr(settings, "video_llm_max_tokens", 2400)),
        "stream": False,
    }
    # 关思考：qwen/千问 用 enable_thinking=false；deepseek 用 thinking.type=disabled（均请求体顶层）
    thinking_on = bool(getattr(settings, "video_llm_thinking", False))
    if provider == "qwen":
        body["enable_thinking"] = thinking_on
    elif not thinking_on:
        body["thinking"] = {"type": "disabled"}

    base = str(settings.advideo_llm_api_base or "").rstrip("/")
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout = float(getattr(settings, "video_llm_timeout", 90) or 90)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise VideoLLMError(f"视频 LLM({provider}) 请求失败: {exc}") from exc

    try:
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    except (AttributeError, IndexError, TypeError) as exc:
        raise VideoLLMError(f"视频 LLM({provider}) 响应结构异常: {exc}") from exc
    text = _extract_prompt(content if isinstance(content, str) else "")
    if not text:
        raise VideoLLMError(f"视频 LLM({provider}) 输出为空或无法解析出正文")

    usage = data.get("usage") or {}
    logger.info(
        "视频 LLM 增强 agent 产出 %d 字符（provider=%s，model=%s，scene=%s，mode=%s，fidelity=%s，images=%d，tokens=%s）",
        len(text), provider, model, scene, gen_mode, fidelity, len(imgs), usage.get("total_tokens", "?"),
    )
    return {
        "text": text, "source": "llm", "scene": scene,
        "gen_mode": gen_mode, "fidelity": fidelity, "images": len(imgs), "usage": usage,
    }
