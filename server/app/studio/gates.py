"""质量门：shuohao 的 bridge + 平台自己的硬约束。

分工：
- shuohao 管「内容对不对」：时长、钩子、切长、景别、构图、H3 提示词；
- 平台管「这台机器能不能生成」：段秒上限、参考图上限、关键帧齐不齐、积分够不够。
两者都过才允许提交；平台门不会替代上游门。
"""
import json
import subprocess
from pathlib import Path
from typing import Optional

from ..config import settings

BRIDGE = Path(__file__).resolve().parent.parent / "services" / "skills" / "shuohao" / "studio_bridge.mjs"

# 平台门：与 shuohao 的段规则一致，但多了「一次生成能不能喂得下」
MAX_CUTS = int(getattr(settings, "studio_max_cuts_per_segment", 9))
MAX_SEGMENT_SECONDS = float(getattr(settings, "studio_max_segment_seconds", 15))


def _run_node(args: list, payload: dict) -> dict:
    """跑一次 bridge（seed / stats 等），失败抛 RuntimeError 带原因。"""
    proc = subprocess.run(
        [str(settings.studio_node_bin), str(BRIDGE), *args],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        timeout=float(getattr(settings, "studio_gate_timeout", 60)),
        cwd=str(BRIDGE.parent),
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "未知错误").strip()[:500])
    return json.loads(proc.stdout)


def run_seed(kind: str, payload: dict) -> dict:
    """确定性生成一份 staging / storyboard；失败抛 RuntimeError，由路由层转 400。"""
    if not BRIDGE.exists():
        raise RuntimeError(f"找不到 {BRIDGE}")
    try:
        return _run_node(["seed", kind], payload)["doc"]
    except FileNotFoundError as exc:
        raise RuntimeError(f"找不到 Node：{settings.studio_node_bin}（生产请配 STUDIO_NODE_BIN）") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("生成超时") from exc
    except ValueError as exc:
        raise RuntimeError(f"生成结果不是 JSON：{exc}") from exc


def run_bridge(kind: str, doc: dict, ctx: Optional[dict] = None, cmd: str = "gates") -> dict:
    """调 studio_bridge，失败不抛：返回一道失败门，让前端能看到原因。"""
    if not BRIDGE.exists():
        return {
            "ok": False,
            "kind": kind,
            "gates": [{
                "id": "bridge-missing",
                "label": "门引擎缺失",
                "ok": False,
                "detail": f"找不到 {BRIDGE}",
            }],
            "problems": [],
            "stats": None,
        }

    payload = {"doc": doc, **(ctx or {})}
    try:
        proc = subprocess.run(
            [str(settings.studio_node_bin), str(BRIDGE), cmd, kind],
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=float(getattr(settings, "studio_gate_timeout", 60)),
            cwd=str(BRIDGE.parent),
        )
    except FileNotFoundError:
        return _bridge_error(kind, f"找不到 Node：{settings.studio_node_bin}（生产请配 STUDIO_NODE_BIN）")
    except subprocess.TimeoutExpired:
        return _bridge_error(kind, "门执行超时")

    if proc.returncode != 0:
        return _bridge_error(kind, (proc.stderr or proc.stdout or "未知错误").strip()[:500])

    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return _bridge_error(kind, f"门输出不是 JSON：{(proc.stdout or '')[:300]}")
    out.setdefault("ok", True)
    out.setdefault("gates", [])
    out.setdefault("problems", [])
    out.setdefault("stats", None)
    return out


def _bridge_error(kind: str, detail: str) -> dict:
    return {
        "ok": False,
        "kind": kind,
        "gates": [{"id": "bridge-error", "label": "门引擎报错", "ok": False, "detail": detail}],
        "problems": [detail],
        "stats": None,
    }


# ---------------------------------------------------------------- 平台门

