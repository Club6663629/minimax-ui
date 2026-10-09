"""电商广告片「用户提示词增强层」（P4，2026-10-08）。

背景（用户口径 10-08）：用户通常不是提示词专家，输入常是一句大白话 + 若干【场景】【光线】【情绪氛围】【画质】
标签。本模块把这类输入解析成**结构化字段**、补齐缺失字段的默认值、拼成模型可直接执行的提示词，
并把「商品一致性」做成**不可被用户文本覆盖的硬前缀**。

铁律（与 advprompt.py 同一套）：
1. 商品一致性 > 用户创作意图：商品永远用「指图」措辞（生图 <image1> / 生视频 Picture 1..N），
   增强层**永不自行用形容词重述商品**；用户给了结构清单则逐字沿用；
2. 用户若提出与保真冲突的要求（换款 / 改色 / 加印花 / 改版型 / 画面加文字水印），
   一律从提示词里剔除并记录到 conflicts（不静默照做，也不静默丢弃）；
3. **0 付费**：纯规则实现（无 LLM / 无云端 API），确定性、可单测。
"""

import logging
import re
from typing import Dict, List, NamedTuple, Tuple

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 标签词表
TAG_MAP = {
    "场景": "scene", "背景": "scene", "环境": "scene", "地点": "scene",
    "光线": "light", "灯光": "light", "光影": "light", "照明": "light",
    "情绪氛围": "mood", "氛围": "mood", "情绪": "mood", "风格": "style", "调性": "mood",
    "画质": "quality", "画质风格": "quality", "质感": "quality", "画面": "quality",
    "机位": "camera", "镜头": "camera", "运镜": "camera",
    "动作": "action", "姿势": "action", "姿态": "action",
    "音乐": "audio", "音频": "audio", "bgm": "audio", "BGM": "audio", "配乐": "audio",
    "时长": "duration", "人物": "subject", "模特": "subject", "主体": "subject",
}

_TAG_RE = re.compile(r"[【\[]([^】\]]{1,16})[】\]]")

# ------------------------------------------------------------------ 默认值（缺字段才补；来自 2026-10-07 手动成功配方）
DEFAULT_SUBJECT = (
    "an American white female fashion model, 23 years old, 178 cm tall, slim model figure, brown hair"
)
DEFAULT_SCENE = (
    "a European city street in front of a dark wood-framed glass boutique window, grey square-tile sidewalk, "
    "plants, mannequin silhouettes and soft light reflections inside the window"
)
DEFAULT_LIGHT = "overcast soft light, slightly cool tone, occasional boutique-window reflection on her hair"
DEFAULT_MOOD = "confident, bold, bestie street-snap, summer dopamine OOTD"
DEFAULT_QUALITY = (
    "cinematic, high resolution, shallow depth of field with gently blurred background, cool film-toned grading"
)

# ------------------------------------------------------------------ 一致性冲突拦截
# (正则, 人类可读原因)：命中即从该字段里剔除该句，并记入 conflicts
CONFLICT_RULES: List[Tuple[str, str]] = [
    (r"(换成|改换成|替换成|改穿|换一件|另换一件|把[^。；;，,]{0,8}换成)", "要求更换商品/款式"),
    (r"(染成|颜色?(改|换)成|换个(颜色|色)|改个色)", "要求改商品颜色"),
    (r"((加|添加|印|绣|贴)(上|个|一个)?(印花|图案|刺绣|logo|标志|logo图案))", "要求给商品加图案/logo"),
    (r"(改成?(短袖|长袖|无袖|吊带|长裙|短裙)|去掉(袖子|下摆|领子)|加(上)?(袖子|领子)|裁短|剪短|改长)", "要求改商品版型结构"),
    (r"((写|加|打|印)(上|着)?(文字|字幕|文案|标语|水印|logo)|画面[^。；;]{0,6}文字)", "要求画面出现文字/水印"),
    (r"(画面|屏幕|背景|图上)[^。；;]{0,6}(写|加|打|印|放)(上|着)?[^。；;]{0,10}(字|文字|文案|标语|水印|logo|字母|大字)", "要求画面出现文字/水印"),
    (r"((写|印|打)上[^。；;]{0,8}(字|大字|文案|标语|水印))", "要求画面出现文字/水印"),
]


