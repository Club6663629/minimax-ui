"""电商广告片（advideo）广告图阶段：调独立 qwen21 节点出 N 张候选广告图。

与视频生成池解耦：走 settings.advideo_image_worker，进程内串行锁 + ComfyUI 自身串行队列。

工作流 server/workflows/advideo_image_api.json（qwen21 PE i2i）：
  UNETLoader 451 qwen_image_2.1_int8_convrot
  CLIPLoader 453 qwen3vl_8b_int8_convrot / 477 qwen3.5_9b_..._pe_i2i
  LoadImage 470(参考图/人物) + 475(商品主图) + 491…(商品细节图) → 485 BatchImagesNode
  500 TextGenerate(PE i2i) ← 487 系统提示词开关（电商保真 v1 / 通用增强器）
  484 ComfySwitchNode → 474.prompt；474.negative_prompt=商品保真负向词
  468 ComfySwitchNode → latent 取 456 EmptyLatentImage（按比例派生画布，不再跟随参考图比例）
  458 KSampler(25步/euler/simple) → 461 SaveImageAdvanced

提示词铁律：本模块产出的是「图像语言」提示词，只喂 qwen21；视频阶段用 task.prompt 走
Content-IR（镜头语言）。两者严格隔离，禁止互相回灌。

2026-10-08「一致性」改造（P0/P1，见 services/advprompt.py 与《一致性方案 v1》）：
  R1 多图白给 → product_paths/ref_paths 全部上传（商品 ≤3、参考 ≤2），额外商品图动态补 LoadImage；
  R2 单图自相矛盾 → 无参考图时不再把商品图同时塞进 <image1>/<image2>，只喂一张商品图；
  R3 系统提示词取向 → 默认切到「电商商品保真 v1」（487 switch=true），通用增强器留作 fallback；
  R4 负向词为空 → 注入 advprompt.NEGATIVE_PROMPT；
  R5 画幅不随前端 → 468 switch=true，latent 走 456（尺寸由 worker 按前端比例/档位查表传入）。
"""
import asyncio
import copy
import json
import logging
import random
from pathlib import Path
from typing import List, Optional, Sequence

import httpx

from ..config import ADVIDEO_DIR, WORKFLOW_DIR, advideo_size_for, settings
from . import advprompt, comfyui

logger = logging.getLogger(__name__)

TEMPLATE_PATH = WORKFLOW_DIR / "advideo_image_api.json"
_LOCK: Optional[asyncio.Lock] = None

NODE_LATENT = "456"
NODE_SAMPLER = "458"
NODE_SAVE = "461"
NODE_LATENT_SWITCH = "468"
NODE_LOAD_REF = "470"
NODE_LOAD_PRODUCT = "475"
NODE_BATCH = "485"
NODE_SWITCH = "484"
NODE_PE = "500"
NODE_PE_SYS_SWITCH = "487"
NODE_ENCODE = "474"
NODE_EXTRA_PRODUCT_BASE = 491   # 491、492… 动态补的商品细节图 LoadImage
NODE_EXTRA_REF_BASE = 493       # 493、494… 动态补的参考图 LoadImage（F1：第 2 张参考图不再被丢弃）
NODE_PREVIEW = "480"            # PreviewAny：PE 改写结果的 UI 回显，用于「空输出回退」判定

# PE 系统提示词节点（P0 新增，模板里若缺失则自动补，便于灰度/回滚）
NODE_PE_SYS_FIDELITY = "486"


def available() -> bool:
    """广告图阶段是否可用：需配置节点 URL 且模板存在。"""
    return bool(settings.advideo_image_worker) and TEMPLATE_PATH.exists()


def status() -> dict:
    return {
        "enabled": bool(settings.advideo_enabled),
        "worker": settings.advideo_image_worker,
        "template": str(TEMPLATE_PATH),
        "template_exists": TEMPLATE_PATH.exists(),
        "available": available(),
        "gap_sec": _gap_sec(),
        # 一致性开关（P0/P1）
        "fidelity_pe": bool(getattr(settings, "advideo_fidelity_pe", True)),
        "canvas_follow_ratio": bool(getattr(settings, "advideo_canvas_follow_ratio", True)),
        "max_product_images": int(getattr(settings, "advideo_max_product_images", 3)),
        "max_ref_images": int(getattr(settings, "advideo_max_ref_images", 2)),
        "image_size_16_9": list(advideo_size_for("16:9")),
        "pe_retries": int(getattr(settings, "advideo_pe_retries", 1) or 1),
    }


