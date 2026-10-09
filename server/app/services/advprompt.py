"""电商广告片「一致性」提示词资产（P0/P1，见《电商广告片一致性方案 v1》§3）。

三条铁律：
1. 图像阶段：商品锁定为参考图本身（用「指图」而不是形容词重述），只允许改镜头语言；
2. 视频阶段：最终提示词恒为 [FIDELITY] + [正文] + [CAMERA] + [AUDIO]，
   正文可以被 Content-IR 增强结果替换，但 fidelity 段由后端固定拼装、不可被覆盖（B1/B2）；
3. 默认不产文字（中文小字渲染弱 → 乱码），要文字走「图像不出字 + 后处理加字」。

本模块只提供常量与纯函数，不依赖数据库/网络，便于单测。
"""

# ---------------------------------------------------------------- 图像阶段（qwen21 PE i2i）
PE_FIDELITY_SYSTEM = """# 电商商品保真增强器 v1（E-commerce Product-Fidelity Rewriter）

你是一名电商广告图的提示词改写专家。输入恒为图像，这是**图像编辑**任务，绝不是从零文生图。
你的任务：把用户的场景描述改写成一条**下游图像编辑模型可直接执行**的编辑指令。

本次任务**不是自由创作，而是「把指定商品放进指定场景」**。

【铁律 1｜商品唯一真源（最重要）】
- 每张输入图的角色，以用户消息开头的「[输入图]」行为准；严格按该行理解 <image1>/<image2>/… 的指代。
- 被标为「商品图」的那张输入图是商品的**唯一真源**。商品一律用**指向该图**的措辞指代，例如「图2(<image2>)中的商品」。
- **禁止用形容词重新描述商品**：不要写 "a light-yellow halter-neck chiffon dress with ruffles" 这类重新描述商品的句子——文字重述会让下游模型按文字重绘商品，一致性立刻崩。
- 商品的全部属性（版型、颜色、材质与纹理、领型、袖、下摆层次、开合方式、五金、印花、件数、logo 有无）必须与真源图完全一致，任何一项不得增删改。

【铁律 2｜保真条款必须写在输出的第一句】
第一句用英文写死：
"Use the exact product shown in <image2>: identical silhouette, colour, material, construction, pattern, print, trims and proportions; realistic fabric drape and natural folds; do NOT redesign, recolour, restyle, lengthen, shorten or crop it; keep the product count unchanged."
（若「[输入图]」行标明商品图是 <image1>，则把这句话里的 <image2> 换成 <image1>，其余照抄。）

【铁律 3｜只允许改动镜头语言】
你可以自由创作的只有：场景、机位、景别、光线、氛围、构图、留白、道具。
商品本体、商品件数保持不变。

【铁律 4｜默认不生成任何文字】
除非用户消息中给出了**确切文案**（引号内的原文），否则输出结尾必须写：
"no text, no lettering, no logo, no watermark, no signage anywhere in the image"
若用户给了确切文案：只允许**逐字引用**该文案（不得改写、翻译或追加），并写明字体气质与位置；同一画面内的文案必须单语。

【铁律 5｜人物】
若「[输入图]」行含「人物/模特参考」图：保持其脸型、发型、发色与配饰不变；不得新增珠宝、帽子、包、手机等物；手部不得遮挡商品关键结构。
若没有人物参考图：可自行设计模特与姿态，但不得改变商品。

【铁律 7｜单张画面】
无论输入几张图（商品图 / 商品细节图 / 人物参考图），最终只输出**一张完整照片**（one single full-frame photograph）。禁止拼贴、多格、分屏、多视角并置、画中画、组图、故事板、把输入图原样并排复刻；多张输入图只用于「理解商品」，绝不是要求把它们画进同一张画布。
正文只写**一个机位、一个景别、一个瞬间**；绝不要写 "Shot 1/Shot 2"、"this set"、"a series of"、"multiple views"、"from different angles" 这类暗示多张/系列的措辞（下游 qwen21 会据此渲染成多格拼接图）。画面中**只允许一个人物、一件商品**，不得写 two models / several people / duplicated person，也不得把同一人或同一商品并排复刻多份。

【铁律 6｜输出格式】
只输出**一段连续指令**，顺序固定：
(1) 保真条款（英文，指向商品图）；
(2) 商品结构清单逐字复述（形如「<image2> 的 版型 / 颜色 / 材质 / 领型 / 下摆 / 印花 / 五金 / 件数 保持不变」，逐项核对输入图后写出，看不到的项写「未见」而不要臆造）；
(3) 场景 / 机位 / 光线 / 构图（你的创作部分）；
(4) 收尾条款（英文）："Keep the product in <image2> unchanged; no text, no logo, no watermark, no extra items. Output a single unified photograph \u2014 not a collage, not a grid, not a split-screen, not multiple panels."
不要输出解释、标题、分点符号或任何前言后语，只输出这条指令。"""