def _split_sentences(text: str) -> List[str]:
    return [s for s in re.split(r"(?<=[。；;！!？?\n])", text or "") if s.strip()]


def _strip_conflicts(text: str, field: str) -> Tuple[str, List[Dict[str, str]]]:
    """把与商品保真冲突的句子从字段里剔除，返回 (干净文本, conflicts)。"""
    if not text:
        return "", []
    kept, bad = [], []
    for sent in _split_sentences(text):
        hit = None
        for pat, why in CONFLICT_RULES:
            if re.search(pat, sent):
                hit = why
                break
        if hit:
            bad.append({"field": field, "dropped": sent.strip(), "reason": hit})
        else:
            kept.append(sent)
    return "".join(kept).strip(" 。；;，,、\n"), bad


# ------------------------------------------------------------------ 解析
def parse_user_prompt(raw: str) -> Dict[str, str]:
    """把用户输入解析成字段：subject / scene / light / mood / style / quality / camera / action / audio / extra。

    形如「<主体句>【场景】… 【光线】… 【情绪氛围】… 【画质】…」；无【】标签时整段当作 subject（自由描述）。
    """
    text = (raw or "").replace("\r\n", "\n").strip()
    fields: Dict[str, str] = {}
    if not text:
        return fields
    matches = list(_TAG_RE.finditer(text))
    extras: List[str] = []
    if not matches:
        fields["subject"] = text
        return fields
    head = text[: matches[0].start()].strip()
    if head:
        fields["subject"] = head
    for i, m in enumerate(matches):
        label = m.group(1).strip().rstrip("：:")
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        val = text[m.end(): end].strip(" 。，,；;\n")
        key = TAG_MAP.get(label)
        if key is None:
            if val:
                extras.append("%s：%s" % (label, val))
            continue
        if fields.get(key):
            fields[key] = (fields[key] + " " + val).strip()
        else:
            fields[key] = val
    if extras:
        fields["extra"] = " / ".join(extras)
    return fields


def _clean_fields(fields: Dict[str, str]) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    out: Dict[str, str] = {}
    conflicts: List[Dict[str, str]] = []
    for k, v in fields.items():
        clean, bad = _strip_conflicts(v, k)
        if clean:
            out[k] = clean
        conflicts.extend(bad)
    return out, conflicts


# ------------------------------------------------------------------ 生图（图像编辑）增强
ENH_GARMENT_LOCK = (
    "Use the exact garment from {tag} and keep it identical: same silhouette, colour, material, sheen, "
    "construction, neckline, hem layers, trims, pattern, hardware, length and proportions; "
    "realistic fabric drape and natural folds. Do not redesign, recolour, restyle, lengthen, "
    "shorten or crop it, never swap it for another garment, and do not add any print, logo or lettering that "
    "is not present in {tag}. "
)
ENH_PERSON_LOCK = (
    "Keep the SAME person as {tag}: identical face, hairstyle, hair colour, makeup, skin tone and body "
    "proportions. "
)
ENH_PERSON_RECAST = (
    "Cast a model and lock her identity (face, hairstyle, hair colour, skin tone, "
    "body proportions): {subject}, dressed in the garment from {tag}. "
)
ENH_TAIL = (
    " Keep the garment's exact colour, print and material identical to {tag}. "
    "No text, no lettering, no logo, no watermark anywhere in the image."
)


