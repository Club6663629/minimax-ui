"""短剧 AI 辅助底座：文本/视觉双通道 + provider 交叉兜底 + JSON 校验 + 缓存/限流/记账。

与 advllm.py / videollm.py 同一套 provider 事实：
- qwen（qwen3.8-flash）：关思考用请求体顶层 enable_thinking=false；
- deepseek（deepseek-flash）：关思考用请求体顶层 thinking={"type":"disabled"}；
- 图片以 base64 image_url 内容块**只放 user 消息**（system/assistant 带图会 400）。

约定：
- 失败一律抛 DramaAgentError，由调用方回退本地模板/原值，不向前端抛 5xx；
- 缓存与限流是进程内实现，多 worker 部署时按实例计（够用且无额外依赖）；
- 图片超过 2.5MB 时先用 ffmpeg 缩到长边 1280 再编码，控制请求体积。
"""
import base64
import hashlib
import json
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

PROVIDERS = ("qwen", "deepseek")
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
_MAX_IMAGE_BYTES = 2_500_000
_OFF_VALUES = ("off", "local", "none", "")


class DramaAgentError(Exception):
    """AI 辅助不可用（无 key / 网络 / 空输出 / 非法 JSON / 限流）。"""


# ---------------------------------------------------------------- provider
def _conf(provider: str) -> Dict[str, str]:
    if provider == "qwen":
        return {
            "key": str(settings.qwen_api_key or ""),
            "base": str(settings.qwen_api_base or "").rstrip("/"),
            "model": str(settings.qwen_llm_model or "qwen3.8-flash"),
        }
    return {
        "key": str(settings.deepseek_api_key or ""),
        "base": str(settings.deepseek_api_base or "").rstrip("/"),
        "model": str(settings.deepseek_llm_model or "deepseek-flash"),
    }


def _master_off() -> bool:
    """drama_llm_provider=off 是短剧 AI 总闸（smoke / 无 key 联调用）。"""
    return str(settings.drama_llm_provider or "").strip().lower() in _OFF_VALUES


def _preferred(use_vision: bool) -> str:
    raw = settings.drama_ai_vision_provider if use_vision else settings.drama_ai_text_provider
    name = str(raw or "").strip().lower()
    return name if name in PROVIDERS else PROVIDERS[0]


def candidates(use_vision: bool) -> List[str]:
    """首选 provider 优先，另一家兜底；只保留配了 key 的。"""
    if _master_off():
        return []
    first = _preferred(use_vision)
    order = [first] + [p for p in PROVIDERS if p != first]
    return [p for p in order if _conf(p)["key"]]


def available(use_vision: bool = False) -> bool:
    return bool(candidates(use_vision))


def provider_label(use_vision: bool = False) -> str:
    """给前端展示的模型名（首个可用 provider）。"""
    for name in candidates(use_vision):
        return _conf(name)["model"]
    return ""


# ---------------------------------------------------------------- 缓存 / 限流
_CACHE: Dict[str, Tuple[float, dict]] = {}
_RATE: Dict[int, List[float]] = {}


def _cache_key(kind: str, salt: str) -> str:
    return hashlib.sha1(f"{kind}|{salt}".encode("utf-8")).hexdigest()


def _cache_get(key: str) -> Optional[dict]:
    item = _CACHE.get(key)
    if not item:
        return None
    expire, payload = item
    if expire < time.time():
        _CACHE.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload: dict) -> None:
    ttl = max(0, int(settings.drama_ai_cache_ttl or 0))
    if ttl <= 0:
        return
    if len(_CACHE) > 512:  # 简单上限，避免长时间运行后无界增长
        now = time.time()
        for k in [k for k, (exp, _) in _CACHE.items() if exp < now][:256]:
            _CACHE.pop(k, None)
    _CACHE[key] = (time.time() + ttl, payload)


def _guard_rate(user_id: int) -> None:
    limit = max(1, int(settings.drama_ai_rate_per_hour or 60))
    now = time.time()
    bucket = [t for t in _RATE.get(user_id, []) if now - t < 3600]
    if len(bucket) >= limit:
        raise DramaAgentError(f"AI 调用过于频繁（每小时 {limit} 次），请稍后再试")
    bucket.append(now)
    _RATE[user_id] = bucket


# ---------------------------------------------------------------- 图片
def _shrink(path: Path) -> Optional[Path]:
    """超过体积上限时缩到长边 1280；失败返回原图（由调用方按体积判断是否放弃）。"""
    out = Path(tempfile.mkdtemp(prefix="drama_ai_img_")) / f"{path.stem}.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(path),
             "-vf", r"scale=min(1280\,iw):-2", "-q:v", "3", str(out)],
            check=True, timeout=60, capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("AI 图片缩放失败（改用原图）: %s", exc)
        return None
    return out if out.exists() and out.stat().st_size > 0 else None


