"""ComfyUI Worker 池：节点注册、心跳自愈、按角色/档位路由（见《H3集群部署方案》§2/§6）。

配置来源：COMFYUI_WORKERS 环境变量，条目间分号分隔，每项 "url|角色|标签"（标签内逗号分隔）。
- 角色：generate（768p 生成）| upscale（SeedVR2 超分）
- 标签：heavy=长片段优先、1k/2k=超分档位、overflow=仅主力全忙时承接 1K、
        unet:<文件名>=超分节点注入的权重、
        unet:<mode族>:<文件名>=生成节点注入的权重（t2v/flf2v→fl2va、r2v→ref2va）、
        engine:seedvr2=原生节点模板 /
        engine:seedvr2_int8=KSampler 管线模板（默认，模板内 UNETLoader 默认 3B FP16）
未配置时退回 COMFYUI_URL 单实例（并发=1），行为与旧版一致。
Mock 模式（MOCK_COMFY=1）下以虚拟节点模拟 4 生成 + 3 超分并发。
"""
import asyncio
import logging
from typing import List, Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 10   # 探活间隔（秒）
FAIL_LIMIT = 3            # 连续失败次数达 3（约 30 秒）即摘除


class WorkerNode:
    """一个 ComfyUI 实例。ComfyUI 单实例串行执行，故并发=1（忙闲位即槽位）。"""

    def __init__(self, url: str, role: str, tags: List[str]):
        self.url = url.rstrip("/")
        self.role = role                    # generate | upscale
        self.tags = set(tags)
        self.healthy = True
        self.consecutive_fails = 0
        self.task_id: Optional[int] = None  # 正在执行的任务（None=空闲）
        self.queue_busy = False             # ComfyUI 真实队列是否忙碌（心跳探测 /queue）

    @property
    def busy(self) -> bool:
        """忙闲 = 本地占位 OR ComfyUI 真实队列有 running/pending 任务。

        后者用于后端重启后感知节点上仍在跑的任务（内存态 task_id 重启即清空，
        但 ComfyUI 队列是持久的），避免把新任务派到已在跑的节点上串行排队。
        """
        return self.task_id is not None or self.queue_busy

    def has_tag(self, tag: str) -> bool:
        return tag in self.tags

    @property
    def unet(self) -> Optional[str]:
        """兼容旧标签 unet:<文件名>（超分等单一权重场景）。"""
        for t in self.tags:
            if t.startswith("unet:"):
                return t.split(":", 1)[1]
        return None

    def unet_for(self, mode: str) -> Optional[str]:
        """按生成 mode 取注入权重：标签 unet:<mode>:<文件名>。

        mode 权重族映射：t2v/flf2v → fl2va，r2v → ref2va。
        未配置对应标签时返回 None（沿用模板默认 int8 权重，A100 场景）。
        """
        family = "ref2va" if mode == "r2v" else "fl2va"
        for t in self.tags:
            if t.startswith(f"unet:{family}:"):
                return t.split(":", 2)[2]
        # 回退：旧式 unet:<文件名> 标签（无 mode 区分）
        return self.unet

    @property
    def engine(self) -> str:
        """超分引擎：标签 engine:<名> 指定，默认 seedvr2_int8（KSampler 管线模板，
        模板内 UNETLoader 默认权重 3B FP16）。"""
        for t in self.tags:
            if t.startswith("engine:"):
                return t.split(":", 1)[1]
        return "seedvr2_int8"

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "role": self.role,
            "tags": sorted(self.tags),
            "healthy": self.healthy,
            "busy": self.busy,
            "task_id": self.task_id,
            "queue_busy": self.queue_busy,
            "consecutive_fails": self.consecutive_fails,
        }


def _parse_workers() -> List[WorkerNode]:
    """解析 COMFYUI_WORKERS；未配置则退回单实例 COMFYUI_URL。"""
    raw = (settings.comfyui_workers or "").strip()
    if not raw:
        return [WorkerNode(settings.comfyui_url, "generate", [])]
    nodes: List[WorkerNode] = []
    for item in raw.split(";"):
        item = item.strip()
        if not item:
            continue
        parts = [p.strip() for p in item.split("|")]
        url = parts[0]
        role = parts[1] if len(parts) > 1 and parts[1] else "generate"
        if role not in ("generate", "upscale"):
            raise ValueError(f"COMFYUI_WORKERS 非法角色: {role}（{item}）")
        tags = [t for t in parts[2].split(",") if t] if len(parts) > 2 else []
        nodes.append(WorkerNode(url, role, tags))
    if not nodes:
        raise ValueError("COMFYUI_WORKERS 解析后为空")
    return nodes


def _mock_workers() -> List[WorkerNode]:
    """Mock 模式：4 生成 + 3 超分虚拟节点，用于无 GPU 环境联调。"""
    nodes = [WorkerNode(f"mock://generate-{i}", "generate", ["heavy"] if i == 0 else [])
             for i in range(4)]
    nodes += [WorkerNode(f"mock://upscale-{i}", "upscale", ["1k", "2k"]) for i in range(3)]
    return nodes