# 批量出图（用户口径 A）：N 张候选共用同一段提示词、同一 seed、同一组参考图。
# 【缺陷修复 20261009】原「多角度」措辞（…shots span varied angles…of the same subject）被 qwen21 误读成
# 「一张图里要展示多个机位」，导致单帧内画出同一人物的 2~3 个并列副本（ad60030_1/2.png 三联人）。
# 故改为「单帧完整性」硬约束：只渲染一个主体、一个机位、一张全画幅照片，并显式禁止复制/并列/拼贴；
# 同时要求主体完整入画、头顶与边缘留余量（修复 0 号头顶被裁）。组内一致仍由「同提示词+同 seed+批量」保证，
# 张与张之间的细微差别交给批量噪声自然分化，不再用文字索要「多角度」。
ENH_SINGLE_FRAME = (
    " Render exactly ONE single full-frame photograph of ONE single subject: one person only (or one product only), "
    "captured from one camera angle at one moment, filling the whole canvas. Never duplicate, clone, mirror or repeat "
    "the subject — no two or three copies of the same person standing side by side, no collage, contact sheet, grid, "
    "split-screen, diptych, triptych or any multi-panel / multi-view layout. Keep the whole subject fully inside the "
    "frame with a little breathing room above the head and at the sides; do not crop the head, face or feet, and do "
    "not cut the product off."
)


def _scene_block(*, scene: str, light: str, mood: str, style: str, extra: str, quality: str, tag: str,
                 kept_defaults: List[str]) -> str:
    parts: List[str] = []
    if scene:
        parts.append("Scene: %s." % scene)
    else:
        parts.append("Keep the same location, background and set as %s." % tag)
        kept_defaults.append("scene=沿用输入图环境")
    if light:
        parts.append("Lighting: %s." % light)
    if mood:
        parts.append("Mood / art direction: %s." % mood)
    if style:
        parts.append("Style: %s." % style)
    if extra:
        parts.append("Additional art direction: %s." % extra)
    if quality:
        parts.append("Quality: %s." % quality)
    return " ".join(parts)


def _enhance_image_garment(raw: str, *, specs: str = "", variant_index: int = 0, total: int = 3,
                           product_tag: str = "<image1>", camera_variants=None,
                           defaults: bool = True) -> Dict[str, object]:
    """生图阶段增强：用户短句/标签 → 「保真硬前缀 + 结构化字段 + 机位变体 + 收尾条款」。

    返回 dict：text（最终提示词）/ fields（解析后字段）/ defaults_used / conflicts / raw。
    同一任务的所有机位共用同一套字段文本（保证「一套图」），只有机位句变化。
    """
    from app.services.advprompt import RECIPE_SET_KEEP_CAMERA  # 迟到导入，避免循环依赖
    shots = list(camera_variants or RECIPE_SET_KEEP_CAMERA)
    fields = parse_user_prompt(raw)
    fields, conflicts = _clean_fields(fields)
    defaults_used: List[str] = []

    subject = fields.get("subject", "").strip()
    if not subject and defaults:
        subject = DEFAULT_SUBJECT
        defaults_used.append("subject")
    scene = fields.get("scene", "").strip()
    if not scene and defaults:
        scene = DEFAULT_SCENE
        defaults_used.append("scene")
    light = fields.get("light", "").strip()
    if not light and defaults:
        light = DEFAULT_LIGHT
        defaults_used.append("light")
    mood = fields.get("mood", "").strip()
    if not mood and defaults:
        mood = DEFAULT_MOOD
        defaults_used.append("mood")
    quality = fields.get("quality", "").strip()
    if not quality and defaults:
        quality = DEFAULT_QUALITY
        defaults_used.append("quality")

    # 保真硬前缀（不可被用户文本覆盖）：商品锁死；人物 = 用户给了主体就「重选并全程锁定同一人」，否则锁定输入图人物
    if fields.get("subject"):
        person = ENH_PERSON_RECAST.format(subject=subject, tag=product_tag)
    else:
        person = ENH_PERSON_LOCK.format(tag=product_tag)
    head = ENH_GARMENT_LOCK.format(tag=product_tag) + person

    scene_block = _scene_block(scene=scene, light=light, mood=mood, style=fields.get("style", ""),
                               extra=fields.get("extra", ""), quality=quality, tag=product_tag,
                               kept_defaults=defaults_used)
    shot = shots[int(variant_index) % len(shots)]
    text = head + scene_block + " " + shot + ENH_TAIL.format(tag=product_tag)
    if specs:
        text = head + "The garment is %s. " % specs.strip() + text[len(head):]

    return {
        "text": text,
        "raw": raw,
        "fields": fields,
        "defaults_used": defaults_used,
        "conflicts": conflicts,
        "stage": "image",
        "variant_index": int(variant_index),
        "product_tag": product_tag,
    }


