"""短剧工作台：确定性质量门（调用 vendored skill 脚本）。

`shuohao-skills` 的每个 skill 都自带 validate/checkup 脚本（零依赖 Node ≥18）。
本模块把生成的文档落成临时 JSON，调 `node novel-*.mjs validate ...`，
把结果写回 `hibtv_docs.gates`：

    {"status": "ok|fail|skipped", "problems": [...], "checked_at": "...", "runner": "node x.y.z"}

node 缺失时**显式标记 skipped**（UI 明示「质量门未运行」），绝不静默通过。
"""
import json
import logging
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from ..config import settings

logger = logging.getLogger(__name__)

SKILL_ROOT = Path(__file__).resolve().parent / "skills" / "shuohao-skills" / "skills"

# kind → (skill 目录, 脚本名)
_SCRIPTS: Dict[str, tuple] = {
    "outline": ("novel-outline", "novel-outline.mjs"),
    "script": ("novel-script", "novel-script.mjs"),
    "storyboard": ("novel-storyboard", "novel-storyboard.mjs"),
}

_TIMEOUT = 120


def _node_bin() -> Optional[str]:
    """找到可用的 node 可执行文件；找不到返回 None（门降级为 skipped）。"""
    bin_name = (settings.hibtv_node_bin or "node").strip() or "node"
    found = shutil.which(bin_name)
    if found:
        return found
    # 常见 nvm / homebrew 兜底
    for guess in ("/opt/homebrew/bin/node", "/usr/local/bin/node", "/usr/bin/node"):
        if Path(guess).exists():
            return guess
    return None


def status() -> Dict:
    node = _node_bin()
    return {
        "available": bool(node),
        "node": node or "",
        "bin": settings.hibtv_node_bin,
        "skills": sorted(_SCRIPTS),
    }


def _dump(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def run_gates(
    kind: str,
    data: Dict,
    *,
    outline: Optional[Dict] = None,
    art: Optional[Dict] = None,
    script: Optional[Dict] = None,
) -> Dict:
    """跑一个文档的确定性质量门。任何异常都收敛成 fail，不抛。"""
    entry = _SCRIPTS.get(kind)
    if not entry:
        return {"status": "skipped", "problems": ["该文档类型没有确定性质量门"], "checked_at": _now()}
    node = _node_bin()
    if not node:
        return {
            "status": "skipped",
            "problems": [f"未找到 Node（HIBTV_NODE_BIN={settings.hibtv_node_bin}），质量门未运行"],
            "checked_at": _now(),
            "runner": "",
        }
    skill_dir, script_name = entry
    script_path = SKILL_ROOT / skill_dir / "scripts" / script_name
    if not script_path.exists():
        return {"status": "skipped", "problems": [f"质量门脚本缺失：{script_path}"], "checked_at": _now()}

    with tempfile.TemporaryDirectory(prefix="hibtv_gate_") as tmp:
        tmp_path = Path(tmp)
        doc_path = tmp_path / f"{kind}.json"
        _dump(doc_path, data)
        cmd = [node, str(script_path), "validate", str(doc_path), "--no-log"]
        if kind in ("script", "storyboard") and outline is not None:
            _dump(tmp_path / "outline.json", outline)
            cmd += ["--outline", str(tmp_path / "outline.json")]
        if kind in ("script", "storyboard") and art is not None:
            _dump(tmp_path / "art.json", art)
            cmd += ["--art", str(tmp_path / "art.json")]
        if kind == "storyboard":
            if script is None:
                return {"status": "skipped", "problems": ["分镜质量门需要先有本集剧本"], "checked_at": _now()}
            _dump(tmp_path / "script.json", script)
            cmd += ["--script", str(tmp_path / "script.json")]
        try:
            proc = subprocess.run(  # noqa: S603 —— 固定脚本路径 + 固定参数，无用户输入拼接
                cmd, cwd=str(tmp_path), capture_output=True, text=True, timeout=_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("质量门执行失败（%s）：%s", kind, exc)
            return {"status": "fail", "problems": [f"质量门执行失败：{exc}"], "checked_at": _now(),
                    "runner": node}

        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        raw = _collect_problems(out)
        problems = _relax(kind, data, raw)
        ok = proc.returncode == 0 or (bool(raw) and not _blocking(problems))
        if not ok and not raw:
            problems = [ln.strip() for ln in out.splitlines() if ln.strip()][:20]
        elif ok and raw and not problems:
            problems = ["单集项目：大爆点必然落在最后一集，「大爆点提前」门不适用，已跳过"]
        return {
            "status": "ok" if ok else "fail",
            "problems": problems[:40],
            "checked_at": _now(),
            "runner": node,
        }


def _collect_problems(out: str) -> list:
    """按 skill 脚本的输出格式收集违规项。

    脚本把明细打在 stderr 的续行（两空格缩进）里，只有标题行带 ✗：
        ✗ 2 处违规（stage=full）：
          E01 缺少钩子
          S01 未在 art 里登记
    这里保留标题行 + 紧随其后的缩进明细，避免只剩一行空标题。
    """
    problems: list = []
    in_detail = False
    for ln in out.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("✗"):
            problems.append(s)
            in_detail = True
            continue
        if in_detail and ln[:1] in (" ", "\t"):
            problems.append(s)
            continue
        in_detail = False
        if "违规" in s or "错误" in s or "Error" in s:
            problems.append(s)
    return problems


def _relax(kind: str, data: Dict, problems: list) -> list:
    """把单集项目上不可能成立的门的违规项降级掉。

    skill 的 `major-early` 门要求「大爆点在最后一集之前出现」，
    单集项目恒不成立——这不是内容缺陷，不能报红。改写成说明行供 UI 展示。
    """
    if kind != "outline" or not problems:
        return problems
    total = (data.get("params") or {}).get("episodes") or len(data.get("episodes") or [])
    try:
        total = int(total)
    except (TypeError, ValueError):
        total = 0
    if total > 1:
        return problems
    if not any("大爆点不在最后一集" in row for row in problems):
        return problems
    details = [row for row in problems
               if not row.startswith("✗") and "大爆点不在最后一集" not in row]
    if not details:
        return []
    return [f"✗ {len(details)} 处违规："] + details


def _blocking(problems: list) -> list:
    return [p for p in problems if not p.startswith("✗")]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def hint_of(gates: Optional[Dict]) -> str:
    """把上次质量门的失败项整理成喂回 LLM 的修正提示。"""
    if not gates or gates.get("status") != "fail":
        return ""
    rows = gates.get("problems") or []
    return "\n".join(f"- {r}" for r in rows[:20])