def _gap_sec() -> float:
    """「间隙跑」间隔（秒）：优先取目标节点自身的 gap:<n> 标签（节点固有属性，A100=5s），
    取不到再回退全局 ADVIDEO_IMAGE_GAP_SEC。0 = 关闭间隙。"""
    try:
        from .pool import pool  # 延迟导入，避免与 pool 初始化互相牵连
        node = pool.get_node_by_url(settings.advideo_image_worker)
        if node is not None and node.gap_sec is not None:
            return float(node.gap_sec)
    except Exception:  # noqa: BLE001 —— 取标签失败绝不能影响出图
        logger.warning("读取节点 gap 标签失败，回退全局配置", exc_info=True)
    return float(getattr(settings, "advideo_image_gap_sec", 5.0) or 0.0)


def image_size_for(aspect_ratio: str) -> tuple:
    """按前端选择的比例返回广告图画布 (width, height)（P1；worker.py 调用入口）。"""
    return advideo_size_for(aspect_ratio)


def _lock() -> asyncio.Lock:
    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    return _LOCK


def load_template() -> dict:
    return json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def ensure_fidelity_nodes(wf: dict) -> dict:
    """模板里若没有「电商保真」系统提示词节点，就地补 486/487（幂等，不落盘）。"""
    if NODE_PE_SYS_FIDELITY not in wf:
        wf[NODE_PE_SYS_FIDELITY] = {
            "class_type": "PrimitiveStringMultiline",
            "inputs": {"value": advprompt.PE_FIDELITY_SYSTEM},
        }
    if NODE_PE_SYS_SWITCH not in wf:
        wf[NODE_PE_SYS_SWITCH] = {
            "class_type": "ComfySwitchNode",
            "inputs": {
                "switch": True,
                "on_true": [NODE_PE_SYS_FIDELITY, 0],   # 电商商品保真 v1
                "on_false": ["479", 0],                 # 通用 Edit Prompt Enhancer（fallback）
            },
        }
    wf[NODE_PE]["inputs"]["system_prompt"] = [NODE_PE_SYS_SWITCH, 0]
    wf[NODE_PE_SYS_FIDELITY]["inputs"]["value"] = advprompt.PE_FIDELITY_SYSTEM
    if NODE_PREVIEW not in wf:  # PE 输出回显（空输出回退依赖它；模板缺失则就地补）
        wf[NODE_PREVIEW] = {"class_type": "PreviewAny", "inputs": {"source": [NODE_SWITCH, 0]}}
    return wf