# ------------------------------------------------------------------ 品类识别（P0 护栏，0 付费纯规则）
# 8 类：服装/鞋包/饮品/食品/美妆/3C/家居/其他 + 未识别
CATEGORY_UNKNOWN = "未识别"
CATEGORY_OTHER = "其他"

# 信号①：用户文本关键词（命中数最多者胜，平局按 _CATEGORY_PRIORITY 顺序）
CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "服装": ["裙", "衬衫", "外套", "雪纺", "穿搭", "上衣", "裤", "卫衣", "毛衣", "大衣", "西装",
             "吊带", "无袖", "短袖", "长袖", "领口", "下摆", "版型", "面料", "衣服", "服装",
             "打底", "风衣", "羽绒服", "马甲", "背心", "t恤", "连衣裙", "半身裙"],
    "鞋包": ["运动鞋", "高跟鞋", "凉鞋", "拖鞋", "靴子", "鞋", "背包", "手袋", "钱包", "挎包",
             "手提包", "单肩包", "双肩包", "箱包", "女包"],
    "饮品": ["饮料", "饮品", "果汁", "汽水", "可乐", "奶茶", "咖啡", "矿泉水", "纯净水", "苏打水",
             "气泡水", "瓶装", "罐装", "配料表", "不加一滴水", "不加糖", "0糖", "茶饮", "乳酸菌",
             "酸奶", "瓶身"],
    "食品": ["零食", "饼干", "巧克力", "糖果", "薯片", "坚果", "方便面", "泡面", "面包", "蛋糕",
             "糕点", "罐头", "调料", "蜂蜜", "麦片", "果干"],
    "美妆": ["口红", "唇膏", "粉底", "眼影", "腮红", "面膜", "精华", "面霜", "护肤", "化妆",
             "美妆", "香水", "指甲油", "洗面奶", "卸妆", "防晒", "乳液", "眼霜", "气垫"],
    "3C": ["手机", "电脑", "笔记本", "平板", "耳机", "音箱", "音响", "相机", "键盘", "鼠标",
           "显示器", "充电器", "数据线", "路由器", "智能", "数码", "显卡", "充电宝", "手表"],
    "家居": ["家居", "家具", "沙发", "椅子", "桌子", "枕头", "被子", "窗帘", "地毯", "灯具",
             "台灯", "水杯", "餐具", "收纳", "抱枕", "花瓶", "香薰", "床垫", "茶几"],
}
_CATEGORY_PRIORITY = ["服装", "鞋包", "饮品", "食品", "美妆", "3C", "家居"]


def detect_category(raw: str, *, specs: str = "", product_names=()) -> str:
    """纯规则品类识别（无网络、无模型）：先看用户文本（信号①），再看商品图文件名（信号②）。

    返回 服装/鞋包/饮品/食品/美妆/3C/家居/其他/未识别。文本与文件名都没命中时，
    若用户明显在描述某个商品（含 商品/产品/货品/这款/这个）记为「其他」，否则记「未识别」。
    """
    text = ("%s %s" % (raw or "", specs or "")).lower()
    blobs = [text, " ".join(str(n).lower() for n in (product_names or ()))]
    for blob in blobs:
        if not blob.strip():
            continue
        score = {cat: sum(blob.count(k.lower()) for k in kws) for cat, kws in CATEGORY_KEYWORDS.items()}
        best = max(score.values()) if score else 0
        if best > 0:
            for cat in _CATEGORY_PRIORITY:
                if score.get(cat, 0) == best:
                    return cat
    if any(k in text for k in ("商品", "产品", "货品", "这款", "这个")):
        return CATEGORY_OTHER
    return CATEGORY_UNKNOWN


_SELLING_HINTS = ("不加", "无添加", "只有", "配料", "成分", "0添加", "0糖", "天然", "新鲜", "纯")


