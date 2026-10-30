"""短剧工作台：当前分段清单拼接、音频边缘平滑与 cutlist 实测。

策略：
1. 先按段序生成 concat 清单，用 `-c copy` 无损拼接（所有段同参数同编码时最快最省）；
2. copy 失败（时间基 / 编码参数不一致）自动回退到 libx264 重编码；
3. 每段用 ffprobe 取实测秒数，与分镜计划秒数逐段对账，写进 cutlist。

ffmpeg/ffprobe 缺失时抛 HibtvComposeError，由 job 记录失败原因（UI 明示）。
"""
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from ..config import HIBTV_DIR, settings

logger = logging.getLogger(__name__)


class HibtvComposeError(Exception):
    """拼接失败（ffmpeg 缺失 / 段文件缺失 / 编码不兼容）。"""


def ffmpeg_bin() -> Optional[str]:
    return shutil.which("ffmpeg") or ("/opt/homebrew/bin/ffmpeg" if Path("/opt/homebrew/bin/ffmpeg").exists() else None)


def ffprobe_bin() -> Optional[str]:
    return shutil.which("ffprobe") or ("/opt/homebrew/bin/ffprobe" if Path("/opt/homebrew/bin/ffprobe").exists() else None)


def status() -> Dict:
    """链路状态：返 `available` 布尔供 UI 直接判（只给路径时前端得自己推），
    不可用时用 `note` 说人话。"""
    ff, fp = ffmpeg_bin() or "", ffprobe_bin() or ""
    available = bool(ff and (fp or not settings.hibtv_audio_fade_seconds))
    return {"ffmpeg": ff, "ffprobe": fp, "available": available,
            "note": "" if available else "请安装 ffmpeg 与 ffprobe，整集音频衔接需要两者"}