def encode_images(paths: Optional[Sequence], cap: Optional[int] = None) -> List[Dict[str, str]]:
    """本地图片 → [{"b64","mime"}]；不存在/超大且缩放失败则跳过。"""
    limit = max(0, int(cap if cap is not None else settings.drama_ai_max_images or 4))
    out: List[Dict[str, str]] = []
    for raw in list(paths or [])[:limit]:
        path = Path(str(raw))
        if not path.exists():
            logger.warning("AI 参考图不存在，跳过：%s", path)
            continue
        if path.stat().st_size > _MAX_IMAGE_BYTES:
            path = _shrink(path) or path
            if path.stat().st_size > _MAX_IMAGE_BYTES:
                logger.warning("AI 参考图过大且无法压缩，跳过：%s", path)
                continue
        mime = _MIME.get(path.suffix.lower(), "image/jpeg")
        out.append({"b64": base64.b64encode(path.read_bytes()).decode("ascii"), "mime": mime})
    return out


# ---------------------------------------------------------------- JSON
def extract_json(raw: str) -> dict:
    """从模型输出提取第一个 JSON 对象（容忍 ``` 围栏与前后说明）。"""
    text = (raw or "").strip()
    if not text:
        raise DramaAgentError("模型输出为空")
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise DramaAgentError("模型输出不含 JSON 对象")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise DramaAgentError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise DramaAgentError("模型输出的 JSON 不是对象")
    return data


# ---------------------------------------------------------------- 请求
def _build_body(provider: str, system: str, user: str, images: List[Dict[str, str]],
                max_tokens: int, temperature: float, with_format: bool) -> dict:
    if images:
        content: List[dict] = [{"type": "text", "text": user}]
        detail = str(getattr(settings, "advideo_llm_vision_detail", "high") or "high")
        for im in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{im['mime']};base64,{im['b64']}", "detail": detail},
            })
        user_msg: dict = {"role": "user", "content": content}
    else:
        user_msg = {"role": "user", "content": user}
    body = {
        "model": _conf(provider)["model"],
        "messages": [{"role": "system", "content": system}, user_msg],
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
        "stream": False,
    }
    if with_format:
        body["response_format"] = {"type": "json_object"}
    # 关思考：qwen 用 enable_thinking=false；deepseek 用 thinking.type=disabled
    if provider == "qwen":
        body["enable_thinking"] = False
    else:
        body["thinking"] = {"type": "disabled"}
    return body


def _post(provider: str, body: dict) -> dict:
    conf = _conf(provider)
    if not conf["key"] or not conf["base"]:
        raise DramaAgentError(f"{provider} 未配置 API Key 或 Base")
    timeout = float(settings.drama_ai_timeout or 60)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{conf['base']}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {conf['key']}", "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DramaAgentError(f"{provider} 请求失败: {exc}") from exc


def _request(provider: str, system: str, user: str, images: List[Dict[str, str]],
             max_tokens: int, temperature: float) -> Tuple[dict, dict, str]:
    # 个别模型/视觉组合不支持 response_format：带格式失败一次后去掉重试
    try:
        data = _post(provider, _build_body(provider, system, user, images, max_tokens, temperature, True))
    except DramaAgentError:
        data = _post(provider, _build_body(provider, system, user, images, max_tokens, temperature, False))
    try:
        raw = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    except (AttributeError, IndexError, TypeError) as exc:
        raise DramaAgentError(f"{provider} 响应结构异常: {exc}") from exc
    usage = data.get("usage") or {}
    return extract_json(raw if isinstance(raw, str) else ""), usage, _conf(provider)["model"]


def chat_json(
    *,
    kind: str,
    system: str,
    user: str,
    images: Optional[Sequence] = None,
    user_id: int = 0,
    max_tokens: Optional[int] = None,
    temperature: float = 0.4,
    cache_salt: str = "",
    force: bool = False,
) -> dict:
    """调模型并把输出解析为 JSON 对象；失败抛 DramaAgentError。

    kind: 调用用途（日志/缓存维度），如 rewrite_shot / precheck；
    images: 本地图片路径列表（视觉通道，最多 drama_ai_max_images 张）；
    cache_salt: 相同输入的去重因子（一般传 spec_hash 或内容哈希）；
    force: 跳过缓存（预检“重新检查”用）。
    """
    imgs = encode_images(images) if images else []
    providers = candidates(bool(imgs))
    if not providers:
        raise DramaAgentError("未配置可用的 AI 模型（QWEN_API_KEY / DEEPSEEK_API_KEY）")
    _guard_rate(user_id)
    key = _cache_key(kind, cache_salt or user)
    if not force:
        hit = _cache_get(key)
        if hit is not None:
            return {**hit, "cached": True}

    tokens = int(max_tokens or settings.drama_ai_max_tokens or 1600)
    last: Optional[Exception] = None
    for provider in providers:
        try:
            data, usage, model = _request(provider, system, user, imgs, tokens, temperature)
        except DramaAgentError as exc:
            last = exc
            logger.warning("短剧 AI[%s] provider=%s 失败，尝试兜底：%s", kind, provider, exc)
            continue
        payload = {
            "data": data, "provider": provider, "model": model,
            "usage": usage, "cached": False,
        }
        _cache_put(key, payload)
        logger.info(
            "短剧 AI[%s] provider=%s model=%s images=%d tokens=%s",
            kind, provider, model, len(imgs), usage.get("total_tokens", "?"),
        )
        return payload
    raise DramaAgentError(str(last) if last else "AI 调用失败")