def platform_gates(kind: str, doc: dict) -> list:
    """只看 JSON 就能判的硬约束；查库的（关键帧/积分）放 preflight。"""
    if kind != "storyboard":
        return []

    gates = []
    episodes = doc.get("episodes") or []
    over_cap, too_many_cuts, bad_sum, no_seg = [], [], [], []

    for ep in episodes:
        for seg in ep.get("segments") or []:
            sid = seg.get("id") or "?"
            cuts = seg.get("cuts") or []
            seconds = [float(c.get("seconds") or 0) for c in cuts]
            total = round(sum(seconds), 2)

            if not cuts:
                no_seg.append(sid)
                continue
            if total > MAX_SEGMENT_SECONDS + 0.01:
                over_cap.append(f"{sid} {total}s")
            if len(cuts) > MAX_CUTS:
                too_many_cuts.append(f"{sid} {len(cuts)} 切")
            # 段总秒数与切秒数必须自洽（schema 没有单独的段秒字段，这里只防 0 秒）
            if total <= 0:
                bad_sum.append(sid)

    def gate(gid: str, label: str, bad: list) -> dict:
        return {
            "id": gid,
            "label": label,
            "ok": not bad,
            "detail": "；".join(bad[:4]) + (f" 等 {len(bad)} 处" if len(bad) > 4 else "") if bad else "",
        }

    gates.append(gate("platform-segment-cap", f"段 ≤ {MAX_SEGMENT_SECONDS:g}s", over_cap))
    gates.append(gate("platform-cut-cap", f"段内切 ≤ {MAX_CUTS}", too_many_cuts))
    gates.append(gate("platform-cuts", "段内有切且秒数有效", no_seg + bad_sum))
    return gates


def _normalize(gate_row: dict) -> dict:
    """把一条门归一成 {id,label,ok:bool,detail}。

    上游个别门的 `ok` 是「证据文本」而不是布尔（如 outline 的 ep1-hook 直接放钩子原句），
    这里统一成布尔并把它挪进 detail——门只回答过/不过，证据放详情里给前端展示。
    """
    row = dict(gate_row or {})
    ok = row.get("ok")
    detail = str(row.get("detail") or "")
    if not isinstance(ok, bool):
        if isinstance(ok, str) and ok and not detail:
            detail = ok
        ok = bool(ok)
    row["ok"] = ok
    row["detail"] = detail
    row["id"] = str(row.get("id") or "")
    row["label"] = str(row.get("label") or row["id"])
    return row


def merge(kind: str, bridge_out: dict, platform: Optional[list] = None) -> dict:
    gates = [_normalize(g) for g in (bridge_out.get("gates") or [])]
    gates.extend(_normalize(g) for g in (platform or []))
    passed = bool(gates) and all(g.get("ok") for g in gates)
    return {
        "kind": kind,
        "gates": gates,
        "problems": list(bridge_out.get("problems") or []),
        "stats": bridge_out.get("stats"),
        "passed": passed,
        "ok": bool(bridge_out.get("ok", True)),
    }


def check(db, project, kind: str, data=None, episode=None) -> dict:
    """跑一次门：库里的文档 + 同项目的上游上下文 + 平台硬约束。

    data 不为空时只检查这份草稿，不落库——前端改一句就能先看门过不过。
    """
    from . import docs as docmod

    episode_id = episode.id if episode is not None else 0
    if data is None:
        data = docmod.get_doc(db, project.id, kind, episode_id)

    ctx = docmod.project_ctx(db, project.id)
    ctx.pop(kind, None)
    if episode is not None:
        ctx["script"] = data if kind == "script" else docmod.get_doc(db, project.id, "script", episode.id)
        ctx["staging"] = docmod.get_doc(db, project.id, "staging", episode.id)
    if kind == "cast":
        ctx["sourceText"] = data.get("source") or None

    out = run_bridge(kind, data, ctx)
    return merge(kind, out, platform_gates(kind, data))


# ---------------------------------------------------------------- 资产门（查库）

def asset_gates(views: list, kind: str = "character") -> list:
    """views: [{view, version, confirmed, sha256}]。缺图/锚点未确认都在这里拦。"""
    by_view: dict = {}
    for v in views:
        name = v.get("view")
        if not name:
            continue
        cur = by_view.get(name)
        if cur is None or int(v.get("version") or 1) >= int(cur.get("version") or 1):
            by_view[name] = v

    if kind == "character":
        required = ["front-full", "face-front", "side-full", "back-full"]
    else:
        required = ["sheet"]

    missing = [v for v in required if v not in by_view]
    anchor = by_view.get("front-full") or by_view.get("sheet")
    anchor_ok = bool(anchor and anchor.get("confirmed"))

    return [
        {
            "id": "platform-views",
            "label": "四视图齐全" if kind == "character" else "设定图齐全",
            "ok": not missing,
            "detail": f"缺 {', '.join(missing)}" if missing else "",
        },
        {
            "id": "platform-anchor",
            "label": "锚点已确认",
            "ok": anchor_ok,
            "detail": "" if anchor_ok else "先确认正面全身（或场景 sheet）再出派生",
        },
    ]
