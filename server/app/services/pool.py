"""ComfyUI Worker 池：节点注册、心跳自愈、按角色/档位路由（见《H3集群部署方案》§2/§6）。

配置来源：COMFYUI_WORKERS 环境变量，条目间分号分隔，每项 "url|角色|标签"（标签内逗号分隔）。
- 角色：generate（768p 生成）| upscale（SeedVR2 超分）| image（qwen21 生图，广告图阶段）
- 标签：heavy=长片段优先、1k/2k=超分档位、overflow=仅主力全忙时承接 1K、
        prio:low=低优先级：同角色有高优先级空闲节点时不被派发（246:8190 兜底用）、
        gap:<秒>=间隙跑：每完成一个出图任务后间隔 N 秒再发下一个（A100 生图节点固有属性）、
        unet:<文件名>=超分节点注入的权重、
        unet:<mode族>:<文件名>=生成节点注入的权重（t2v/flf2v→fl2va、r2v→ref2va）、
        engine:seedvr2=原生节点模板 /
        engine:seedvr2_int8=KSampler 管线模板（默认，模板内 UNETLoader 默认 3B FP16）
未配置时退回 COMFYUI_URL 单实例（并发=1），行为与旧版一致。
Mock 模式（MOCK_COMFY=1）下以虚拟节点模拟 4 生成 + 3 超分并发。
"""
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import List, Optional

import httpx

from ..config import settings
from .clouds import registry

logger = logging.getLogger(__name__)