def probe_seconds(path: Path) -> float:
    """取媒体时长（秒）；只装了 ffmpeg 没装 ffprobe 时退回解析 ffmpeg 的 Duration 行。"""
    if not Path(path).exists():
        return 0.0
    exe = ffprobe_bin()
    if exe:
        try:
            proc = subprocess.run(  # noqa: S603 —— 固定可执行文件 + 只读参数
                [exe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=60,
            )
            return round(float((proc.stdout or "0").strip() or 0), 2)
        except (OSError, subprocess.SubprocessError, ValueError):
            return 0.0
    ff = ffmpeg_bin()
    if not ff:
        return 0.0
    try:
        proc = subprocess.run(  # noqa: S603 —— ffmpeg -i 只读探测，退出码非 0 属正常
            [ff, "-hide_banner", "-i", str(path), "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    for line in (proc.stderr or "").splitlines():
        head = line.strip()
        if not head.startswith("Duration:"):
            continue
        stamp = head.split("Duration:", 1)[1].split(",", 1)[0].strip()
        try:
            hh, mm, ss = stamp.split(":")
            return round(int(hh) * 3600 + int(mm) * 60 + float(ss), 2)
        except ValueError:
            return 0.0
    return 0.0


def _run(cmd: Sequence[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(  # noqa: S603 —— 固定可执行文件 + 参数由本地路径拼装
            list(cmd), capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HibtvComposeError(f"ffmpeg 执行失败：{exc}") from exc


def _has_audio(path):
    exe = ffprobe_bin()
    if not exe:
        raise HibtvComposeError("音频衔接需要 ffprobe，请安装后重试")
    proc = _run([exe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                 "stream=index", "-of", "csv=p=0", str(path)], timeout=60)
    if proc.returncode:
        raise HibtvComposeError(f"无法检查音轨：{path.name}")
    return bool(proc.stdout.strip())


def _prepare_audio(exe, files, durations, directory, fade):
    """只平滑音频边缘，不重叠或压缩画面时间线；缺音轨补等长静音。"""
    prepared = []
    for i, (path, duration) in enumerate(zip(files, durations)):
        out = directory / f"audio-{i}.mp4"
        cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path)]
        audio = _has_audio(path)
        if not audio:
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        filters = ["aresample=48000", "aformat=channel_layouts=stereo", "apad", f"atrim=duration={duration:.6f}"]
        if i > 0:
            filters.append(f"afade=t=in:st=0:d={fade:.6f}")
        if i < len(files)-1:
            filters.append(f"afade=t=out:st={max(0, duration-fade):.6f}:d={fade:.6f}")
        cmd += ["-map", "0:v:0", "-map", "0:a:0" if audio else "1:a:0",
                "-c:v", "copy", "-af", ",".join(filters), "-c:a", "aac", "-b:a", "192k",
                "-t", f"{duration:.6f}", str(out)]
        proc = _run(cmd)
        if proc.returncode or not out.is_file():
            raise HibtvComposeError(f"音频衔接失败：{(proc.stderr or '')[:300]}")
        prepared.append(out)
    return prepared


def render_episode(
    *,
    project_id: int,
    ep: int,
    segment_paths: Sequence[str],
    planned: Optional[Sequence[float]] = None,
    version: int = 1,
) -> Dict:
    """把一段清单拼成整集，返回 {path, duration_sec, size, cutlist}。"""
    exe = ffmpeg_bin()
    if not exe:
        raise HibtvComposeError("未找到 ffmpeg，无法拼接整集")
    files = [Path(p) for p in segment_paths if p]
    missing = [str(p) for p in files if not p.exists()]
    if missing:
        raise HibtvComposeError(f"有 {len(missing)} 个段文件缺失：{missing[:3]}")
    if not files:
        raise HibtvComposeError("没有可拼接的段")

    out_dir = HIBTV_DIR / str(project_id) / "render"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"E{ep:02d}-v{version}.mp4"
    listfile = out_dir / f"E{ep:02d}-v{version}.txt"
    durations = [probe_seconds(path) for path in files]
    if any(seconds <= 0 for seconds in durations):
        raise HibtvComposeError("存在无法读取时长的分段视频，不能拼接")
    if planned and len(planned) != len(files):
        raise HibtvComposeError("分段计划与视频清单数量不一致")
    if planned:
        for path, expected, actual in zip(files, planned, durations):
            if abs(float(expected) - actual) > 0.15:
                raise HibtvComposeError(f"{path.name} 实际 {actual:.3f}s 与计划 {expected:.3f}s 不符，请先检查分段输出")
    fade = max(0.0, min(float(settings.hibtv_audio_fade_seconds), 0.1))
    with tempfile.TemporaryDirectory(prefix="hibtv-audio-", dir=out_dir) as temporary:
        inputs = (_prepare_audio(exe, files, durations, Path(temporary), fade)
                  if fade and len(files) > 1 else files)
        listfile.write_text(
            "".join(f"file '{p.resolve().as_posix()}'\n" for p in inputs), encoding="utf-8")

        base = [exe, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                "-i", str(listfile)]
        proc = _run(base + ["-c", "copy", str(out_path)])
        if proc.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            logger.warning("段直拷拼接失败，回退重编码：%s", (proc.stderr or "")[:300])
            proc = _run(base + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                                "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p", str(out_path)])
            if proc.returncode != 0 or not out_path.exists():
                raise HibtvComposeError(f"拼接失败：{(proc.stderr or '').strip()[:400]}")

    # 永久清单引用原素材；带淡化的中间文件随临时目录释放。
    listfile.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in files), encoding="utf-8")
    cutlist: List[Dict] = []
    cumulative = 0.0
    for i, path in enumerate(files):
        actual = durations[i]
        plan = float(planned[i]) if planned and i < len(planned) else 0.0
        cumulative = round(cumulative + (actual or plan), 2)
        cutlist.append({
            "index": i + 1,
            "file": path.name,
            "plan": round(plan, 2),
            "actual": actual,
            "start": round(cumulative - (actual or plan), 2),
        })

    duration = probe_seconds(out_path) or cumulative
    result = {
        "path": str(out_path),
        "duration_sec": round(float(duration or 0), 2),
        "size": out_path.stat().st_size,
        "cutlist": json.dumps(cutlist, ensure_ascii=False),
        "version": version,
        "audioFadeSeconds": fade,
    }
    logger.info("短剧整集拼接完成：#%s E%02d v%d → %s（%.2fs）",
                project_id, ep, version, out_path.name, result["duration_sec"])
    return result
