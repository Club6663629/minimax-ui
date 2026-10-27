"""短剧工作台：作业执行器（worker 常驻循环调用）。

作业种类（`hibtv_jobs.kind`）：
- outline / cast / art / script / storyboard ── LLM（或确定性骨架）产出文档 + 质量门
- images ── 锚点图 / 派生视图出图（qwen21 节点，串行）
- compose ── 整集 ffmpeg 拼接

所有状态机都落在 `hibtv_jobs` 上，前端 4s 轮询；已产出物不受取消影响（与原型一致）。
"""
import asyncio
import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..config import HIBTV_DIR, settings
from ..database import SessionLocal
from ..models import (HibtvAsset, HibtvChange, HibtvDoc, HibtvJob, HibtvProject, HibtvRender,
                      HibtvSegment, Task, Upload, User)
from . import hibtv_compose, hibtv_gates, hibtv_image, hibtv_llm, hibtv_parse
from .billing import add_credits

logger = logging.getLogger(__name__)

# 每类资产的视图定义：[view, 中文名]，第 1 个是锚点
# 版式对齐《角色/场景/道具 设定与多视图规范》（assetspec2trl）：
# 角色 4 格（§3.2，3/4 侧替换 90° 正侧）；场景 2×2 四宫格（§4.3）；道具 1×3 三视角（§5.2）
VIEW_DEFS: Dict[str, List[List[str]]] = {
    "cast": [["front", "正面全身"], ["big", "大头"], ["side", "3/4 侧面"], ["back", "背面"]],
    "scene": [["wide", "广角全景"], ["mid", "中景调度区"], ["close", "材质特写"], ["reverse", "反打机位"]],
    "prop": [["front", "正面"], ["threeq", "3/4 侧面"], ["top", "俯视"]],
}

# 视图约束按资产类型分开：同一个 `top` 对场景曾是「俯视全景」、对道具是「俯看这一件」，
# 共用一份文案会把道具写成「空间俯视」。约束要写成硬命令（数量 / 占比 / 禁止项），
# 只写「保持一致」模型会自由发挥——大头照最容易变成另一个人或带上手和场景。
VIEW_HINT: Dict[str, Dict[str, str]] = {
    "cast": {
        "front": "同一个人物的正面全身：站姿自然、手臂不交叉，从头到脚完整入画，头顶与脚底各留约 5% 余量；"
                 "五官、脸型、体型、发型与服装严格沿用参考图，不重新设计；中性纯色背景；"
                 "画面里只有这一个人，不出现第二张脸、文字或边框",
        "big": "同一个人物的头肩人像特写，证件照取景：只画头部与肩部，头顶贴近画面上边、"
               "下巴在画面高度正中、下边切在锁骨；不写也不画下装与鞋（一提镜头就会退去画它们）；"
               "五官、脸型、眉形、眼型、鼻唇比例、发际线、发型发色严格沿用参考图，不美化、不换人；"
               "表情自然收敛，视线看向镜头；背景为中性纯色或极简虚化，不出现场景陈设、道具、文字、边框；"
               "画面里只有这一个人，不出现第二张脸、手或身体其他部位；头顶与下巴都不被画面裁掉",
        "side": "同一个人物的 3/4 侧面全身（镜头绕角色向左偏转约 45°）：双眼都可见、鼻梁走向与下颌线"
                "可见、肩线走向与侧身厚度可见；视线随身体自然偏移，不直勾勾看镜头；从头到脚完整入画；"
                "体型、发型、服装与参考图完全一致；只有这一个人，不出现正脸或镜像重复",
        "back": "镜头绕人物旋转 180° 后的背面全身：只画背面看得到的东西（后脑发型、衣服背部结构、"
                "脚跟与鞋底），从头到脚完整入画；不出现脸、不出现纽扣口袋等正面细节、不回头露肩看镜头；"
                "不出现第二张脸",
    },
    "scene": {
        "wide": "同一场景的广角全景：完整空间格局、边界与出入口，地面、墙面、主要家具与陈设的位置关系、"
                "材质、色调与参考图一致；光照方向与色温一致；画面里不出现人物、文字招牌或水印；不裁切主体陈设",
        "mid": "同一场景面向主调度区（案前 / 门口 / 主位前）的中景机位：画面里必须有能发生人物调度的区域，"
               "家具位置、材质、色调与光照方向与参考图一致；不出现人物与文字，不新增参考图里没有的家具",
        "close": "同一场景固定物件的材质/工艺特写：桌面纹理与磨损、墙面与地面交界处、雕花与灯具近景，"
                 "材质、颜色与光照方向与参考图一致；是工艺细节特写，不是氛围空镜；不出现人物与文字",
        "reverse": "从该空间另一端反向拍回主视图方向的机位：可见相反一侧的墙面与陈设，位置关系、材质与"
                   "色调光照保持一致；不出现人物与文字，不新增参考图里没有的家具",
    },
    "prop": {
        "front": "同一件道具的正面产品图：单件完整居中、占画面高度约 70%，材质纹理、颜色、磨损与刻字"
                 "与参考图完全一致；浅灰或纯白无缝背景，柔和均匀布光；不出现手、人物、场景或包装盒，"
                 "不出现文字水印或价签；不改变道具形状与比例",
        "threeq": "同一件道具的 3/4 侧面产品图（镜头绕道具约 45°）：单件完整居中，能看清厚度、圆角与"
                  "立体结构，材质颜色与参考图一致；浅色无缝背景；不出现手、人物、文字水印",
        "top": "同一件道具的正上方俯看：单件完整居中，可看清顶面结构与按钮/开口位置，材质颜色与参考图一致；"
               "浅色无缝背景；不出现手、人物、文字水印",
    },
}

# 兜底：未知视图名（自定义 defs）仍按「只改视角、不改本体」给一句
VIEW_HINT_ANY = "与参考图是同一个主体，只改变视角与取景，比例、材质与细节保持一致"

# 反向词直接挂 474.negative_prompt，不经 PE、写了就生效；比正向堆定语可靠。
# 商品保真负向词（含拼贴 / 网格 / 克隆人 / 屏内文字）作为底座，这里只补视图专属项。
VIEW_NEGATIVE: Dict[str, Dict[str, str]] = {
    "cast": {
        "big": "full body, standing figure, whole torso, hips, legs, shoes, lower garment, "
               "wide shot, medium shot, headroom gap, cropped head, cropped chin",
        "side": "facing camera, frontal face, eye contact, looking at viewer, full 90-degree profile, "
                "only one eye visible, mirrored duplicate, second person",
        "back": "face, facial features, eyes, nose, mouth, buttons, pockets, zipper on chest, "
                "front view, looking back over shoulder, turned head",
        "front": "cropped head, cropped feet, no headroom, sitting, kneeling",
    },
    "scene": {"_any": "person, people, human figure, crowd, silhouette, signage, shop sign, "
                      "poster text, subtitle, watermark"},
    "prop": {"_any": "hand, fingers, arm, person, human body, holding by hand, packaging box, "
                     "price tag, label text, watermark, scene background, table reflection"},
}

# 所有资产图都压的通用项：单帧、单主体、无屏内文字
NEGATIVE_BASE = ("extra person, duplicated person, cloned face, multiple people, side-by-side views, "
                 "character sheet, model sheet, turnaround sheet, reference table layout")