# A2｜负向提示词（写入模板 474.negative_prompt）
NEGATIVE_PROMPT = (
    "redesigned product, different colour, recoloured product, different silhouette, "
    "added print, added pattern, logo, lettering, watermark, caption, on-screen text, "
    "garbled text, gibberish characters, extra items, extra product copies, "
    "wrong material, changed proportions, deformed hands, fused fingers, extra limbs, "
    "cropped product, product cut off by frame, blurry product, low detail, "
    "collage, photo collage, image grid, multiple panels, split screen, diptych, triptych, "
    "side-by-side duplicate views, contact sheet, picture-in-picture, repeated scene, "
    "multiple views, series of shots, storyboard, film strip, comic panels, before-and-after split, "
    "grid layout, multiple people, duplicated person, cloned person, two models, several copies of the product, "
    "重复人物, 拼贴, 多格, 分屏, 多视角并置, 分镜, 多视图, 故事板, 人物克隆, 多个模特"
)


# ---------------------------------------------------------------- 图像角色行（动态注入 PE 的 user 消息开头）
def image_role_line(*, images: list) -> str:
    """images: 按喂给模型的顺序给出角色名（第 1 项 = <image1>）。

    例：["人物/模特参考", "商品图（唯一真源，不得改变）"]
    """
    if not images:
        return ""
    parts = [f"<image{i + 1}>={name}" for i, name in enumerate(images)]
    return "[输入图] 共 %d 张：%s。请严格按此理解各 <imageN> 的指代；商品图是商品唯一真源。" % (
        len(images), "；".join(parts),
    )



# ---------------------------------------------------------------- 「10-07 手动成功配方」（逐字对齐，2026-10-08 新增）
# 来源（45 实测原文，非推测）：`/data/workspace/qwen21-cloth-20261007/gen.py`
#   BASE + "The garment is <结构描述>. " + VARIANTS[i] + " Keep the garment's exact colour, print and material identical to <image1>."
# 该配方在 2026-10-07 跑出 3 商品 × 5 机位 = 15 张全部 success（保真逐张 PASS），
# 是记忆档 `qwen21-衣服广告图-3商品x5机位+双人换装-交付-20261007.md` §4 记录的「保真取向」策略。
# 关键差异（相对 PE 改写路径）：cfg=1.0、negative_prompt 为空、**不依赖 PE**、
# 直接把「指图 + 机位」写死在一段英文里 —— 这正是「一次生成一套图」的做法。
RECIPE_GARMENT_HEAD = (
    "Use the exact garment from {tag} (identical design, colour, material, print and proportion; "
    "realistic fabric drape and natural folds; do not change or redesign it) and dress it on a "
    "23-year-old American white female fashion model, 178cm tall, slim model figure, brown hair. "
)
RECIPE_SCENE = (
    "Scene: a European city street, in front of a dark wood-framed glass boutique window, grey "
    "square-tile sidewalk, plants, mannequin silhouettes and soft light reflections inside the window. "
    "Lighting: overcast soft light, slightly cool tone, occasional boutique-window reflection on her hair. "
    "Mood: confident, bold, bestie street-snap, summer dopamine OOTD. "
    "Quality: cinematic, high resolution, shallow depth of field with gently blurred background, "
    "cool film-toned grading. "
)
# 5 机位（逐字照抄 gen.py VARIANTS —— 只换机位/姿态，商品/角色/场景不变 = 一套图）
RECIPE_SHOT_VARIANTS = [
    "Camera: full-body front street-style photo, standing straight facing the camera, eye contact, hands relaxed at sides.",
    "Camera: full-body side view walking mid-stride along the sidewalk, looking back over her shoulder at the camera.",
    "Camera: candid motion shot walking towards the camera, slight wind, one hand lifting her hair, natural laugh.",
    "Camera: medium half-body shot, three-quarter turn, one hand on hip, confident chin-up expression.",
    "Camera: full-body shot leaning against the dark wood window frame, one leg crossed over the other, slight low camera angle.",
]
# 用户自带场景时用的中性机位（去掉街道/橱窗字样，避免与用户场景自相矛盾）
RECIPE_CAMERA_VARIANTS = [
    "Camera: full-body front view, standing straight facing the camera, eye contact, hands relaxed at sides.",
    "Camera: full-body three-quarter/side view, slight turn, looking towards the camera.",
    "Camera: candid walking shot moving towards the camera, natural motion, slight wind, one hand lifting her hair.",
    "Camera: medium half-body shot, three-quarter turn, one hand on hip, confident chin-up expression.",
    "Camera: full-body shot at a slight low camera angle, one leg crossed over the other, relaxed pose.",
]
RECIPE_TAIL = (
    " Keep the garment's exact colour, print and material identical to {tag}. "
    "Output exactly one single full-frame photograph of one person - not a collage, not a grid, not a split-screen, "
    "not multiple panels, not a series of shots, not multiple camera angles in one image; only one person and only "
    "one instance of the garment in the frame. "
    "no text, no lettering, no logo, no watermark anywhere in the image."
)
RECIPE_EXTRA_IMAGES = (
    " Additional image(s) {rng} are further views of the SAME single garment "
    "(use them only to calibrate material, colour and hardware; do not paste them into the frame)."
)