def inject_image_job(
    wf: dict,
    *,
    scenario: str,
    product_name: str,
    ref_name: str,
    prefix: str,
    seed: int,
    width: int,
    height: int,
    product_names: Optional[Sequence[str]] = None,
    ref_names: Optional[Sequence[str]] = None,
    resolution: int = 0,
    fidelity_pe: bool = True,
    canvas_follow_ratio: bool = True,
) -> dict:
    """注入一次出图任务（就地修改并返回）。scenario 为用户一句话场景（中文可）。

    product_names: 商品图（第 1 张为主商品真源，其余为细节图）；缺省退化为 product_name。
    """
    ensure_fidelity_nodes(wf)

    products = [str(p) for p in (list(product_names) if product_names else [product_name]) if p]
    if not products:
        raise ValueError("至少需要一张商品图")

    wf[NODE_LATENT]["inputs"]["width"] = int(width)
    wf[NODE_LATENT]["inputs"]["height"] = int(height)
    wf[NODE_LATENT]["inputs"]["batch_size"] = 1
    wf[NODE_SAMPLER]["inputs"]["seed"] = int(seed)
    wf[NODE_SAVE]["inputs"]["filename_prefix"] = prefix
    refs = [str(r) for r in (list(ref_names) if ref_names else ([ref_name] if ref_name else [])) if r]
    # F1 修复：参考图不再只取第 1 张 —— 第 2 张及以后动态补 LoadImage(493、494…)
    for idx, name in enumerate(refs):
        nid = NODE_LOAD_REF if idx == 0 else str(NODE_EXTRA_REF_BASE + idx - 1)
        if idx == 0:
            wf[nid]["inputs"]["image"] = name
        else:
            wf[nid] = {"class_type": "LoadImage", "inputs": {"image": name}}
    wf[NODE_LOAD_PRODUCT]["inputs"]["image"] = products[0]
    if not refs:
        wf[NODE_LOAD_REF]["inputs"]["image"] = products[0]

    # R5：latent 走 456 EmptyLatentImage（按前端比例派生的画布），不再跟随参考图比例
    wf[NODE_LATENT_SWITCH]["inputs"]["switch"] = bool(canvas_follow_ratio)

    # R4：负向词
    enc = wf[NODE_ENCODE]["inputs"]
    enc["negative_prompt"] = advprompt.NEGATIVE_PROMPT
    if resolution:
        enc["resolution"] = int(resolution)

    # R1/R2/A5/A6：输入图角色装配 —— [人物参考?] + 商品主图 + 商品细节图
    slots: List[tuple] = []
    for idx in range(len(refs)):
        nid = NODE_LOAD_REF if idx == 0 else str(NODE_EXTRA_REF_BASE + idx - 1)
        slots.append((nid, "人物/模特参考图（保持其脸型发型配饰不变）"))
    slots.append((NODE_LOAD_PRODUCT, "商品图（唯一真源：版型/颜色/材质/结构/件数均不得改变）"))
    for idx, name in enumerate(products[1:]):
        nid = str(NODE_EXTRA_PRODUCT_BASE + idx)
        wf[nid] = {"class_type": "LoadImage", "inputs": {"image": name}}
        slots.append((nid, "商品细节图（同一件商品的另一视角，仅用于材质与五金校准）"))

    # 清掉模板里多余的旧槽位，避免上一轮的 image_3… 残留
    for key in [k for k in enc if k.startswith("images.image_")]:
        del enc[key]
    for i, (nid, _role) in enumerate(slots):
        enc[f"images.image_{i + 1}"] = [nid, 0]

    # PE 看到同一批图（BatchImagesNode 支持 1..N）
    batch = {f"images.image{i}": [nid, 0] for i, (nid, _r) in enumerate(slots)}
    wf[NODE_BATCH]["inputs"] = batch
    wf[NODE_PE]["inputs"]["image"] = [NODE_BATCH, 0]

    # 动态角色行：让 PE 明确 <imageN> 各是谁（防 R2 指代失效）
    role_line = advprompt.image_role_line(images=[r for _n, r in slots])
    wf[NODE_PE]["inputs"]["prompt"] = f"{role_line}\n\n{scenario}".strip()
    wf[NODE_PE]["inputs"]["sampling_mode.seed"] = int(seed)
    wf[NODE_SWITCH]["inputs"]["on_false"] = scenario
    wf[NODE_SWITCH]["inputs"]["switch"] = True  # 默认开 PE（用户拍板「默认开启」口径）
    wf[NODE_PE_SYS_SWITCH]["inputs"]["switch"] = bool(fidelity_pe)
    return wf


def _prune(wf: dict, sink: str) -> dict:
    """只保留 sink 可达的子图（PE 阶段与采样阶段互相隔离，避免重复跑 PE）。"""
    keep: set = set()
    stack = [sink]
    while stack:
        nid = stack.pop()
        if nid in keep or nid not in wf:
            continue
        keep.add(nid)
        for v in (wf[nid].get("inputs") or {}).values():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str) and v[0] in wf:
                stack.append(v[0])
    return {k: v for k, v in wf.items() if k in keep}