# ---------------------------------------------------------------- 多视图设定稿（一图多格）
# 一张 sheet 装下全部视角：出图一次、裁切一次，主图改了就整组同步。
# cells 用**比例矩形** (view, x, y, w, h)，按模型实际输出尺寸换算像素：
# 不依赖它画的线，也允许列宽不等分（大头格窄一点，省下的宽度给三个全身格）。
# 画幅取 Qwen-Image-2.1 官方 16:9 原生 2K（2752×1536）——低于推荐值等于白丢细节。
SHEET_SPEC: Dict[str, Dict] = {
    # 宽度配比有硬约束：大头格窄于约 0.2 画幅（<0.4:1 的竖条）就会横向切掉头肩，
    # 0.22 / 0.26 / 0.26 / 0.26 是 16:9 下「不切头」与「全身格尽量宽」的折中。
    # 第 3 格用 3/4 侧而非 90° 正侧（规范「前提三」）：观众认人绝大多数在 3/4 附近角度。
    "cast": {
        "cells": [("big", 0.00, 0.0, 0.22, 1.0),
                  ("front", 0.22, 0.0, 0.26, 1.0),
                  ("side", 0.48, 0.0, 0.26, 1.0),
                  ("back", 0.74, 0.0, 0.26, 1.0)],
        "brief": "画面按列分成 4 个等高画格：第 1 格较窄（约占画幅宽度 22%，头肩特写），"
                 "后 3 格各约占 26%（正面全身 / 3/4 侧全身 / 背面全身）",
    },
    # 场景用规范 §4.3 的 2×2 四宫格：每格恰好 1376×768（16:9），与信息异构的四种机位天然
    # 匹配；旧版 5 格里 front/left/right 全是等高机位旋转，信息量重复——这是「角度没区别」的根因。
    "scene": {
        "cells": [("wide", 0.0, 0.0, 0.5, 0.5), ("mid", 0.5, 0.0, 0.5, 0.5),
                  ("close", 0.0, 0.5, 0.5, 0.5), ("reverse", 0.5, 0.5, 0.5, 0.5)],
        "brief": "画面 2×2 等分成 4 个画格（每格恰为 16:9）：左上 广角全景 / 右上 中景调度区 / "
                 "左下 材质特写 / 右下 反打机位",
    },
    # 道具用规范 §5.2 的 1×3 三视角：正面/3-4 侧/俯视。多一格就是多一份裁切与人工核对成本。
    "prop": {
        "cells": [("front", 0.0, 0.0, 1 / 3, 1.0), ("threeq", 1 / 3, 0.0, 1 / 3, 1.0),
                  ("top", 2 / 3, 0.0, 1 / 3, 1.0)],
        "brief": "画面按列等分成 3 个等高画格：正面视图 / 3/4 侧面视图 / 俯视视图",
    },
}
# 无论视频横版还是竖版，设定稿统一 16:9（画格横向排列才能一遍读完）
SHEET_W, SHEET_H = 2752, 1536

# 模板写法来自两处：skills 里 character-refs 的逐视图几何约束（大头证件照取景、
# 侧面只看右半边脸、背面不出现五官），加上业界 turnaround / reference sheet 的
# 通行写法（orthographic、A-pose、8% 安全边距、平光无投影、跨格同焦距、俯视 1.2m 剖切）。
# 不给色板/比例尺/箭头文字——那些是给人看的，会被裁进某一格。
SHEET_PROMPT: Dict[str, str] = {
    "cast": (
        "角色设定参考图（character reference sheet / turnaround sheet）：横向 16:9、纯白无缝背景，"
        "画面按列分成 4 个等高画格，格与格之间只留均匀白边、不画分隔线、不写任何文字："
        "第 1 格较窄（约占画幅宽度 22%）——正脸特写：头肩近景、证件照取景，头顶距画格上边约一成高，"
        "下巴在画格高度中部，下边切在锁骨，双眼平视镜头，五官清晰，不出现胸部以下；"
        "后 3 格各约占 26%——第 2 格正面全身：正对镜头，A 字站姿，双臂自然下垂且与躯干留出缝隙、掌背朝前，"
        "头顶到脚底完整入镜；第 3 格 3/4 侧全身：镜头绕角色向左偏转约 45°，双眼都可见、鼻梁走向与"
        "下颌线可见、肩线走向与侧身厚度可见，不得退成 90° 正侧只露一只眼；"
        "第 4 格背面全身：镜头绕人物旋转 180°，只画后脑发型、衣服背部结构与脚跟，不出现五官。"
        "正交视角（orthographic views, no perspective distortion）；四格是同一个人：五官、发型、体型、"
        "服装、配饰完全一致，身高一致，四格头顶与脚底各自对齐同一水平线；"
        "每格四周留 8% 安全边距，人物不触画格边缘；均匀平光棚拍（flat even studio lighting, "
        "no cast shadow, no depth of field, no vignette）。"
    ),
    "scene": (
        "场景设定参考图（location reference sheet）：横向 16:9，画面 2×2 等分成 4 个画格"
        "（2 行 2 列，每格恰为 16:9 横幅），格与格之间不画任何颜色的分隔线、无边框、无间隙，"
        "无缝相邻（NO borders of any color, NO dividing lines, NO frames, panels seamlessly adjacent），"
        "不写任何文字："
        "左上格 广角全景：完整空间格局、边界与出入口，最宽视场；"
        "右上格 中景机位：面向空间主调度区（案前 / 门口 / 主位前），画面里必须有能发生人物调度的区域；"
        "左下格 特写细节：固定物件的材质或工艺近景（桌面纹理、墙面与地面交界、雕花与灯具），"
        "不是氛围空镜；右下格 反打机位：从空间另一端反向拍回主视图方向，可见相反一侧的墙面与陈设。"
        "四格是同一个空间、同一时刻、同一组光源（光源方向、色温与主辅色完全一致），只有相机机位变化；"
        "地面、门窗、家具与陈设的相对位置、材质与色调完全一致；每格四周留 8% 安全边距，"
        "空间完整入画；不出现人物、动物、文字招牌与现代物品。"
    ),
    "prop": (
        "道具设定参考图（prop reference sheet / product turnaround）：横向 16:9、纯白无缝背景，"
        "画面按列等分成 3 个等高画格，格与格之间不画任何颜色的分隔线、无边框、无间隙"
        "（NO dividing lines, NO frames, panels seamlessly adjacent）、不写任何文字："
        "第 1 格 正面视图：正对镜头平视，完整外观与主要细节；第 2 格 3/4 侧面视图：镜头绕道具"
        "旋转约 45°，展示厚度、圆角与立体感；第 3 格 俯视视图：从正上方俯看，展示顶面结构与"
        "开口 / 按钮位置。"
        "正交视角、三格同一焦距（orthographic, consistent focal length），三格是同一件道具、"
        "同一状态、同一光源方向与色温：形制、比例、材质、纹理、颜色、磨损与刻字完全一致；"
        "每格单件居中，占该格高度约 70%–80%，四周留 8% 安全边距不触边缘；柔和均匀棚拍光，"
        "无高光热点、无投影（soft even light, no specular hotspot, no drop shadow）；"
        "不出现手、人物、场景、台面、包装盒与价签。"
    ),
}