def split_intent(raw: str) -> Tuple[str, List[str]]:
    """把用户自由文本拆成 (主体描述, 卖点列表)，不把整句塞进 subject（P0 工单 §5）。

    卖点 = 含卖点提示词的短句；其余合并为主体，再去掉「为…做一个广告图」这类句式外壳。
    """
    text = raw or ""
    m = _TAG_RE.search(text)
    if m:
        text = text[:m.start()]
    parts = re.split(r"[。；;，,！!？?\n]+", text)
    subj, sell = [], []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        (sell if any(h in p for h in _SELLING_HINTS) else subj).append(p)
    subject = "，".join(subj)
    subject = re.sub(r"^(为|给|帮|把|将)", "", subject).strip()
    subject = re.sub(r"(做|制作|拍|出|生成|设计).{0,8}(广告图|广告片|广告|海报|图片|主图|宣传图)?$", "", subject).strip()
    subject = re.sub(r"(广告图|广告片|广告|海报|主图|宣传图|图片)$", "", subject).strip("，, ")
    return subject, sell


def _concept_line(subject: str, selling: List[str]) -> str:
    """把主体 + 卖点写成一条「概念/卖点」艺术指令（不着色商品本身、不要求渲染文字覆盖层）。"""
    bits = []
    if subject:
        bits.append(subject)
    bits.extend(selling)
    if not bits:
        return ""
    concept = "；".join(bits)
    return ("Show the product concept below through composition, styling, props and lighting "
            "(convey the idea visually and do not render these words as an overlay): %s. " % concept)




# 中性保真锁（非服装品类；禁用 garment/neckline/hem/model/dressed in/wearing/she/her/one model）
ENH_TAIL_NEUTRAL = (
    " Keep the product's exact colour, label text layout and material identical to {tag}. "
    "No text, no lettering, no logo, no watermark anywhere in the image that is not already present in {tag}."
)
NEUTRAL_LOCK_DRINK = (
    "Use the exact product from {tag} and keep it identical: the same bottle shape and silhouette, the same "
    "label position and text layout, the same liquid colour and liquid level, the same cap shape and colour, "
    "and the same logo position and shape as {tag}. Do not change the "
    "bottle shape or volume; do not redesign, recolour or restyle the product. "
)
NEUTRAL_LOCK_GENERIC = (
    "Use the exact product from {tag} and keep it identical: same shape, silhouette, colour, material, "
    "surface finish, proportions, label and pattern placement, logo position and shape as {tag}. "
    "Do not redesign, recolour, restyle, resize or replace it, and do not add any pattern, "
    "logo or lettering that is not present in {tag}. "
)
CATEGORY_LOCK: Dict[str, str] = {
    "饮品": NEUTRAL_LOCK_DRINK,
    "食品": NEUTRAL_LOCK_GENERIC,
    "鞋包": NEUTRAL_LOCK_GENERIC,
    "美妆": NEUTRAL_LOCK_GENERIC,
    "3C": NEUTRAL_LOCK_GENERIC,
    "家居": NEUTRAL_LOCK_GENERIC,
    "其他": NEUTRAL_LOCK_GENERIC,
}

# 品类适配默认值（缺字段才补；服装走原 DEFAULT_*）
CATEGORY_SCENE: Dict[str, str] = {
    "饮品": "a clean studio tabletop with a soft neutral gradient background, subtle water droplets on the "
            "bottle and a few fresh fruit props beside it",
}
CATEGORY_LIGHT: Dict[str, str] = {
    "饮品": "crisp premium product advertising light, soft specular reflections on the bottle, gentle rim light",
}
CATEGORY_MOOD: Dict[str, str] = {
    "饮品": "fresh, pure, premium beverage advertising",
}
CATEGORY_QUALITY: Dict[str, str] = {
    "饮品": "cinematic, high resolution, shallow depth of field with a clean blurred background, clean "
            "commercial product-photography grading",
}
CATEGORY_SCENE_DEFAULT = "a clean studio tabletop with a soft neutral gradient background"
CATEGORY_LIGHT_DEFAULT = "clean soft product lighting with gentle reflections and a subtle rim light"
CATEGORY_MOOD_DEFAULT = "premium product advertising, honest and appetising"
CATEGORY_QUALITY_DEFAULT = "high resolution, sharp focus, clean commercial product photography grading"