class WorkerPool:
    """全部 ComfyUI 节点的管理器：心跳探活 + 忙闲槽位分配。"""

    def __init__(self) -> None:
        self.mock = settings.mock_comfy
        self.nodes = _mock_workers() if self.mock else _parse_workers()
        self._hb_task: Optional[asyncio.Task] = None
        self._rr_index = 0  # 生成节点轮询指针（round-robin，避免永远优先第一个节点）

    # ---- 生命周期 ----
    async def start(self) -> None:
        if self.mock or self._hb_task is not None:
            return
        self._hb_task = asyncio.get_event_loop().create_task(self._heartbeat_loop())
        logger.info(
            "Worker 池已启动：%d 生成 + %d 超分",
            sum(1 for n in self.nodes if n.role == "generate"),
            sum(1 for n in self.nodes if n.role == "upscale"),
        )

    async def stop(self) -> None:
        if self._hb_task is not None:
            self._hb_task.cancel()
            self._hb_task = None

    async def _heartbeat_loop(self) -> None:
        """每 10s 探活全部节点；连续失败 30s 摘除，恢复后自动回池。"""
        while True:
            try:
                await asyncio.gather(*(self._probe(n) for n in self.nodes))
            except asyncio.CancelledError:
                raise
            except Exception:  # 心跳循环永不退出
                logger.exception("心跳循环异常")
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def _probe(self, node: WorkerNode) -> None:
        was_healthy = node.healthy
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{node.url}/system_stats")
                r.raise_for_status()
                # 同时探测真实队列状态：有 running/pending 即视为忙碌
                try:
                    q = await client.get(f"{node.url}/queue")
                    if q.status_code == 200:
                        data = q.json()
                        node.queue_busy = bool(
                            data.get("queue_running") or data.get("queue_pending")
                        )
                except Exception:
                    pass  # 队列探测失败不影响健康判定
            node.consecutive_fails = 0
            node.healthy = True
        except Exception:  # 网络错误 / 超时 / 非 200 均计为失败
            node.consecutive_fails += 1
            if node.consecutive_fails >= FAIL_LIMIT:
                node.healthy = False
        if was_healthy and not node.healthy:
            logger.warning("Worker 摘除（失联 ≥%ds）: %s", FAIL_LIMIT * HEARTBEAT_INTERVAL, node.url)
        elif not was_healthy and node.healthy:
            logger.info("Worker 恢复: %s", node.url)

    # ---- 槽位分配 ----
    def acquire_generate(self, heavy: bool, task_id: int) -> Optional[WorkerNode]:
        """领取生成节点：时长>10s 的任务优先 heavy 节点（A100），否则任意空闲节点。

        采用 round-robin 轮询：从上次派发位置之后找空闲节点，避免永远优先第一个
        节点（此前 idle[0] 导致 45 永远优先、51/246 空闲）。
        """
        idle = [n for n in self.nodes if n.role == "generate" and n.healthy and not n.busy]
        if not idle:
            return None
        if heavy:
            preferred = [n for n in idle if n.has_tag("heavy")]
            if preferred:
                # heavy 任务仍优先 heavy 节点，但也在 heavy 集合内轮询
                node = self._pick_round_robin(preferred)
                node.task_id = task_id
                return node
        node = self._pick_round_robin(idle)
        node.task_id = task_id
        return node

    def _pick_round_robin(self, candidates: List[WorkerNode]) -> WorkerNode:
        """在候选空闲节点中按轮询指针选一个，并推进指针。"""
        n = len(candidates)
        idx = self._rr_index % n
        self._rr_index = (self._rr_index + 1) % n
        return candidates[idx]

    def acquire_upscale(self, tier: str, task_id: int) -> Optional[WorkerNode]:
        """领取超分节点：主力池（带档位标签）优先，2K 优先派发由调度侧排序保证；
        overflow 节点仅在主力全忙时承接 1K。"""
        idle = [n for n in self.nodes if n.role == "upscale" and n.healthy and not n.busy]
        main = [n for n in idle if n.has_tag(tier) and not n.has_tag("overflow")]
        if main:
            node = self._pick_round_robin(main)
        elif tier == "1k" and any(n.has_tag("overflow") for n in idle):
            node = next(n for n in idle if n.has_tag("overflow"))
        else:
            return None
        node.task_id = task_id
        return node

    def release(self, node: WorkerNode) -> None:
        node.task_id = None

    def get_node_by_url(self, url: str) -> Optional[WorkerNode]:
        """按 url 精确匹配节点（孤儿任务恢复时定位原派发节点）。"""
        url = (url or "").rstrip("/")
        for n in self.nodes:
            if n.url.rstrip("/") == url:
                return n
        return None

    def reset_all(self) -> None:
        """启动时兜底：清掉上次进程异常退出残留的忙闲位。"""
        for n in self.nodes:
            n.task_id = None

    # ---- 监控 ----
    def has_upscale(self) -> bool:
        return any(n.role == "upscale" for n in self.nodes)

    def snapshot(self) -> List[dict]:
        return [n.to_dict() for n in self.nodes]

    def counts(self) -> dict:
        return {
            "generate_total": sum(1 for n in self.nodes if n.role == "generate"),
            "generate_healthy": sum(1 for n in self.nodes if n.role == "generate" and n.healthy),
            "upscale_total": sum(1 for n in self.nodes if n.role == "upscale"),
            "upscale_healthy": sum(1 for n in self.nodes if n.role == "upscale" and n.healthy),
        }


pool = WorkerPool()