# sheet 模式的反向词：**不能**再压 character sheet / 多视图（那是本模式的目标），
# 也不能压“格子不等宽”（本模式就是 22/26/26/26 的不等分），
# 改压“格子数不对 / 每格人不同 / 文字边框 / 透视畸变 / 景深投影”。
SHEET_NEGATIVE: Dict[str, str] = {
    "cast": ("different person in each panel, inconsistent face, different hairstyle, different outfit, "
             "different body proportions, more than four panels, fewer than four panels, empty panel, "
             "cropped head, cropped feet, subject touching panel edge, overlapping panels, "
             "standing on different ground lines, second person, duplicated person, dynamic pose, "
             "holding prop, full 90-degree profile, only one eye visible, perspective distortion, "
             "wide angle distortion, bokeh, depth of field, "
             "cast shadow, drop shadow, vignette, "
             "text, caption, letters, label, logo, watermark, signature, arrow, annotation, "
             "color swatch, height chart, ruler, border line, dividing lines, panel borders, frames, "
             "colored background, dramatic lighting, harsh shadow, deformed hands, extra limbs"),
    "scene": ("person, people, human figure, crowd, silhouette, animal, text, caption, signage, shop sign, "
              "poster text, watermark, border line, dividing lines, panel borders, frames, "
              "more than four panels, fewer than four panels, empty panel, missing panel, "
              "overlapping panels, subject touching panel edge, different room in each panel, "
              "different furniture layout, furniture changing position, different lighting direction, "
              "different color temperature, different time of day, "
              "perspective distortion, wide angle distortion, fisheye, bokeh, depth of field, drop shadow"),
    "prop": ("hand, fingers, arm, person, holding by hand, text, caption, letters, price tag, label, "
             "logo, watermark, border line, dividing lines, panel borders, frames, "
             "more than three panels, fewer than three panels, empty panel, missing panel, "
             "overlapping panels, subject touching panel edge, different object in each panel, "
             "different material, changed proportions, inconsistent lighting direction, "
             "perspective distortion, wide angle distortion, bokeh, "
             "depth of field, specular hotspot, drop shadow, "
             "packaging box, scene background, table reflection"),
}

MEDIA = {"image": {".png", ".jpg", ".jpeg", ".webp"}, "video": {".mp4", ".mov", ".webm"},
         "audio": {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}}