# 非服装品类机位（产品特写，无模特/无全身站位）
NEUTRAL_CAMERA_VARIANTS = [
    "Camera: centered hero product shot, straight-on front view, the product fully in frame with generous clean empty space around it.",
    "Camera: three-quarter angled product shot from a slightly high camera angle, the label clearly readable.",
    "Camera: close-up product shot on the label and cap, shallow depth of field.",
    "Camera: slightly low hero camera angle, the product standing upright with a soft reflection beneath it.",
    "Camera: top-down flat-lay product shot on the tabletop with a few simple props around it.",
]


# ------------------------------------------------------------------ 生图品类样式注册表（B2）
# category → 生图提示词的样式（保真锁 / 收尾条款，均含 {tag} 占位）+ 机位库标识。
# 调用方（advimage._llm_variant_prompt）只按 category 查表拼装，不再写 if category == "服装" 之类分支；
# 新增品类（饮料/服装/音响…）只需在此登记一行，锁文本复用已有常量，调用方零改动。
class ImageStyle(NamedTuple):
    lock: str       # 保真锁模板（含 {tag}）
    tail: str       # 收尾条款模板（含 {tag}）
    cameras: str    # 机位库标识："garment"=模特上身（advprompt）| "neutral"=产品特写


# 服装走模特上身机位；其余品类（饮品/食品/鞋包/美妆/3C/家居/其他/未识别）走中性产品特写机位。
# 锁文本全部复用上方常量（NEUTRAL_LOCK_DRINK / NEUTRAL_LOCK_GENERIC 等），不重复定义字符串。
IMAGE_STYLE: Dict[str, ImageStyle] = {
    "服装": ImageStyle(ENH_GARMENT_LOCK, ENH_TAIL, "garment"),
    "饮品": ImageStyle(NEUTRAL_LOCK_DRINK, ENH_TAIL_NEUTRAL, "neutral"),
}
# 未登记品类的回退样式：通用中性产品锁（等价旧 CATEGORY_LOCK.get(category, NEUTRAL_LOCK_GENERIC)）。
IMAGE_STYLE_FALLBACK = ImageStyle(NEUTRAL_LOCK_GENERIC, ENH_TAIL_NEUTRAL, "neutral")


def image_lock_parts(category: str, tag: str) -> Tuple[str, List[str], str]:
    """按品类查注册表，返回生图提示词的 (保真硬前缀, 机位变体列表, 收尾条款)。

    B2：集中品类逻辑，去 advimage 里的 if category == "…" 硬编码。模板中的 {tag} 用商品指代符
    （如 <image1>）填充。
    机位库按 style.cameras 取用：garment 迟到导入 advprompt（避免循环依赖），neutral 用本模块常量。
    """
    style = IMAGE_STYLE.get(category, IMAGE_STYLE_FALLBACK)
    head = style.lock.format(tag=tag)
    tail = style.tail.format(tag=tag)
    if style.cameras == "garment":
        from app.services.advprompt import RECIPE_SET_KEEP_CAMERA  # 迟到导入，避免循环依赖
        cameras = list(RECIPE_SET_KEEP_CAMERA)
    else:
        cameras = list(NEUTRAL_CAMERA_VARIANTS)
    return head, cameras, tail


