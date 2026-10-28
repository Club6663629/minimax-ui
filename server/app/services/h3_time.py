"""H3 时长 ↔ 帧数换算（模型硬约束，与 r2v_api.json 节点 131 同一算法）。

模板节点的算法是
    max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17
其中 a 是请求的秒数，24 是 fps，17 是 H3 的帧步长。它把任意秒数**向上吸附**到
合法的 `5 + 17n` 帧，所以：

- 直接传 13 秒 → round(312) = 312 → 312 % 17 = 6 → +11 → **328 帧 = 13.67 秒**（超限）；
- 想要「不超过 13 秒」必须传 **311 帧 / 24 = 12.9583 秒**（311 = 5 + 18×17）。

本模块只做算术，不碰 ComfyUI；出片侧一律用 `seconds_for(计划秒数)` 当请求时长。
"""
from typing import Union

FPS = 24
BASE_FRAMES = 5
STEP = 17


def frames_for(seconds: Union[int, float]) -> int:
    """不超过 `seconds` 的最大合法帧数（5+17n）。下限 5 帧。"""
    try:
        need = float(seconds or 0)
    except (TypeError, ValueError):
        need = 0.0
    want = int(need * FPS)
    if want <= BASE_FRAMES:
        return BASE_FRAMES
    n = (want - BASE_FRAMES) // STEP
    return BASE_FRAMES + STEP * max(0, n)


def seconds_for(seconds: Union[int, float]) -> float:
    """把计划秒数吸附到「不会超过它」的合法秒数（帧数 / 24）。"""
    return round(frames_for(seconds) / FPS, 4)


def snap_up(seconds: Union[int, float]) -> float:
    """向上吸附到最近的合法秒数（与模型行为一致，仅用于核对吸附结果）。"""
    try:
        need = float(seconds or 0)
    except (TypeError, ValueError):
        need = 0.0
    want = max(BASE_FRAMES, int(round(need * FPS)))
    if want <= BASE_FRAMES:
        frames = BASE_FRAMES
    else:
        n = -(-(want - BASE_FRAMES) // STEP)   # 向上取整
        frames = BASE_FRAMES + STEP * n
    return round(frames / FPS, 4)


def describe(seconds: Union[int, float]) -> dict:
    """给 UI / 日志用的一行换算说明。"""
    frames = frames_for(seconds)
    return {
        "plan": round(float(seconds or 0), 3),
        "frames": frames,
        "seconds": round(frames / FPS, 4),
        "would_be": snap_up(seconds),
    }