# ------------------------------------------------ 「一套图」set 模式（2026-10-08 45:A100 实测定稿）
# 实测（45 / A100:8194）：只喂 1 张主商品图 → 单张干净成图、同模特/同服装/同场景，仅机位变；
# 喂 2 张以上 → 模型把输入并排复刻成 N 格拼贴。故 set 模式只喂 1 张图。
# 先例：2026-10-07 手动成功配方（gen.py，衣着广告图）同法——同一人物/场景，只换机位 = 一套图。
RECIPE_SET_KEEP_HEAD = (
    "Edit the photograph in <image1>: keep the SAME single woman and the SAME single garment exactly as they are. "
    "Identical face, hairstyle, hair colour, makeup, earrings, skin tone and body proportions; identical garment "
    "colour, fabric, sheen, construction, neckline pleating, hemline layers and length. Change ONLY the camera angle "
    "and the pose as described below. Do not redesign, recolour, restyle, lengthen, shorten, crop or re-cut the "
    "garment, and never swap it for another garment. Keep the same location, background, lighting and colour grading "
    "as <image1>. "
)
RECIPE_SET_KEEP_CAMERA = [
    "Camera: full-body front view, standing straight facing the camera, eye contact, hands relaxed at her sides, full length visible from head to shoes.",
    "Camera: full-body three-quarter view, body slightly turned away then looking back towards the camera, one hand lightly lifting her hair, full length visible.",
    "Camera: medium shot framed from the waist up, slight low camera angle, confident relaxed expression, looking straight at the camera.",
    "Camera: full-body side view, standing in profile, chin slightly raised, full length visible.",
    "Camera: full-body candid walking shot moving towards the camera, natural motion, slight wind.",
]
RECIPE_SET_KEEP_TAIL = (
    " Output exactly one single full-frame photograph of one person - not a collage, not a grid, not a split screen, "
    "not multiple panels, not a contact sheet, not a diptych or triptych, not a series of shots, not multiple camera "
    "angles in one image. There must be only one person and only one instance of the garment in the frame. "
    "No text, no lettering, no logo, no watermark anywhere in the image."
)


def set_keep_prompt(*, scenario: str = "", variant_index: int = 0, specs: str = "") -> str:
    """set 模式：锁定 <image1> 里的同一模特与同一商品，只换机位 → 一批图 = 一套图。"""
    p = RECIPE_SET_KEEP_HEAD
    if specs:
        p += "The garment is %s. " % specs
    sc = (scenario or "").strip()
    if sc:
        p += "Mood / art direction for this photograph: %s. " % sc
    p += RECIPE_SET_KEEP_CAMERA[int(variant_index) % len(RECIPE_SET_KEEP_CAMERA)]
    return p + RECIPE_SET_KEEP_TAIL


def raw_prompt(*, scenario: str, variant_index: int = 0) -> str:
    """SAI 直写（[RAW]）：scenario 原文即正文，只追加机位行与「单张画面/无文字」收尾条款。"""
    return (scenario.strip() + " "
            + RECIPE_SET_KEEP_CAMERA[int(variant_index) % len(RECIPE_SET_KEEP_CAMERA)]
            + RECIPE_SET_KEEP_TAIL)


def recipe_prompt(*, garment_tag: str, scenario: str, variant_index: int, specs: str = "", n_images: int = 1) -> str:
    """「套图」配方提示词：同角色/同场景/同商品，第 variant_index 个机位。

    garment_tag: 商品图在输入里的指代（无人物参考图时是 <image1>，有人物参考图时是 <image2>）。
    specs:       商品结构清单（前端 product_specs，可为空）。
    """
    sc = (scenario or "").strip()
    if sc:
        scene = f"Scene: {sc} Lighting: soft even studio light, clean gentle falloff. "
        shots = RECIPE_CAMERA_VARIANTS
    else:
        scene = RECIPE_SCENE
        shots = RECIPE_SHOT_VARIANTS
    head = RECIPE_GARMENT_HEAD.format(tag=garment_tag)
    if specs:
        head += f"The garment is {specs}. "
    if n_images > 1:
        rng = "<image2>" if n_images == 2 else "<image2>..<image%d>" % n_images
        head += RECIPE_EXTRA_IMAGES.format(rng=rng)
    shot = shots[int(variant_index) % len(shots)]
    return head + scene + shot + RECIPE_TAIL.format(tag=garment_tag)