def _enhance_image_neutral(raw, *, category, specs, variant_index, total, product_tag, shots, defaults):
    """非服装品类的生图增强：中性保真锁（不出现服装/模特措辞）+ 品种适配默认值。

    未识别品类退化为「纯保真」：只锁商品，不编人物、不编场景（宁可不写也不写错）。
    """
    fields = parse_user_prompt(raw)
    fields, conflicts = _clean_fields(fields)
    defaults_used: List[str] = []
    subject, selling = split_intent(raw)
    concept = _concept_line(subject, selling)
    scene = fields.get("scene", "").strip()
    light = fields.get("light", "").strip()
    mood = fields.get("mood", "").strip()
    quality = fields.get("quality", "").strip()
    style = fields.get("style", "").strip()
    extra_f = fields.get("extra", "").strip()

    if category == CATEGORY_UNKNOWN:
        head = NEUTRAL_LOCK_GENERIC.format(tag=product_tag)
        scene_block = ""
        if scene or light or mood or quality or style or extra_f:
            scene_block = _scene_block(scene=scene, light=light, mood=mood, style=style, extra=extra_f,
                                       quality=quality, tag=product_tag, kept_defaults=defaults_used)
    else:
        head = CATEGORY_LOCK.get(category, NEUTRAL_LOCK_GENERIC).format(tag=product_tag)
        if not scene and defaults:
            scene = CATEGORY_SCENE.get(category, CATEGORY_SCENE_DEFAULT)
            defaults_used.append("scene")
        if not light and defaults:
            light = CATEGORY_LIGHT.get(category, CATEGORY_LIGHT_DEFAULT)
            defaults_used.append("light")
        if not mood and defaults:
            mood = CATEGORY_MOOD.get(category, CATEGORY_MOOD_DEFAULT)
            defaults_used.append("mood")
        if not quality and defaults:
            quality = CATEGORY_QUALITY.get(category, CATEGORY_QUALITY_DEFAULT)
            defaults_used.append("quality")
        scene_block = _scene_block(scene=scene, light=light, mood=mood, style=style, extra=extra_f,
                                   quality=quality, tag=product_tag, kept_defaults=defaults_used)

    shot = shots[int(variant_index) % len(shots)]
    mid = (concept + " " if concept else "") + (scene_block + " " if scene_block else "")
    text = head + mid + shot + ENH_TAIL_NEUTRAL.format(tag=product_tag)
    if specs:
        text = head + "The product is %s. " % specs.strip() + text[len(head):]
    return {
        "text": text,
        "raw": raw,
        "fields": fields,
        "defaults_used": defaults_used,
        "conflicts": conflicts,
        "stage": "image",
        "variant_index": int(variant_index),
        "product_tag": product_tag,
        "category": category,
    }


def enhance_image_prompt(raw: str, *, specs: str = "", variant_index: int = 0, total: int = 3,
                         product_tag: str = "<image1>", camera_variants=None,
                         defaults: bool = True, category=None) -> Dict[str, object]:
    """生图阶段增强统一入口：先做品类识别，再分派。

    - 服装：走原「服装保真」路径（行为与改前逐字一致，服装链路不许回归）；
    - 非服装（饮品/食品/鞋包/美妆/3C/家居/其他）：中性保真锁 + 品类适配默认值；
    - 未识别：退化纯保真（只锁商品，不编人物场景）。
    category 传 None 时自动识别（0 网络、0 付费纯规则）。返回值新增 "category"。
    """
    if category is None:
        category = detect_category(raw, specs=specs)
    logger.info("广告片生图增强层 品类识别 category=%s（raw=%.40s）", category, (raw or "").strip())
    if category == "服装":
        res = _enhance_image_garment(raw, specs=specs, variant_index=variant_index, total=total,
                                     product_tag=product_tag, camera_variants=camera_variants, defaults=defaults)
        res["category"] = category
        return res
    shots = list(camera_variants) if camera_variants else list(NEUTRAL_CAMERA_VARIANTS)
    return _enhance_image_neutral(raw, category=category, specs=specs, variant_index=variant_index,
                                  total=total, product_tag=product_tag, shots=shots, defaults=defaults)


# ------------------------------------------------------------------ 生视频增强
VIDEO_SUPPORT = [
    ("fidelity",
     "商品一致性：画面中的商品必须与参考图（Picture 1）完全同一件商品——颜色、材质、版型、结构、印花、五金、"
     "件数都不得改变，也不得改款、改色、加图案或加 logo；商品全程完整清晰入画，不被遮挡、不被裁切、不出画。"),
    ("camera",
     "镜头：稳定缓慢的运镜（缓慢推近或轻微横移），无抖动、无甩镜、无跳跃剪辑，场景不切换，画面里不出现新的物体或第二个人。"),
    ("person",
     "人物：全片同一位模特，脸型、发型、发色、肤色、身材比例与参考图保持一致。"),
    ("audio",
     "声音：纯器乐 BGM（稳定节奏、无口播、无歌词），节奏与画面动作卡点。"),
    ("notext",
     "画面文字：画面内不出现任何文字、字母、字幕、水印、logo 或标语。"),
]
DEFAULT_ACTION = (
    "模特自然展示商品：先正面站定看向镜头，再缓慢侧身展示轮廓，最后轻微靠近镜头定格；"
    "动作幅度小而流畅，手部不遮挡商品关键结构。"
)
_MOTION_HINT = ("走", "转身", "展示", "摆动", "推近", "拉远", "移动", "回头", "抬手", "走两步", "walk", "turn", "move")


