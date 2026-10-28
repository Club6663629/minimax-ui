"""短剧工作台：质量门**读产物**（不再跑脚本）。

口径变了，别再退回去：
- 门在网关侧由 skill 自己跑，平台**不再**调 `node *.mjs validate`，也不读 skill 规范；
- 平台只读产物：优先 `.gates.jsonl`（逐次运行 + 逐条失败），退回 `*-report.html`
  里的 `gatepill N/M` 徽标（展示层，脚本改版即失效 → 退化为 `skipped` 并明示）；
- 用户就地改稿后门**没有复跑**，如实标 `stale`（「改动后门未复跑」），绝不伪装成通过。
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_FAIL = "fail"
STATUS_SKIPPED = "skipped"
STATUS_STALE = "stale"

_GATEPILL_RE = re.compile(r"gatepill\s+(?P<cls>[\w-]+)\"[^>]*>(?P<text>[^<]*)", re.IGNORECASE)
_RATIO_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def status() -> Dict:
    """链路状态：门已改成「读网关产物」，本机不再需要 node。"""
    return {
        "mode": "artifacts",
        "available": True,
        "node": "",
        "skills": [],
        "note": "质量门由网关 skill 产出（.gates.jsonl / report.html），本机不再执行门脚本",
    }


def from_artifacts(files: Dict[str, Path], doc_name: str = "", *,
                   skipped: str = "") -> Dict:
    """从一段 stage 的产物里取门结果。

    - `.gates.jsonl` 优先：`{"kind":"run","gates":N,"failed":M}` + 逐条 `{"kind":"fail",...}`
    - 否则找 `*report*.html` 的 `gatepill` 徽标（只能拿到 N/M，明细从 HTML 行里捞）
    - 都没有 → `skipped` + note（明示「没有门结果」，不当作通过）
    """
    gates_path = _find(files, suffix=".gates.jsonl")
    if gates_path is not None:
        parsed = _parse_jsonl(Path(files[gates_path]))
        if parsed:
            parsed["source"] = Path(gates_path).name
            return parsed
    report = _find(files, suffix=".html")
    if report is not None:
        parsed = _parse_report_html(Path(files[report]), doc_name)
        if parsed:
            parsed["source"] = Path(report).name
            return parsed
    return {
        "status": STATUS_SKIPPED,
        "passed": 0, "total": 0,
        "problems": [], "notes": [skipped or "产物里没有门结果（.gates.jsonl / report.html 都没有）"],
        "checked_at": _now(),
    }


def _find(files: Dict[str, Path], *, suffix: str = "", contain: str = "") -> Optional[str]:
    for rel in sorted(files or {}):
        name = Path(rel).name
        if suffix and name.endswith(suffix):
            return rel
        if contain and contain in name:
            return rel
    return None


def _parse_jsonl(path: Path) -> Optional[Dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    total = passed = failed = 0
    problems: List[str] = []
    notes: List[str] = []
    checked_at = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        checked_at = checked_at or str(row.get("at") or "")
        kind = str(row.get("kind") or "")
        if kind == "run":
            total = max(total, int(row.get("gates") or 0))
            failed = max(failed, int(row.get("failed") or 0))
        elif kind == "fail":
            label = str(row.get("label") or row.get("gate") or "").strip()
            detail = str(row.get("detail") or "").strip()
            problems.append(f"{label}：{detail}"[:600] if label else detail[:600])
        elif kind == "note":
            notes.append(str(row.get("detail") or row.get("label") or "")[:400])
    if not total and not problems:
        return None
    passed = max(0, total - failed) if total else 0
    if not total:
        total = len(problems)
    return {
        "status": STATUS_FAIL if problems else STATUS_OK,
        "passed": passed if problems else total,
        "total": total,
        "failed": failed or len(problems),
        "problems": problems[:40],
        "notes": notes[:10],
        "checked_at": checked_at or _now(),
    }


def _parse_report_html(path: Path, doc_name: str) -> Optional[Dict]:
    try:
        html = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = _GATEPILL_RE.search(html)
    if not m:
        return None
    text = m.group("text") or ""
    ratio = _RATIO_RE.search(text)
    if not ratio:
        return None
    passed, total = int(ratio.group(1)), int(ratio.group(2))
    failed = max(0, total - passed)
    problems: List[str] = []
    if failed:
        # 明细只能从展示层捞：抓带失败标记的行，抓不到就只报数量（不编造）
        for row in re.findall(r"<li[^>]*class=\"[^\"]*fail[^\"]*\"[^>]*>([^<]{4,300})</li>", html):
            problems.append(row.strip())
            if len(problems) >= 40:
                break
        if not problems:
            problems = [f"{failed} 条门失败（report.html 只有汇总，明细未结构化）"]
    return {
        "status": STATUS_FAIL if failed else STATUS_OK,
        "passed": passed, "total": total, "failed": failed,
        "problems": problems,
        "notes": ["门结果来自 report.html 的展示徽标（gatepill），脚本改版即失效"
                  if failed else ""],
        "checked_at": _now(),
    }


def stale(note: str = "改动后门未复跑") -> Dict:
    """就地编辑后门标记：不覆盖历史结果，只把状态改成 stale。"""
    return {
        "status": STATUS_STALE,
        "passed": 0, "total": 0,
        "problems": [], "notes": [note],
        "checked_at": _now(),
    }


def hint_of(gates: Optional[Dict]) -> str:
    """把上次质量门的失败项整理成「重跑时带上的提示」（只作业务提示，不改 schema）。"""
    if not gates or gates.get("status") != STATUS_FAIL:
        return ""
    rows = gates.get("problems") or []
    return "\n".join(f"- {r}" for r in rows[:20])


def notes_of(gates: Optional[Dict]) -> List[str]:
    """门的说明行（未运行原因 / 展示层降级）：不是缺陷，只给 UI 展示。"""
    if not gates:
        return []
    return [n for n in (gates.get("notes") or []) if n]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