# ---------------------------------------------------------------- 视频阶段（r2v / ref2va）
VIDEO_FIDELITY = (
    "[FIDELITY] Identify and follow the product in the reference picture (Picture 1) exactly: "
    "identical shape, colour, material, construction, pattern, trims, hardware, logo placement and "
    "proportions. Do not redesign, recolour, restyle, lengthen, shorten or crop it, and do not add "
    "any print, lettering, badge or brand mark that is not present in the reference picture. "
    "Do not add any on-screen text, caption or subtitle. The product stays fully in frame and "
    "unobstructed for the whole shot."
)
VIDEO_CAMERA = (
    "[CAMERA] Locked-off / very slow push-in camera, stable and shake-free; only small natural motion "
    "of the product and model; no scene change, no cuts, no new objects entering the frame."
)
VIDEO_AUDIO = (
    "[AUDIO] Instrumental only — steady rhythmic percussive beat, NO vocals."
)


def video_set_note(n_refs: int) -> str:
    """视频阶段：把「整套广告图」标成同一套（同一商品 + 同一模特 + 同一场景，仅机位不同）。"""
    if not n_refs or n_refs <= 1:
        return ""
    return (
        "[SET] Picture 1 to Picture %d are the same single photo shoot of the SAME one product and the SAME one model, "
        "captured from different camera angles only. Treat them as one consistent set: the garment colour, silhouette, "
        "material, construction, trims, pattern and proportions must be identical to the reference pictures, and the "
        "face, hairstyle, hair colour, skin tone and body proportions must be identical too." % n_refs
    )


def wrap_video_prompt(text: str, n_refs: int = 1) -> str:
    """B1：把正文（用户原话或 Content-IR 增强结果）包进固定 fidelity/set/camera/audio 段。"""
    body = (text or "").strip()
    parts = [VIDEO_FIDELITY]
    note = video_set_note(n_refs)
    if note:
        parts.append(note)
    parts += [body, VIDEO_CAMERA, VIDEO_AUDIO]
    return "\n\n".join(x for x in parts if x)


def enhanced_ok(text: str) -> bool:
    """B2：增强结果必须同时 ① 指代参考图 ② 含「无文字/无标语」否定式，否则回退用户原话。"""
    t = (text or "").lower()
    if not t.strip():
        return False
    has_ref = any(k in t for k in ("reference", "picture 1", "picture1", "参考图", "参考图片"))
    has_notext = any(
        k in t
        for k in (
            "no text", "no on-screen text", "without text", "no caption", "no subtitle",
            "no logo", "no lettering", "no watermark", "text-free", "无文字", "无字幕", "无标语",
        )
    )
    return has_ref and has_notext


def truncate(text: str, n: int = 400) -> str:
    s = text or ""
    return s if len(s) <= n else s[:n] + "…"


# ---------------------------------------------------------------- P2.5「一套图」（复刻 2026-10-07 手动成功配方）
# 10-07 成功要点：固定人物 + 固定场景 + 固定商品，只换机位 → 一批图即「一套图」。
SET_PERSONA_SCENE = (
    "[固定人物｜逐字复用，不得改动] A 23-year-old American white female fashion model, 178 cm, slim model "
    "figure, brown hair; keep the identical face, hairstyle, hair colour, skin tone, makeup and body proportions.\n"
    "[固定场景｜逐字复用，不得改动] Keep one single consistent scene, with the same "
    "location, background, lighting, colour grading and depth of field; overcast soft light, "
    "cinematic film-like cool grading.\n"
    "[单张画面] This is ONE single standalone photograph, not a set, not a series and not multiple views: the SAME one "
    "model wearing the SAME single product in the SAME scene, shown from one camera angle in one frame. Do not place "
    "two or more panels, frames or copies of the model or product in the same image."
)

SHOT_VARIANTS = list(RECIPE_SHOT_VARIANTS)


def set_directive(scenario: str, index: int, total: int) -> str:
    """把用户一句话场景扩成单图指令：固定人物/场景 + 本张机位（第 index/total 张，各自独立成图）。"""
    shot = SHOT_VARIANTS[int(index) % len(SHOT_VARIANTS)]
    head = (scenario or "").strip()
    return ("%s\n\n%s\n\n[本张机位] %s" % (head, SET_PERSONA_SCENE, shot)).strip()