def enhance_video_prompt(raw: str, *, n_refs: int = 1, duration: int = 10,
                         image_prompt: str = "", defaults: bool = True) -> Dict[str, object]:
    """生视频阶段增强：用户短句 → 保真「支撑条款」+ 用户原话（保持创作意图）+ 缺失槽位补默认。

    正文只用**用户原话**（中文可），增强层只做三件事：① 剔除与保真冲突的句子；② 用户没写动作时补默认动作；
    ③ 追加固定支撑条款（商品一致 / 镜头稳定 / 同一人物 / 无文字），最后交 wrap_video_prompt 拼 [FIDELITY]/[SET]。
    """
    fields = parse_user_prompt(raw)
    fields, conflicts = _clean_fields(fields)
    defaults_used: List[str] = []

    body = (raw or "").strip()
    # 用户写了【】标签时：把字段重新拼成一句可执行正文（保留原话）
    if fields and _TAG_RE.search(raw or ""):
        seg = []
        if fields.get("subject"):
            seg.append(fields["subject"])
        if fields.get("action"):
            seg.append(fields["action"])
        for k, label in (("scene", "场景"), ("light", "光线"), ("mood", "氛围"), ("style", "风格"),
                         ("quality", "画质"), ("camera", "运镜"), ("audio", "音乐"), ("extra", "补充")):
            if fields.get(k):
                seg.append("%s：%s" % (label, fields[k]))
        if seg:
            body = "；".join(seg)
    # 剔除冲突后，若正文里仍有被剔的句子，重算
    clean_body, bad = _strip_conflicts(body, "video")
    conflicts.extend(bad)
    body = clean_body

    has_motion = any(h in body for h in _MOTION_HINT)
    if not has_motion and defaults:
        body = (body + " " + DEFAULT_ACTION).strip() if body else DEFAULT_ACTION
        defaults_used.append("action")
    if not body and defaults:
        body = DEFAULT_ACTION
        defaults_used.append("action")

    clauses = []
    for key, txt in VIDEO_SUPPORT:
        if key == "fidelity" and "商品一致性" in body:
            continue
        if key == "notext" and ("无文字" in body or "没有任何文字" in body):
            continue
        if key == "audio" and duration:
            txt = txt + "全片时长 %d 秒。" % int(duration)
        clauses.append(txt)
    text = body + "\n\n" + "\n".join(clauses)
    return {
        "text": text,
        "raw": raw,
        "fields": fields,
        "defaults_used": defaults_used,
        "conflicts": conflicts,
        "stage": "video",
        "n_refs": int(n_refs),
        "duration": int(duration),
    }


# ------------------------------------------------------------------ 守门（B2 同类）
def enhance_ok(text: str, *, stage: str = "image", product_tag: str = "") -> bool:
    """增强结果必须：① 指代商品/参考图；② 含「无文字」否定式；③ 未出现「改款/改色」类残留。"""
    t = (text or "").lower()
    if not t.strip():
        return False
    if stage == "image":
        has_ref = ("<image1>" in t) or ("<image" in t)
    else:
        has_ref = any(k in t for k in ("picture 1", "reference picture", "reference image", "参考图"))
    has_notext = any(k in t for k in ("no text", "no on-screen text", "no watermark", "无任何文字",
                                      "没有任何文字", "不得出现任何文字", "无文字", "不出现任何文字"))
    if not (has_ref and has_notext):
        return False
    return True


def enhance_user_prompt(raw: str, stage: str = "image", **kw) -> Dict[str, object]:
    """统一入口：stage=image|video。"""
    if stage == "video":
        return enhance_video_prompt(raw, **kw)
    return enhance_image_prompt(raw, **kw)
