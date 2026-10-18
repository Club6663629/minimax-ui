"""广告片「确认候选图」公共逻辑：人工确认路由 与 自动模式 共用，避免两条路径漂移。

- 只改字段/状态，**不 commit**（由调用方在同一事务里提交）；
- 不含鉴权、不含积分预检：视频费仍在生成领取时（worker._claim_generate）扣除。
"""
import json
from typing import Optional

from ..models import Task


def candidate_paths(task: Task) -> list:
    """候选广告图本地路径列表（脏数据一律返回空列表）。"""
    try:
        paths = json.loads(getattr(task, "ad_image_paths", "") or "[]")
    except (TypeError, ValueError):
        return []
    return [p for p in paths if isinstance(p, str)]


def apply_confirm(
    task: Task,
    image_index: int,
    *,
    video_prompt: Optional[str] = None,
    enhance: Optional[bool] = None,
    video_prompt_mode: Optional[str] = None,
) -> int:
    """把任务从「候选图已就绪」推进到视频阶段（status=queued）。

    返回实际选中的序号（入参越界会被吸附到合法范围）。调用方负责校验状态/素材并 db.commit()。
    与 POST /api/videos/{id}/confirm-image 的行为完全一致——自动模式只是把同一步骤自动化。
    """
    paths = candidate_paths(task)
    if not paths:
        raise ValueError("候选广告图不存在")
    idx = max(0, min(int(image_index), len(paths) - 1))
    task.chosen_image = paths[idx]
    task.chosen_index = idx
    if video_prompt and video_prompt.strip():
        task.prompt = video_prompt.strip()[:2000]
    if enhance is not None:
        task.enhance = bool(enhance)
    if video_prompt_mode:
        # advideo9：生视频增强路由（llm 主方案 / content_ir 备选 / local 本地规则）
        task.video_prompt_mode = video_prompt_mode
        if video_prompt_mode == "local":
            task.enhance = True   # A 臂也要进增强阶段（跳过云端 IR，仅本地规则，0 付费）
    task.status = "queued"          # 复用既有生成循环（enhance → 768p → 超分）
    task.error = ""
    task.worker_url = ""
    task.started_at = None
    task.finished_at = None
    return idx