# ------------------------------------------------------------------ 路径与 JSON
def project_dir(project_id: int) -> Path:
    d = HIBTV_DIR / str(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def abs_path(project_id: int, rel: str) -> Path:
    """把库里的相对路径还原成绝对路径（防目录穿越）。"""
    base = project_dir(project_id).resolve()
    p = (base / (rel or "")).resolve()
    if base not in p.parents and p != base:
        raise ValueError("非法文件路径")
    return p


def rel_path(project_id: int, path: Path) -> str:
    base = project_dir(project_id).resolve()
    return Path(path).resolve().relative_to(base).as_posix()


def collect_media(project_id: int, rel_name: str, src: Path) -> str:
    """把平台产物（Task 成片）收进项目目录，返回相对路径。

    Worker 落在 `DATA_DIR/output/`，不在项目目录内，而 `/files/hibtv/{pid}/{rel}`
    只服务项目目录：不收进来就既不能预览也不能下载。
    """
    if not src.exists():
        return ""
    dest = project_dir(project_id) / rel_name
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dest.exists() and dest.stat().st_size == src.stat().st_size:
            return rel_name
        if dest.exists():
            dest.unlink()
        try:
            os.link(src, dest)          # 同盘硬链：不占双份空间
        except OSError:
            shutil.copy2(src, dest)     # 跨盘回退复制
    except OSError:
        logger.warning("产物归集失败：%s → %s", src, dest, exc_info=True)
        return ""
    return rel_name


def jloads(raw: Optional[str], default):
    try:
        val = json.loads(raw or "")
    except (TypeError, ValueError):
        return default
    return val if val is not None else default


def jdumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def now() -> datetime:
    return datetime.now()


# ------------------------------------------------------------------ 文档读写
def get_doc(db: Session, project_id: int, kind: str, ep: int = 0) -> Optional[HibtvDoc]:
    return (db.query(HibtvDoc)
            .filter(HibtvDoc.project_id == project_id, HibtvDoc.kind == kind,
                    HibtvDoc.ep == int(ep))
            .first())


def ensure_doc(db: Session, project_id: int, kind: str, ep: int = 0) -> HibtvDoc:
    doc = get_doc(db, project_id, kind, ep)
    if doc is None:
        doc = HibtvDoc(project_id=project_id, kind=kind, ep=int(ep), status="empty")
        db.add(doc)
        db.flush()
    return doc


def save_doc(db: Session, project_id: int, kind: str, ep: int, data: Dict, *,
             gates: Optional[Dict] = None, status: str = "done", error: str = "") -> HibtvDoc:
    doc = ensure_doc(db, project_id, kind, ep)
    doc.data = jdumps(data)
    doc.version = int(doc.version or 0) + 1
    doc.status = status
    doc.error = error
    if gates is not None:
        doc.gates = jdumps(gates)
    doc.updated_at = now()
    # 落盘留档（便于直接喂 vendored 脚本 / 排查）
    try:
        out = project_dir(project_id) / kind / (f"{kind}-E{ep:02d}.json" if ep else f"{kind}.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(jdumps(data), encoding="utf-8")
    except OSError:
        logger.warning("短剧文档落盘失败（不影响主流程）", exc_info=True)
    return doc


def doc_payload(doc: HibtvDoc) -> Dict:
    return {
        "kind": doc.kind, "ep": doc.ep, "status": doc.status,
        "version": doc.version, "error": doc.error or "",
        "data": jloads(doc.data, {}), "gates": jloads(doc.gates, {}),
        "updated_at": doc.updated_at.strftime("%Y-%m-%d %H:%M:%S") if doc.updated_at else "",
    }


def set_json_path(obj, path: str, value):
    """按 `a.b[0].c` 写 JSON 路径，返回旧值。"""
    if not path:
        raise ValueError("缺少 path")
    tokens = re.findall(r"[^.\[\]]+", path)
    cur = obj
    for i, tok in enumerate(tokens):
        last = i == len(tokens) - 1
        key = int(tok) if tok.isdigit() else tok
        if last:
            if isinstance(cur, list):
                old = cur[key] if key < len(cur) else None
                if key < len(cur):
                    cur[key] = value
                else:
                    raise ValueError("路径越界")
                return old
            if not isinstance(cur, dict):
                raise ValueError("路径不是对象")
            old = cur.get(key)
            cur[key] = value
            return old
        nxt = cur[key] if isinstance(cur, list) else cur.get(key)
        if nxt is None:
            nxt = {} if not tokens[i + 1].isdigit() else []
            if isinstance(cur, list):
                cur[key] = nxt
            else:
                cur[key] = nxt
        cur = nxt
    return None


def get_json_path(obj, path: str):
    tokens = re.findall(r"[^.\[\]]+", path or "")
    cur = obj
    for tok in tokens:
        if cur is None:
            return None
        if isinstance(cur, list):
            idx = int(tok) if tok.isdigit() else None
            if idx is None or idx >= len(cur):
                return None
            cur = cur[idx]
        elif isinstance(cur, dict):
            cur = cur.get(tok)
        else:
            return None
    return cur


# ------------------------------------------------------------------ 资产同步
def _asset_views(project_id: int, asset: HibtvAsset) -> Dict:
    return jloads(asset.views, {})


def sync_assets(db: Session, project: HibtvProject, kind: str, rows: Sequence[Dict]) -> int:
    """把文档里的角色 / 场景 / 道具同步成资产卡（按 code 更新或新建）。"""
    existing = {a.code: a for a in db.query(HibtvAsset).filter(
        HibtvAsset.project_id == project.id, HibtvAsset.kind == kind).all()}
    seen = set()
    count = 0
    prefix = {"cast": "C", "scene": "S", "prop": "P"}.get(kind, "X")
    for i, row in enumerate(rows):
        code = (row.get("id") or row.get("code") or "").strip() or f"{prefix}{i + 1:02d}"
        name = (row.get("name") or code or f"{kind}{i + 1}").strip()
        if not code:
            continue
        seen.add(code)
        asset = existing.get(code)
        if asset is None:
            asset = HibtvAsset(project_id=project.id, kind=kind, code=code, name=name,
                               data=jdumps(row), views="{}", status="empty")
            db.add(asset)
        else:
            asset.name = name
            asset.data = jdumps(row)
        asset.sort = i
        asset.updated_at = now()
        count += 1
    for code, asset in existing.items():
        if code not in seen:
            asset.hidden = True
        elif asset.hidden and not db.query(HibtvChange).filter(
                HibtvChange.project_id == project.id,
                HibtvChange.what == f"asset:{asset.id}",
                HibtvChange.path == "hidden",
                HibtvChange.reverted.is_(False)).first():
            # 文档里又出现这个 code（常见于撤销「从文档删掉这角色」）：
            # 只恢复不是用户亲手隐藏的卡，否则隐藏的卡会被同步反复拉回来。
            asset.hidden = False
    db.flush()
    return count


def asset_payload(project_id: int, asset: HibtvAsset) -> Dict:
    views = jloads(asset.views, {})
    # `__sheet` 是多视图设定稿本身，不是视图；单独给前端，不混进 views
    sheet_meta = dict(views.pop("__sheet", {}) or {})
    out_views: Dict[str, Dict] = {}
    for view, meta in (views or {}).items():
        entry = dict(meta or {})
        rel = entry.get("path") or ""
        entry["url"] = f"/files/hibtv/{project_id}/{rel}" if rel else ""
        out_views[view] = entry
    # 每张视图当前生效的出图约束（用户自定义 or 默认），前端「改这张的提示词」直接回显，
    # 免得把约束文案在前端复制一份、两边改飘。
    view_hints = {}
    for view, _label in VIEW_DEFS.get(asset.kind, []):
        text, custom = view_hint(asset, view)
        view_hints[view] = {"text": text, "custom": custom}
    sheet_meta["url"] = (f"/files/hibtv/{project_id}/{sheet_meta['path']}"
                         if sheet_meta.get("path") else "")
    return {
        "id": asset.id, "kind": asset.kind, "code": asset.code, "name": asset.name,
        "data": jloads(asset.data, {}), "views": out_views, "viewHints": view_hints,
        "sheet": sheet_meta,
        "sheetSpec": {
            # 画幅 + 每种资产的格子比例矩形（view, x, y, w, h）：前端只做展示/提示用，
            # 裁切以后端实际出图尺寸为准，不依赖这份数据。
            "size": list(sheet_size()),
            "kinds": {k: {"brief": v["brief"],
                           "cells": [[c[0], round(c[1], 4), round(c[2], 4),
                                      round(c[3], 4), round(c[4], 4)] for c in v["cells"]]}
                      for k, v in SHEET_SPEC.items()},
        },
        "status": asset.status, "version": asset.version, "hidden": bool(asset.hidden),
        "sort": asset.sort,
        "defs": VIEW_DEFS.get(asset.kind, []),
        "anchor": VIEW_DEFS.get(asset.kind, [["front", "正面"]])[0][0],
    }


# ------------------------------------------------------------------ 作业投递
def enqueue(db: Session, project_id: int, kind: str, *,
            ep: int = 0, ref: str = "", payload: Optional[Dict] = None) -> HibtvJob:
    job = HibtvJob(project_id=project_id, kind=kind, ep=int(ep), ref=ref or "",
                   status="queued", payload=jdumps(payload or {}))
    db.add(job)
    db.flush()
    return job


def job_payload(job: HibtvJob) -> Dict:
    return {
        "id": job.id, "kind": job.kind, "ep": job.ep, "ref": job.ref,
        "status": job.status, "progress": job.progress, "stage": job.stage,
        "error": job.error or "", "payload": jloads(job.payload, {}),
        "created_at": job.created_at.strftime("%Y-%m-%d %H:%M:%S") if job.created_at else "",
        "finished_at": job.finished_at.strftime("%Y-%m-%d %H:%M:%S") if job.finished_at else "",
    }


# ------------------------------------------------------------------ 执行入口
async def run_job(job_id: int) -> None:
    """执行一条作业；异常一律落 failed（不抛给调度循环）。"""
    with SessionLocal() as db:
        job = db.get(HibtvJob, job_id)
        if job is None:
            return
        job.status = "running"
        job.started_at = now()
        job.progress = 5
        db.commit()
        project = db.get(HibtvProject, job.project_id)
        if project is None:
            _fail(db, job, "项目不存在")
            return
        try:
            handler = {
                "outline": _job_outline, "cast": _job_cast, "art": _job_art,
                "script": _job_script, "storyboard": _job_storyboard,
                "images": _job_images, "compose": _job_compose,
            }.get(job.kind)
            if handler is None:
                _fail(db, job, f"未知作业类型 {job.kind}")
                return
            await handler(db, project, job)
            job.status = "done"
            job.progress = 100
            job.stage = "完成"
            job.finished_at = now()
            db.commit()
        except Exception as exc:  # noqa: BLE001 —— 作业失败必须收敛
            logger.exception("短剧作业 #%s(%s) 失败", job.id, job.kind)
            db.rollback()
            job = db.get(HibtvJob, job_id)
            if job is not None:
                _fail(db, job, str(exc))
                _refund(db, project, job)
                if job.kind == "images":
                    # 出图中断后不能把资产永远卡在「出图中」
                    fresh = db.get(HibtvProject, job.project_id)
                    if fresh is not None:
                        reset_drawing(db, fresh)


def _fail(db: Session, job: HibtvJob, message: str) -> None:
    job.status = "failed"
    job.error = str(message)[:2000]
    job.finished_at = now()
    db.commit()


def _progress(db: Session, job: HibtvJob, pct: int, stage: str) -> None:
    job.progress = max(0, min(100, int(pct)))
    job.stage = stage
    db.commit()


def _refund(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    """出图 / 拼接作业系统失败时全额退还预扣积分（只退一次）。"""
    payload = jloads(job.payload, {})
    cost = int(payload.get("cost") or 0)
    if cost <= 0 or payload.get("refunded"):
        return
    user = db.get(User, project.user_id)
    if user is not None:
        add_credits(db, user, cost, "refund", note=f"短剧 {job.kind} 作业失败退还",
                    task_id=None)
    payload["refunded"] = True
    job.payload = jdumps(payload)
    db.commit()


def _parsed(db: Session, project: HibtvProject) -> tuple:
    script = hibtv_parse.parse_script(project.script_text or "")
    art = hibtv_parse.parse_art(project.art_text or "")
    return script, art


def _require_outline(db: Session, project: HibtvProject) -> Dict:
    doc = get_doc(db, project.id, "outline")
    if doc is None or doc.status != "done":
        raise RuntimeError("大纲尚未生成，先跑一次「大纲」")
    return jloads(doc.data, {})


def _art_doc(db: Session, project: HibtvProject) -> Optional[Dict]:
    """质量门对账用的是**已落库的美术文档**（scenes / props 带 id）。"""
    doc = get_doc(db, project.id, "art")
    if doc is None or doc.status != "done":
        return None
    return jloads(doc.data, {})


# ------------------------------------------------------------------ 各类作业
async def _job_outline(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    _progress(db, job, 15, "解析剧本与美术设定")
    script, art = _parsed(db, project)
    _progress(db, job, 40, "生成大纲骨架")
    data, source = await hibtv_llm.generate(
        "outline",
        user_text=hibtv_llm.outline_prompt(project, script, art),
        fallback=lambda: hibtv_llm.outline_from_parse(project, script, art),
    )
    hibtv_llm.ensure_reuse_plan(data)
    _progress(db, job, 80, "跑质量门")
    gates = hibtv_gates.run_gates("outline", data)
    save_doc(db, project.id, "outline", 0, data, gates=gates)
    project.status = "ready"
    project.detected = jdumps(hibtv_parse.detect_summary(script, art))
    project.updated_at = now()
    db.commit()
    logger.info("短剧大纲已生成（来源 %s，质量门 %s）", source, gates.get("status"))


async def _job_cast(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    outline = _require_outline(db, project)
    _progress(db, job, 30, "解析美术设定")
    _script, art = _parsed(db, project)
    _progress(db, job, 60, "生成角色卡")
    data, source = await hibtv_llm.generate(
        "cast",
        user_text=hibtv_llm.cast_prompt(project, art, outline),
        fallback=lambda: hibtv_llm.cast_from_parse(project, art, outline),
    )
    hibtv_llm.ensure_cast_ids(data)
    _progress(db, job, 90, "同步资产卡")
    save_doc(db, project.id, "cast", 0, data)
    sync_assets(db, project, "cast", data.get("characters") or [])
    db.commit()
    logger.info("短剧角色卡已生成（来源 %s，%d 人）", source, len(data.get("characters") or []))


async def _job_art(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    outline = _require_outline(db, project)
    _progress(db, job, 30, "解析美术设定")
    _script, art = _parsed(db, project)
    _progress(db, job, 60, "生成场景与道具")
    data, source = await hibtv_llm.generate(
        "art",
        user_text=hibtv_llm.art_prompt(project, art, outline),
        fallback=lambda: hibtv_llm.art_from_parse(project, art, outline),
    )
    _progress(db, job, 90, "同步资产卡")
    save_doc(db, project.id, "art", 0, data)
    sync_assets(db, project, "scene", data.get("scenes") or [])
    sync_assets(db, project, "prop", data.get("props") or [])
    db.commit()
    logger.info("短剧美术设定已生成（来源 %s，%d 场景 / %d 道具）", source,
                len(data.get("scenes") or []), len(data.get("props") or []))


def _episode_of(script: Dict, ep: int) -> Optional[Dict]:
    for row in script.get("episodes") or []:
        if int(row.get("ep") or 0) == int(ep):
            return row
    return None


async def _job_script(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    outline = _require_outline(db, project)
    script_parse, _art_parse = _parsed(db, project)
    ep_row = _episode_of(script_parse, job.ep) or (script_parse["episodes"][0] if script_parse["episodes"] else None)
    if ep_row is None:
        raise RuntimeError("剧本里没有这一集")
    _progress(db, job, 40, f"结构化第 {job.ep} 集")
    prev = get_doc(db, project.id, "script", job.ep)
    hint = hibtv_gates.hint_of(jloads(prev.gates, {}) if prev else None)
    data, source = await hibtv_llm.generate(
        "script",
        user_text=hibtv_llm.script_prompt(project, ep_row, outline),
        fallback=lambda: hibtv_llm.script_from_parse(project, ep_row, outline),
        gates_hint=hint,
    )
    hibtv_llm.clamp_target_seconds(data, script_parse, job.ep)
    # 生成侧先补齐「大纲说这集有的爆点必须认领」，再跑门（漏写整栏是常态，不是内容缺陷）
    claimed = hibtv_llm.ensure_beats_claimed(data, outline)
    if claimed:
        logger.info("短剧剧本 E%02d 补齐 beatsClaimed %d 集", job.ep, claimed)
    _progress(db, job, 80, "跑质量门")
    gates = hibtv_gates.run_gates("script", data, outline=outline, art=_art_doc(db, project))
    save_doc(db, project.id, "script", job.ep, data, gates=gates)
    db.commit()
    logger.info("短剧剧本 E%02d 已生成（来源 %s，质量门 %s）", job.ep, source, gates.get("status"))


async def _job_storyboard(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    outline = _require_outline(db, project)
    script_doc = get_doc(db, project.id, "script", job.ep)
    if script_doc is None or script_doc.status != "done":
        # 先补跑剧本，保证分镜有事实来源
        await _job_script(db, project, job)
        script_doc = get_doc(db, project.id, "script", job.ep)
    if script_doc is None:
        raise RuntimeError("本集剧本生成失败，无法出分镜")
    script_data = jloads(script_doc.data, {})
    _progress(db, job, 45, f"第 {job.ep} 集分段切镜")
    data, source = await hibtv_llm.generate(
        "storyboard",
        user_text=hibtv_llm.storyboard_prompt(project, script_data, outline),
        fallback=lambda: hibtv_llm.storyboard_from_script(project, script_data, outline),
    )
    script_ep = next((e for e in script_data.get("episodes") or []
                      if int(e.get("ep") or 0) == int(job.ep)),
                     (script_data.get("episodes") or [{}])[0])
    # 生成侧先把「台词装不下 / 越场道具 / 段超上限」这三类必挂项消化掉，再跑门
    hibtv_llm.fit_dialogue_seconds(data, script_ep)
    hibtv_llm.prune_foreign_props(data, script_ep)
    added = hibtv_llm.split_oversized(data, settings.hibtv_seg_max_seconds, job.ep)
    fixed = hibtv_llm.ensure_segment_prompts(data, script_ep, outline)
    if added or fixed:
        logger.info("短剧分镜 E%02d 拆段 +%d、补齐提示词 %d 段", job.ep, added, fixed)
    _progress(db, job, 80, "跑质量门")
    gates = hibtv_gates.run_gates("storyboard", data, outline=outline,
                                  art=_art_doc(db, project), script=script_data)
    save_doc(db, project.id, "storyboard", job.ep, data, gates=gates)
    sync_segments(db, project, job.ep, data)
    db.commit()
    logger.info("短剧分镜 E%02d 已生成（来源 %s，质量门 %s）", job.ep, source, gates.get("status"))


def sync_segments(db: Session, project: HibtvProject, ep: int, storyboard: Dict) -> int:
    """把分镜文档的段清单落成 `hibtv_segments` 行（分镜作业与就地改镜头都走这里）。"""
    rows: List[Dict] = []
    for ep_doc in storyboard.get("episodes") or []:
        if int(ep_doc.get("ep") or 0) == int(ep):
            rows = ep_doc.get("segments") or []
            break
    existing = {s.seg_id: s for s in db.query(HibtvSegment).filter(
        HibtvSegment.project_id == project.id, HibtvSegment.ep == int(ep)).all()}
    count = 0
    for i, seg in enumerate(rows):
        seg_id = str(seg.get("id") or "").strip()
        if not seg_id:
            continue
        obj = existing.get(seg_id)
        if obj is None:
            obj = HibtvSegment(project_id=project.id, ep=int(ep), seg_id=seg_id, idx=i,
                               status="none", versions="[]")
            db.add(obj)
        obj.data = jdumps(seg)
        obj.idx = i
        obj.updated_at = now()
        count += 1
    # 文档里已经没有的段：只清理从未出过片的孤儿行，避免旧段残留到段页/合成清单
    keep = {str(seg.get("id") or "").strip() for seg in rows}
    for seg_id, obj in existing.items():
        if seg_id in keep:
            continue
        if obj.status != "none" or (jloads(obj.versions, []) or []):
            continue
        db.delete(obj)
    db.flush()
    return count


# ------------------------------------------------------------------ 出图作业
def _aspect_canvas(project: HibtvProject) -> tuple:
    if (project.aspect_ratio or "").startswith("16:9"):
        return (int(settings.hibtv_image_height), int(settings.hibtv_image_width))
    return (int(settings.hibtv_image_width), int(settings.hibtv_image_height))


def _anchor_prompt(asset: HibtvAsset) -> str:
    data = jloads(asset.data, {})
    image = data.get("image") or {}
    prompt = (image.get("prompt") or data.get("prompt") or "").strip()
    if not prompt:
        summary = (data.get("summary") or "").strip()
        prompt = (f"{asset.name}，{summary}，当代中国都市写实风格。" if summary
                  else f"{asset.name}，当代中国都市写实风格。")
    return prompt


def view_hint(asset: HibtvAsset, view: str) -> tuple:
    """这一视图的生效约束：(文案, 是否用户自定义)。用户改过的 hint 优先于默认约束。"""
    data = jloads(asset.data, {})
    custom = ((data.get("hints") or {}).get(view) or "").strip()
    if custom:
        return custom[:400], True
    defs = VIEW_DEFS.get(asset.kind, [["front", "正面"]])
    if view == defs[0][0]:
        # 锚点正文里已经写了人物/空间本身，再叠默认视角约束会自相矛盾
        return "", False
    by_kind = VIEW_HINT.get(asset.kind) or {}
    return by_kind.get(view) or VIEW_HINT_ANY, False


def _view_prompt(asset: HibtvAsset, view: str) -> str:
    """视图提示词：锚点正文 + 视角约束（默认或用户改写）+ 一句「只改视角」收尾。"""
    base = _anchor_prompt(asset)
    hint, _custom = view_hint(asset, view)
    if not hint:
        return base
    tail = {
        "cast": "以参考图为准，必须看起来是同一个人（五官、发型、体型一致），只改变取景。",
        "scene": "以参考图为同一个空间，只改变机位与朝向，家具陈设的位置与材质不变。",
        "prop": "以参考图为同一件道具，只改变观察角度，材质、颜色与比例不变。",
    }.get(asset.kind, "以参考图为准，只改变视角。")
    return f"{base}；{hint}。{tail}"


def _register_upload(db: Session, user_id: int, path: Path) -> Optional[int]:
    """把出图产物登记成 Upload（可供 Task.ref_image_ids 直接复用）。"""
    try:
        up = Upload(user_id=user_id, slot="reference", filename=path.name,
                    path=str(path), md5="", size=path.stat().st_size if path.exists() else 0)
        db.add(up)
        db.flush()
        return up.id
    except Exception:  # noqa: BLE001
        logger.warning("短剧产物登记 Upload 失败（不影响出图）", exc_info=True)
        return None


def view_negative(kind: str, view: str) -> str:
    """视图专属反向词 + 通用反向词（背面禁脸、侧面禁止面对镜头、道具禁手）。"""
    by_kind = VIEW_NEGATIVE.get(kind) or {}
    return f"{NEGATIVE_BASE}, {by_kind.get(view) or by_kind.get('_any') or ''}".rstrip(", ")


async def _draw_view(db: Session, project: HibtvProject, asset: HibtvAsset, view: str,
                     *, ref_view: Optional[str] = None) -> Dict:
    views = jloads(asset.views, {})
    defs = VIEW_DEFS.get(asset.kind, [["front", "正面"]])
    anchor = defs[0][0]
    width, height = _aspect_canvas(project)
    out_rel = f"assets/{asset.kind}/{asset.code}/{view}.png"
    out_abs = project_dir(project.id) / out_rel
    ref_rel = ""
    if view != anchor:
        ref_rel = (views.get(ref_view or anchor) or {}).get("path") or ""
    ref_abs = abs_path(project.id, ref_rel) if ref_rel else None
    if ref_abs is not None and not ref_abs.exists():
        ref_abs = None
    prompt = _view_prompt(asset, view)
    result = await hibtv_image.generate_image(
        prompt=prompt, out_path=out_abs, ref_path=ref_abs, width=width, height=height,
        negative=view_negative(asset.kind, view))
    upload_id = None
    if out_abs.exists():
        upload_id = _register_upload(db, project.user_id, out_abs)
    version = int((views.get(view) or {}).get("version") or 0) + 1
    views[view] = {
        "path": out_rel, "upload_id": upload_id, "version": version,
        "uploaded": False, "stale": False,
        "at": now().strftime("%Y-%m-%d %H:%M"),
    }
    asset.views = jdumps(views)
    asset.version = max(int(asset.version or 0), version)
    asset.updated_at = now()
    return views[view]


def sheet_prompt(asset: HibtvAsset) -> str:
    """多视图设定稿的提示词：主体描述 + 版式模板 + 用户改图要求。"""
    spec = SHEET_SPEC.get(asset.kind)
    if not spec:
        return _anchor_prompt(asset)
    base = _anchor_prompt(asset).rstrip("。 ")
    custom = ((jloads(asset.data, {}).get("hints") or {}).get("__sheet") or "").strip()
    tail = f"；{custom[:400]}" if custom else ""
    return f"{base}。{SHEET_PROMPT[asset.kind]}{tail}"


def sheet_negative(kind: str) -> str:
    """设定稿反向词：禁“每格不同人 / 格子数不对 / 文字边框”，不禁多视图。"""
    return SHEET_NEGATIVE.get(kind, SHEET_NEGATIVE.get("cast", ""))


def _crop_sheet(sheet_abs: Path, kind: str, out_dir: Path) -> Dict[str, Path]:
    """把设定稿按比例矩形裁成格子（不依赖模型画的线，也允许列宽不等分）。缺 Pillow 时返回空。"""
    spec = SHEET_SPEC.get(kind)
    if not spec or not sheet_abs.exists():
        return {}
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover —— 线上 requirements 已装 Pillow
        logger.warning("未安装 Pillow，设定稿不做裁切（只能看整张）")
        return {}
    out: Dict[str, Path] = {}
    with Image.open(sheet_abs) as im:
        im = im.convert("RGB")
        width, height = im.size
        for view, x, y, w, h in spec["cells"]:
            # 比例 → 像素：模型没按 16:9 出图时也照样能裁（用实际尺寸换算）
            box = (max(0, round(x * width)), max(0, round(y * height)),
                   min(width, round((x + w) * width)), min(height, round((y + h) * height)))
            if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                logger.warning("设定稿 %s 的 %s 格尺寸异常，跳过裁切：%s", kind, view, box)
                continue
            dst = out_dir / f"{view}.png"
            im.crop(box).save(dst, "PNG")
            out[view] = dst
    return out


def sheet_size() -> tuple:
    """设定稿画幅：默认 Qwen-Image-2.1 官方 16:9 原生 2K（2752×1536）。

    低于推荐值等于把每格细节再降一档，所以默认取满；显存不够可在 env 调小
    （HIBTV_SHEET_WIDTH / HIBTV_SHEET_HEIGHT），裁切按实际尺寸自适应。
    """
    w = int(getattr(settings, "hibtv_sheet_width", 0) or SHEET_W)
    h = int(getattr(settings, "hibtv_sheet_height", 0) or SHEET_H)
    return w, h


def _apply_sheet(db: Session, project: HibtvProject, asset: HibtvAsset,
                 sheet_abs: Path, rel: str, *, uploaded: bool = False) -> Dict:
    """设定稿落库 + 重裁全部视图：LLM 出图与手工上传走同一条路径。

    版本号只在设定稿上递增，每个格子版本跟随设定稿——所以不存在“只改了一张派生图”
    这种状态，要改就重出/重传整张，四/五格一起变。
    """
    views = jloads(asset.views, {})
    spec = SHEET_SPEC.get(asset.kind) or {"cells": []}
    upload_id = _register_upload(db, project.user_id, sheet_abs) if sheet_abs.exists() else None
    version = int((views.get("__sheet") or {}).get("version") or 0) + 1
    stamp = now().strftime("%Y-%m-%d %H:%M")
    views["__sheet"] = {
        "path": rel, "upload_id": upload_id, "version": version, "at": stamp,
        "uploaded": uploaded, "stale": False, "cells": [c[0] for c in spec["cells"]],
    }
    cells = _crop_sheet(sheet_abs, asset.kind, sheet_abs.parent) if sheet_abs.exists() else {}
    folder = rel.rsplit("/", 1)[0]
    for view, _label in VIEW_DEFS.get(asset.kind, []):
        cell = cells.get(view)
        if cell is not None and cell.exists():
            views[view] = {
                "path": f"{folder}/{view}.png",
                "upload_id": _register_upload(db, project.user_id, cell),
                "version": version, "uploaded": uploaded, "stale": False,
                "fromSheet": True, "at": stamp,
            }
        elif (views.get(view) or {}).get("path"):
            # 裁切不可用（缺 Pillow / 落盘失败）：旧格子标过期，不假装同步
            views[view]["stale"] = True
    asset.views = jdumps(views)
    asset.version = max(int(asset.version or 0), version)
    asset.updated_at = now()
    reconcile_status(asset)
    return views["__sheet"]


async def _draw_sheet(db: Session, project: HibtvProject, asset: HibtvAsset) -> Dict:
    """一次出整张多视图设定稿，再裁成每个视图（改主图 = 四/五张同步）。"""
    views = jloads(asset.views, {})
    spec = SHEET_SPEC.get(asset.kind)
    if spec is None:
        anchor = VIEW_DEFS.get(asset.kind, [["front"]])[0][0]
        return await _draw_view(db, project, asset, anchor)
    rel = f"assets/{asset.kind}/{asset.code}/sheet.png"
    out_abs = project_dir(project.id) / rel
    # 重出时把上一版设定稿当参考图：只换版式/补细节，不换人
    prev_rel = (views.get("__sheet") or {}).get("path") or ""
    ref_abs = abs_path(project.id, prev_rel) if prev_rel else None
    if ref_abs is not None and not ref_abs.exists():
        ref_abs = None
    await hibtv_image.generate_image(
        prompt=sheet_prompt(asset), out_path=out_abs, ref_path=ref_abs,
        width=sheet_size()[0], height=sheet_size()[1],
        negative=sheet_negative(asset.kind), keep_adv_negative=False)
    return _apply_sheet(db, project, asset, out_abs, rel)


def import_sheet(db: Session, project: HibtvProject, asset: HibtvAsset,
                 sheet_abs: Path, rel: str) -> Dict:
    """手工上传一张多视图设定稿（外部出图 / 人工修图）后重裁全部视图。"""
    return _apply_sheet(db, project, asset, sheet_abs, rel, uploaded=True)


def mark_derived_stale(asset: HibtvAsset) -> None:
    views = jloads(asset.views, {})
    defs = VIEW_DEFS.get(asset.kind, [["front", "正面"]])
    anchor = defs[0][0]
    for view, meta in views.items():
        if view != anchor and meta and meta.get("path"):
            meta["stale"] = True
    asset.views = jdumps(views)


def _asset_query(db: Session, project: HibtvProject, payload: Dict):
    kind = str(payload.get("kind") or "")
    codes = payload.get("codes") or ([payload.get("code")] if payload.get("code") else [])
    asset_id = payload.get("asset_id")
    query = db.query(HibtvAsset).filter(HibtvAsset.project_id == project.id)
    if kind:
        query = query.filter(HibtvAsset.kind == kind)
    if asset_id:
        query = query.filter(HibtvAsset.id == int(asset_id))
    if codes:
        query = query.filter(HibtvAsset.code.in_(list(codes)))
    return query.order_by(HibtvAsset.kind, HibtvAsset.sort)


def image_steps(db: Session, project: HibtvProject, payload: Dict) -> int:
    """预估一次出图作业的图片张数（用于提交时一次性预扣积分）。"""
    assets = _asset_query(db, project, payload).all()
    if not assets:
        return 0
    mode = str(payload.get("mode") or "anchor")
    steps = 0
    for asset in assets:
        views = jloads(asset.views, {})
        defs = [v[0] for v in VIEW_DEFS.get(asset.kind, [["front"]])]
        if mode == "sheet":
            steps += 1        # 一张设定稿 = 一张图（不再按视图张数扣）
        elif mode == "anchor":
            steps += 1
        elif mode == "all":
            steps += len([v for v in defs
                          if not ((views.get(v) or {}).get("path")
                                  and not (views.get(v) or {}).get("stale"))])
        else:
            steps += len(payload.get("views") or defs[1:])
    return max(0, steps)


async def _job_images(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    payload = jloads(job.payload, {})
    mode = str(payload.get("mode") or "anchor")
    assets = _asset_query(db, project, payload).all()
    if not assets:
        raise RuntimeError("没有可出图的资产")

    if mode == "sheet":
        # 一张多视图设定稿：出图 1 张、裁切 N 格，所以进度按资产算而不是按视图算
        total = max(1, len(assets))
        for i, asset in enumerate(assets, start=1):
            asset.status = "drawing"
            db.commit()
            _progress(db, job, int(5 + 90 * i / total), f"{asset.code} {asset.name} · 多视图设定稿")
            await _draw_sheet(db, project, asset)
            reconcile_status(asset)
            db.commit()
        return

    total_steps = 0
    for asset in assets:
        views = jloads(asset.views, {})
        defs = [v[0] for v in VIEW_DEFS.get(asset.kind, [["front"]])]
        if mode == "anchor":
            total_steps += 1
        elif mode == "all":
            total_steps += len([v for v in defs if not ((views.get(v) or {}).get("path") and not (views.get(v) or {}).get("stale"))])
        else:
            total_steps += len(payload.get("views") or defs)
    total_steps = max(1, total_steps)

    done = 0
    for asset in assets:
        defs = [v[0] for v in VIEW_DEFS.get(asset.kind, [["front"]])]
        anchor = defs[0]
        views = jloads(asset.views, {})
        targets: List[str]
        if mode == "anchor":
            targets = [anchor]
        elif mode == "views":
            targets = list(payload.get("views") or defs[1:])
        else:  # all
            targets = [v for v in defs
                       if not ((views.get(v) or {}).get("path") and not (views.get(v) or {}).get("stale"))]
            if anchor not in targets:
                targets.insert(0, anchor)
        if not targets:
            continue
        asset.status = "drawing"
        db.commit()
        for view in targets:
            done += 1
            _progress(db, job, int(5 + 90 * done / total_steps),
                      f"{asset.code} {asset.name} · {dict(VIEW_DEFS.get(asset.kind, [])).get(view, view)}")
            await _draw_view(db, project, asset, view)
            if view == anchor:
                mark_derived_stale(asset)
            db.commit()
        all_views = jloads(asset.views, {})
        has_anchor = bool((all_views.get(anchor) or {}).get("path"))
        every = all(bool((all_views.get(v) or {}).get("path")) for v in defs)
        asset.status = "done" if (has_anchor and every) else ("review" if has_anchor else "empty")
        asset.updated_at = now()
        db.commit()


def reconcile_status(asset: HibtvAsset) -> None:
    """按已出的视图重算状态（中断/失败后不留「出图中」）。"""
    defs = [v[0] for v in VIEW_DEFS.get(asset.kind, [["front"]])]
    views = jloads(asset.views, {})
    has_anchor = bool((views.get(defs[0]) or {}).get("path"))
    every = all(bool((views.get(v) or {}).get("path")) for v in defs)
    asset.status = "done" if (has_anchor and every) else ("review" if has_anchor else "empty")
    asset.updated_at = now()


def reset_drawing(db: Session, project: HibtvProject) -> None:
    """把停在 drawing 的资产收敛回真实状态（出图作业失败时调）。"""
    rows = (db.query(HibtvAsset)
            .filter(HibtvAsset.project_id == project.id, HibtvAsset.status == "drawing")
            .all())
    for asset in rows:
        reconcile_status(asset)
    if rows:
        db.commit()


# ------------------------------------------------------------------ 拼接作业
def snap_duration(seconds: float) -> int:
    cap = int(settings.hibtv_seg_max_seconds)
    allowed = [d for d in (5, 8, 10, 15) if d <= cap] or [5]
    need = float(seconds or 0)
    for d in allowed:
        if d >= need:
            return d
    return allowed[-1]


async def _job_compose(db: Session, project: HibtvProject, job: HibtvJob) -> None:
    payload = jloads(job.payload, {})
    ep = int(payload.get("ep") or job.ep or 1)
    segs = (db.query(HibtvSegment)
            .filter(HibtvSegment.project_id == project.id, HibtvSegment.ep == ep)
            .order_by(HibtvSegment.idx).all())
    paths: List[str] = []
    planned: List[float] = []
    for seg in segs:
        versions = jloads(seg.versions, [])
        cur = None
        for v in versions:
            if int(v.get("v") or 0) == int(seg.current_version or 0):
                cur = v
                break
        if cur is None and versions:
            cur = versions[-1]
        if not cur or not cur.get("path"):
            raise RuntimeError(f"{seg.seg_id} 还没有成片段，先出片再拼接")
        paths.append(cur["path"] if Path(cur["path"]).is_absolute()
                     else str(abs_path(project.id, cur["path"])))
        data = jloads(seg.data, {})
        planned.append(round(sum(float(c.get("seconds") or 0) for c in (data.get("cuts") or [])), 2))
    _progress(db, job, 40, f"拼接 {len(paths)} 段")
    prev_max = db.query(HibtvRender).filter(
        HibtvRender.project_id == project.id, HibtvRender.ep == ep).count()
    result = hibtv_compose.render_episode(
        project_id=project.id, ep=ep, segment_paths=paths, planned=planned,
        version=prev_max + 1)
    rel = rel_path(project.id, Path(result["path"]))
    render = HibtvRender(project_id=project.id, ep=ep, path=rel,
                         duration_sec=float(result["duration_sec"]), size=int(result["size"]),
                         cutlist=result["cutlist"], version=result["version"])
    db.add(render)
    db.commit()


# ------------------------------------------------------------------ 段与版本
TASK_DONE = "done"
TASK_DEAD = ("failed", "cancelled")


def segment_payload(project_id: int, seg: HibtvSegment) -> Dict:
    """段的对外视图：含版本表、当前版本、视频 URL 与切镜计划秒数。"""
    data = jloads(seg.data, {})
    versions = jloads(seg.versions, [])
    current = None
    for v in versions:
        if int(v.get("v") or 0) == int(seg.current_version or 0):
            current = v
            break
    if current is None and versions:
        current = versions[-1]
    out_versions: List[Dict] = []
    for v in versions:
        item = dict(v)
        rel = item.get("path") or ""
        item["url"] = f"/files/hibtv/{project_id}/{rel}" if rel else ""
        out_versions.append(item)
    cur_url = ""
    if current is not None:
        rel = current.get("path") or ""
        cur_url = f"/files/hibtv/{project_id}/{rel}" if rel else ""
    cuts = data.get("cuts") or []
    return {
        "id": seg.seg_id, "ep": seg.ep, "idx": seg.idx,
        "status": seg.status, "currentVersion": seg.current_version,
        "versions": out_versions, "url": cur_url,
        "data": data, "h3Prompt": data.get("h3Prompt") or "", "cuts": cuts,
        "planSeconds": round(sum(float(c.get("seconds") or 0) for c in cuts), 2),
    }


def sync_segment_tasks(db: Session, project: HibtvProject, ep: int) -> None:
    """拉取式同步：把 Task 的成片回写进段版本（不新开常驻循环）。"""
    segs = (db.query(HibtvSegment)
            .filter(HibtvSegment.project_id == project.id, HibtvSegment.ep == int(ep))
            .order_by(HibtvSegment.idx).all())
    changed = False
    for seg in segs:
        versions = jloads(seg.versions, [])
        running = False
        dead = False
        for v in versions:
            if v.get("path"):
                continue
            tid = v.get("task_id")
            if not tid:
                continue
            task = db.get(Task, int(tid))
            if task is None:
                continue
            if task.status == TASK_DONE and task.video_path:
                p = Path(task.video_path)
                suffix = p.suffix or ".mp4"
                rel = collect_media(
                    project.id, f"segments/{seg.seg_id}-v{int(v.get('v') or 0)}{suffix}", p)
                if not rel:
                    v["failed"] = True
                    v["error"] = "成片文件丢失，请重新生成这一段"
                    dead = True
                    changed = True
                    continue
                v["path"] = rel
                v["seconds"] = round(float(task.duration or 0), 2)
                v["at"] = now().strftime("%Y-%m-%d %H:%M")
                v.pop("failed", None)
                v.pop("error", None)
                seg.status = "done"
                if not int(seg.current_version or 0):
                    seg.current_version = int(v.get("v") or 0)
                changed = True
            elif task.status in TASK_DEAD:
                v["failed"] = True
                v["error"] = (task.error or task.status)[:200]
                dead = True
                changed = True
            else:
                running = True
        if not any(v.get("path") for v in versions):
            seg.status = "running" if running else ("failed" if dead else "none")
        else:
            seg.status = "running" if running else "done"
        if changed:
            seg.versions = jdumps(versions)
            seg.updated_at = now()
    if changed:
        db.commit()


# 段级出片的参考图约定：挂上去的是设定稿（一张里并排 3~4 个视角），
# 视频模型很容易把整张版式复刻成九宫格，或把一格当成一个人。
# 不改镜头正文结构，只在尾部追加“怎么用参考图”。
REF_GUARD = (
    "参考图约定：随附图片是角色、场景、道具的设定图（同一张里并排多个视角），"
    "只作为外观证据——照着确认脸、发型、体型、服装、配色、材质与空间结构；"
    "不要假设固定的分格数量或顺序，不要复刻它的分格排版，不要把同一角色画成多个人，不要输出九宫格或拼贴；"
    "景别、机位、构图与动作以上面的镜头描述为准。"
)


def video_prompt(db: Session, project: HibtvProject, seg: HibtvSegment,
                 prompt: str = "", ref_ids: Optional[List[int]] = None) -> str:
    """出片提示词：挂了参考图就追加「只当外观证据、不复刻分格版式」。"""
    text = (prompt or "").strip()
    if not text or REF_GUARD in text:
        return text
    ids = ref_ids if ref_ids is not None else segment_ref_ids(db, project, seg)
    if not ids:
        return text
    return f"{text}\n{REF_GUARD}"


def segment_ref_ids(db: Session, project: HibtvProject, seg: HibtvSegment) -> List[int]:
    """段的参考图：场景锚点 + 出场角色锚点 + 道具锚点（≤ 9，各取第一个有图的视图）。"""
    data = jloads(seg.data, {})
    outline_doc = get_doc(db, project.id, "outline")
    outline = jloads(outline_doc.data, {}) if outline_doc else {}
    scenes = outline.get("scenes") or []
    pairs: List[tuple] = []
    si = int(data.get("sceneIndex") or 0)
    if 0 < si <= len(scenes):
        pairs.append(("scene", scenes[si - 1].get("id") or ""))
    codes: List[str] = []
    for cut in data.get("cuts") or []:
        for code in (cut.get("characters") or []):
            if code and code not in codes:
                codes.append(code)
    for code in codes[:6]:
        pairs.append(("cast", code))
    prop_codes: List[str] = []
    for cut in data.get("cuts") or []:
        for code in (cut.get("props") or []):
            if code and code not in prop_codes:
                prop_codes.append(code)
    for code in prop_codes[:3]:
        pairs.append(("prop", code))

    ids: List[int] = []
    for kind, code in pairs:
        if not code or len(ids) >= 9:
            continue
        asset = (db.query(HibtvAsset)
                 .filter(HibtvAsset.project_id == project.id, HibtvAsset.kind == kind,
                         HibtvAsset.code == code)
                 .first())
        if asset is None:
            continue
        views = jloads(asset.views, {})
        # 多视图设定稿：整张当 1 个参考（含多个视角），不拆成多张占预算
        sheet_uid = (views.get("__sheet") or {}).get("upload_id")
        if sheet_uid and int(sheet_uid) not in ids:
            ids.append(int(sheet_uid))
            continue
        order = [v[0] for v in VIEW_DEFS.get(kind, [["front"]])]
        for view in order:
            uid = (views.get(view) or {}).get("upload_id")
            if uid and int(uid) not in ids:
                ids.append(int(uid))
                break
    return ids[:9]