async def _history_text(client, prompt_id: str, node_id: str, timeout_min: float) -> str:
    """等 ComfyUI 任务完成并取回指定节点的文本输出（PreviewAny → outputs[node].text）。"""
    poll = max(1.0, float(settings.comfyui_poll_interval))
    max_polls = max(1, int(timeout_min * 60 / poll))
    async with httpx.AsyncClient(timeout=30) as c:
        for _ in range(max_polls):
            await asyncio.sleep(poll)
            r = await c.get(f"{client.base}/history/{prompt_id}")
            if r.status_code != 200:
                continue
            entry = (r.json() or {}).get(prompt_id)
            if not entry:
                continue
            st = entry.get("status") or {}
            if st.get("status_str") == "error":
                raise comfyui.ComfyUIError(f"ComfyUI 执行出错: {st.get('messages', [])}")
            if not st.get("completed"):
                continue
            outs = (entry.get("outputs") or {}).get(node_id) or {}
            t = outs.get("text")
            if isinstance(t, list) and t:
                return str(t[0] or "")
            return ""
    raise comfyui.ComfyUIError(f"等待 PE 文本输出超时（节点 {node_id}）")


async def _pe_text(client, wf: dict, *, retries: int = 1) -> str:
    """阶段 1：单独提交 PE 子图，取回 PE（节点 480 回显）实际写出的提示词。

    2026-10-08 实测（45，A100:8194）：4 图输入时 PE 3/3 返回空串，1 图输入 1 空 1 非空；
    空串会经模板 484 原样喂给 474.prompt → 编辑指令为空 → 模型把 N 张输入并排复刻成
    N 格拼贴。故此处必须显式判定，空则回退用户原话（见 generate_candidates）。
    """
    for attempt in range(max(1, int(retries))):
        try:
            pid = await client.submit(_prune(copy.deepcopy(wf), NODE_PREVIEW))
            txt = await _history_text(
                client, pid, NODE_PREVIEW,
                timeout_min=float(getattr(settings, "advideo_image_timeout_minutes", 30)),
            )
        except Exception:  # noqa: BLE001 —— PE 失败绝不能挡住出图
            logger.warning("PE 子图执行失败（第 %d 次）", attempt + 1, exc_info=True)
            txt = ""
        if txt and txt.strip():
            return txt.strip()
        logger.warning("PE 输出为空（第 %d/%d 次，节点 %s）", attempt + 1, int(retries), NODE_PREVIEW)
    return ""