# 手动置忙清单（qwen21 模式等）：{"disabled": ["<url>", ...]}
DISABLED_FILE = Path(settings.data_dir).resolve() / "worker_disabled.json"

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
        self.disabled = False               # 手动置忙（外部清单）：派发侧视同 busy
        self.cloud = None                   # 云端实例（clouds/*.env 按 url 绑定；非云节点为 None）

    @property
    def busy(self) -> bool:
        """忙闲 = 本地占位 OR ComfyUI 真实队列有 running/pending 任务。

        后者用于后端重启后感知节点上仍在跑的任务（内存态 task_id 重启即清空，
        但 ComfyUI 队列是持久的），避免把新任务派到已在跑的节点上串行排队。
        """
        return self.disabled or self.task_id is not None or self.queue_busy

    def has_tag(self, tag: str) -> bool:
        return tag in self.tags

    @property
    def priority(self) -> int:
        """派发优先级：标签 prio:low → 0（兜底），其余默认 1（常规）。

        低优先级节点只在同角色高优先级节点全忙/不可用时才承接任务。
        """
        return 0 if self.has_tag("prio:low") else 1

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
        # 优先取「该 mode 专用」权重标签：unet:<mode>:<文件>。
        # 长视频导演台（director）用 Singularity，短视频 t2v/flf2v/r2v 用官方 convrot，
        # 两者不能再共用族标签，否则 unet 覆写会把导演台换成官方权重。
        for t in self.tags:
            if t.startswith(f"unet:{mode}:"):
                return t.split(":", 2)[2]
        family = "ref2va" if mode in ("r2v", "advideo") else "fl2va"
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

    @property
    def gap_sec(self) -> Optional[float]:
        """「间隙跑」间隔（秒）：标签 gap:<n> —— 节点固有属性，随节点走。

        语义：该节点每完成一个出图任务后，间隔 n 秒再发下一个（散热/防热降频）。
        无标签 → None（由调用侧回退全局配置）；gap:0 → 0.0（显式关闭）。
        """
        for t in self.tags:
            if t.startswith("gap:"):
                try:
                    val = float(t.split(":", 1)[1])
                except ValueError:
                    logger.warning("节点 %s 的 gap 标签非法: %s", self.url, t)
                    return None
                return val if val > 0 else 0.0
        return None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "role": self.role,
            "tags": sorted(self.tags),
            "healthy": self.healthy,
            "busy": self.busy,
            "disabled": self.disabled,
            "priority": self.priority,
            "task_id": self.task_id,
            "queue_busy": self.queue_busy,
            "consecutive_fails": self.consecutive_fails,
            # 生图节点固有「间隙跑」间隔（标签 gap:<n>）；非间隙节点为 None
            "gap_sec": self.gap_sec,
            # 云端实例信息（无 cloud 配置时全为 None / false，前端不渲染开关机按钮）
            "platform": self.cloud.platform if self.cloud else None,
            "instance_id": self.cloud.id if self.cloud else None,
            "instance_uuid": self.cloud.instance_uuid if self.cloud else None,
            "instance_status": self.cloud.instance_status if self.cloud else None,
            "op_state": self.cloud.op_state if self.cloud else None,
            "power_controllable": bool(self.cloud and self.cloud.controllable),
            # 最近一次手动开关机结果（失败原因持久化，刷新页面不丢；白名单字段，无凭据）
            "last_op_action": (self.cloud.last_op or {}).get("action") if self.cloud else None,
            "last_op_ok": (self.cloud.last_op or {}).get("ok") if self.cloud else None,
            "last_op_code": (self.cloud.last_op or {}).get("code") if self.cloud else None,
            "last_op_msg": (self.cloud.last_op or {}).get("msg") if self.cloud else None,
            "last_op_at": (self.cloud.last_op or {}).get("at") if self.cloud else None,
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
        if role not in ("generate", "upscale", "image"):
            raise ValueError(f"COMFYUI_WORKERS 非法角色: {role}（{item}；合法：generate|upscale|image）")
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
        self._cloud_task: Optional[asyncio.Task] = None
        self._rr_index = 0  # 生成节点轮询指针（round-robin，避免永远优先第一个节点）
        self._disabled_meta: dict = {}
        # 云端实例绑定：按 CLOUD_WORKER_URL 精确匹配；目录不存在/不匹配只告警（防误操作）
        try:
            registry.bind_nodes(self.nodes)
        except Exception:
            logger.exception("云端实例绑定失败（忽略，不影响本地节点）")

    # ---- 生命周期 ----
    async def start(self) -> None:
        if self.mock or self._hb_task is not None:
            return
        self._hb_task = asyncio.get_event_loop().create_task(self._heartbeat_loop())
        # 云实例状态轮询（无云实例时内部立即返回）
        self._cloud_task = asyncio.get_event_loop().create_task(registry.status_loop())
        self.load_disabled()
        logger.info(
            "Worker 池已启动：%d 生成 + %d 生图 + %d 超分",
            sum(1 for n in self.nodes if n.role == "generate"),
            sum(1 for n in self.nodes if n.role == "image"),
            sum(1 for n in self.nodes if n.role == "upscale"),
        )

    async def stop(self) -> None:
        if self._hb_task is not None:
            self._hb_task.cancel()
            self._hb_task = None
        if self._cloud_task is not None:
            self._cloud_task.cancel()
            self._cloud_task = None

    async def _heartbeat_loop(self) -> None:
        """每 10s 探活全部节点；连续失败 30s 摘除，恢复后自动回池。"""
        while True:
            try:
                await asyncio.gather(*(self._probe(n) for n in self.nodes))
                self.load_disabled()  # 每轮心跳重读置忙清单（外部可随时置忙/解忙）
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

    # ---- 手动置忙（外部清单，qwen21 模式等）----
    def load_disabled(self) -> None:
        """从清单文件刷新各节点置忙态；文件不存在/损坏则全部视为不置忙。"""
        try:
            data = json.loads(DISABLED_FILE.read_text()) if DISABLED_FILE.exists() else {}
        except Exception:
            logger.warning("读取置忙清单失败: %s", DISABLED_FILE)
            return
        urls = data.get("disabled", []) if isinstance(data, dict) else list(data)
        want = {str(u).rstrip("/") for u in urls}
        self._disabled_meta = {
            "note": data.get("note", "") if isinstance(data, dict) else "",
            "ts": data.get("ts", "") if isinstance(data, dict) else "",
        }
        for n in self.nodes:
            n.disabled = n.url.rstrip("/") in want

    def set_disabled(self, urls, note: str = "") -> dict:
        """写清单并即时生效（幂等）。urls 为空 = 全部解除置忙。"""
        norm = sorted({str(u).rstrip("/") for u in urls})
        DISABLED_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"disabled": norm, "note": note, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        DISABLED_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
        self.load_disabled()
        logger.info("Worker 置忙清单已更新: %s (note=%s)", norm, note)
        return {"disabled": norm, "note": note, "ts": payload["ts"], "file": str(DISABLED_FILE)}

    def disabled_state(self) -> dict:
        return {
            "disabled": sorted(n.url for n in self.nodes if n.disabled),
            "note": self._disabled_meta.get("note", ""),
            "ts": self._disabled_meta.get("ts", ""),
            "file": str(DISABLED_FILE),
        }

    # ---- 槽位分配 ----
    def acquire_generate(self, heavy: bool, task_id: int) -> Optional[WorkerNode]:
        """领取生成节点：时长>10s 的任务优先 heavy 节点（A100），否则任意空闲节点。

        采用 round-robin 轮询：从上次派发位置之后找空闲节点，避免永远优先第一个
        节点（此前 idle[0] 导致 45 永远优先、51/246 空闲）。
        """
        idle = [n for n in self.nodes if n.role == "generate" and n.healthy and not n.busy]
        idle = self._split_by_priority(idle)   # 低优先级节点仅在高优先级全忙时兜底
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

    def _split_by_priority(self, idle: List[WorkerNode]) -> List[WorkerNode]:
        """按优先级收敛候选集：有高优先级空闲节点时，低优先级节点不参与派发。

        低优先级节点（prio:low，如 qwen21 占用的 246:8190）仅作兜底：
        高优先级全忙时才回落使用，既不会「有闲卡却派给低优先卡」，
        也不会因过滤而无人可用（hi 为空时原样返回 idle）。
        """
        hi = [n for n in idle if n.priority > 0]
        return hi or idle

    def _pick_round_robin(self, candidates: List[WorkerNode]) -> WorkerNode:
        """在候选空闲节点中按轮询指针选一个，并推进指针。"""
        n = len(candidates)
        idx = self._rr_index % n
        self._rr_index = (self._rr_index + 1) % n
        return candidates[idx]

    def has_director(self) -> bool:
        """是否配置了长视频导演台节点（标签 director）。"""
        return any(n.role == "generate" and n.has_tag("director") for n in self.nodes)

    def acquire_director(self, task_id: int) -> Optional[WorkerNode]:
        """领取导演台节点：只认带 director 标签、且当前完全空闲（无占位且队列空）的节点。

        导演台独占整卡（一次提交跑完全部段、可达数十分钟），故不做 heavy 优先与兜底派发；
        短视频生成侧的 _claim 与领取逻辑在其运行期间互斥（见 worker._generate_loop）。
        """
        idle = [
            n for n in self.nodes
            if n.role == "generate" and n.has_tag("director") and n.healthy and not n.busy
        ]
        idle = self._split_by_priority(idle)
        if not idle:
            return None
        node = self._pick_round_robin(idle)
        node.task_id = task_id
        return node

    def acquire_upscale(self, tier: str, task_id: int) -> Optional[WorkerNode]:
        """领取超分节点：主力池（带档位标签）优先，2K 优先派发由调度侧排序保证；
        overflow 节点仅在主力全忙时承接 1K。"""
        idle = [n for n in self.nodes if n.role == "upscale" and n.healthy and not n.busy]
        idle = self._split_by_priority(idle)
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
            "image_total": sum(1 for n in self.nodes if n.role == "image"),
            "image_healthy": sum(1 for n in self.nodes if n.role == "image" and n.healthy),
        }


pool = WorkerPool()