async def generate_candidates(
    *,
    task_id: int,
    product_paths: Sequence[Path],
    ref_paths: Sequence[Path],
    scenario: str,
    count: int,
    width: int,
    height: int,
) -> List[str]:
    """在 qwen21 节点生成 count 张候选广告图，返回落盘后的本地路径列表。"""
    if not available():
        raise RuntimeError(
            "广告图阶段未配置：ADVIDEO_IMAGE_WORKER 为空或模板 "
            f"{TEMPLATE_PATH} 缺失（qwen21 节点是否已切到 to-qwen21？）"
        )
    out_dir = ADVIDEO_DIR / str(task_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    client = comfyui.ComfyUIClient(settings.advideo_image_worker)

    # R1：商品图全部用上（主图 + 细节图），参考图按上限取
    products = [Path(p) for p in list(product_paths)[: int(settings.advideo_max_product_images)]]
    refs = [Path(p) for p in list(ref_paths)[: int(settings.advideo_max_ref_images)]]
    # R2：参考图与商品图是同一文件时按「无参考图」处理，避免同一张图被当成两个角色
    refs = [r for r in refs if r not in products]
    if not products and refs:
        products, refs = refs[:1], []

    # 前端可选「商品结构清单」：随 image_prompt 以 [商品结构清单] 分隔符带下来（P2）
    specs = ""
    if "[商品结构清单]" in (scenario or ""):
        scenario, specs = scenario.split("[商品结构清单]", 1)
        scenario, specs = scenario.strip(), specs.strip()

    product_names = [await client.upload_image(p) for p in products]
    ref_names = [await client.upload_image(r) for r in refs]  # F1 修复：参考图全量上传
    logger.info(
        "广告图任务 #%s 输入：商品图 %d 张、参考图 %d 张、画幅 %dx%d、保真PE=%s",
        task_id, len(product_names), len(ref_names), width, height,
        bool(getattr(settings, "advideo_fidelity_pe", True)),
    )

    results: List[str] = []
    async with _lock():
        for i in range(max(1, int(count))):
            seed = random.randint(0, 2 ** 63 - 1)
            wf = inject_image_job(
                load_template(),
                scenario=scenario,
                product_name=product_names[0],
                product_names=product_names,
                ref_name=(ref_names[0] if ref_names else ""),
                ref_names=ref_names,
                prefix=f"advideo_{task_id}_{i}",
                seed=seed,
                width=width,
                height=height,
                resolution=int(getattr(settings, "advideo_pe_resolution", 0) or 0),
                fidelity_pe=bool(getattr(settings, "advideo_fidelity_pe", True)),
                canvas_follow_ratio=bool(getattr(settings, "advideo_canvas_follow_ratio", True)),
            )
            # ---- 阶段 1：生图提示词 ----
            # 默认走「10-07 手动成功配方」（同角色/同场景/同商品，只换机位 = 一套图）；
            # 想要 PE 改写把 settings.advideo_image_prompt_mode 设成 "pe"。
            # 2026-10-08 实测：PE(节点500) 多图输入时 3/3 返回空串，空串会经 484 原样喂给
            # 474.prompt → 编辑指令为空 → 模型把 N 张输入并排复刻成 N 格拼贴。故空输出必须回退。
            prompt_text = ""
            pe_on = bool(getattr(settings, "advideo_fidelity_pe", True))
            pmode = str(getattr(settings, "advideo_image_prompt_mode", "recipe") or "recipe").lower()
            if pmode == "pe" and pe_on:
                retries = max(1, int(getattr(settings, "advideo_pe_retries", 1) or 1))
                prompt_text = await _pe_text(client, wf, retries=retries)
            if not prompt_text:
                prompt_text = advprompt.recipe_prompt(
                    garment_tag=("<image1>" if not ref_names else "<image2>"),
                    scenario=scenario,
                    variant_index=i,
                    specs=specs,
                    n_images=len(product_names) + len(ref_names),
                )
                # 供目视核对：配方提示词原文落盘（每张一份，避免「改了没说清」）
                (out_dir / f"{task_id}_{i}.prompt.txt").write_text(prompt_text, encoding="utf-8")
                logger.info(
                    "广告图任务 #%s 第 %d/%d 张：配方提示词 %d 字符（机位 %d/%d，mode=%s pe=%s 图数=%d）",
                    task_id, i + 1, count, len(prompt_text), (i % 5) + 1, 5, pmode, pe_on,
                    len(product_names) + len(ref_names),
                )
                logger.warning(
                    "广告图任务 #%s 第 %d/%d 张：PE 输出为空 → 回退「角色行+用户原话」（%d 字符）",
                    task_id, i + 1, count, len(prompt_text),
                )
            else:
                logger.info("广告图任务 #%s 第 %d/%d 张：PE 输出 %d 字符",
                            task_id, i + 1, count, len(prompt_text))
            # ---- 阶段 2：把最终提示词写死进 474，只提交采样子图（PE 分支自动剪掉）----
            wf[NODE_ENCODE]["inputs"]["prompt"] = prompt_text
            prompt_id = await client.submit(_prune(wf, NODE_SAVE))
            info = await client.wait_image_result(prompt_id)
            content = await client.fetch_file(
                info["filename"], info.get("subfolder", ""), info.get("type", "output")
            )
            path = out_dir / f"{task_id}_{i}.png"
            path.write_bytes(content)
            results.append(str(path))
            logger.info("广告图任务 #%s 第 %d/%d 张已落盘: %s", task_id, i + 1, count, path)
            # 间隙跑（A100 专属可配）：每张之间歇 gap 秒，避免连续满载触发 84C 热降频
            gap = _gap_sec()  # 节点自身 gap 标签优先（A100=5s），否则回退全局配置
            if gap > 0 and i < max(1, int(count)) - 1:
                logger.info("间隙跑：等待 %.1fs 后出下一张", gap)
                await asyncio.sleep(gap)
    return results
