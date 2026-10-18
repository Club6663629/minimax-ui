"""短剧媒体处理：ffmpeg/ffprobe 切片、拼接、抽帧、联系表、接缝指标。

本机可能只有 ffmpeg 没有 ffprobe（imageio-ffmpeg shim），所以所有探测都带
「解析 ffmpeg -i 头部」的回退路径；所有 ffmpeg 调用失败都抛 MediaError，
由 runner 统一回写 run/episode 的失败状态。
"""
import logging
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class MediaError(RuntimeError):
    """媒体处理失败（缺文件 / ffmpeg 报错 / 产物为空）。"""


def _run(cmd: list, timeout: int = 900) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise MediaError("未找到 ffmpeg/ffprobe，请确认已安装并在 PATH 中") from exc
    except subprocess.SubprocessError as exc:
        raise MediaError(f"ffmpeg 执行失败：{exc}") from exc


def run_ffmpeg(cmd: list, timeout: int = 900) -> subprocess.CompletedProcess:
    proc = _run(cmd, timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise MediaError(f"ffmpeg 失败：{detail}")
    return proc


def _ffmpeg_header(path: Path) -> str:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return (proc.stderr or "") + (proc.stdout or "")
    except (FileNotFoundError, subprocess.SubprocessError):
        return ""


def probe_duration(path) -> float:
    """探测媒体时长（秒）；ffprobe 缺失时解析 ffmpeg 头部 Duration。"""
    path = Path(path)
    if not path.exists():
        return 0.0
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        value = (proc.stdout or "").strip().splitlines()
        if value:
            return round(float(value[0]), 3)
    except (FileNotFoundError, subprocess.SubprocessError, ValueError):
        pass
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", _ffmpeg_header(path))
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3)


def probe_has_audio(path) -> bool:
    path = Path(path)
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return bool((proc.stdout or "").strip())
    except (FileNotFoundError, subprocess.SubprocessError):
        return "Audio:" in _ffmpeg_header(path)


# ---------------------------------------------------------------- 切片
def slice_frames(master, out, start_frame: int, end_frame: int, fps: int = 24,
                 has_audio: Optional[bool] = None) -> Path:
    """按帧窗口（start 含 / end 不含）切出一段；无音轨时补静音轨保证可 concat。"""
    master, out = Path(master), Path(out)
    if not master.exists():
        raise MediaError("原始产物文件缺失，无法切片")
    if end_frame <= start_frame:
        raise MediaError(f"非法切片窗口：{start_frame}–{end_frame}")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.mp4")
    if has_audio is None:
        has_audio = probe_has_audio(master)
    duration = (end_frame - start_frame) / float(fps)
    vf = f"trim=start_frame={start_frame}:end_frame={end_frame},setpts=PTS-STARTPTS"
    base = ["ffmpeg", "-y", "-v", "error", "-i", str(master)]
    tail = [
        "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp),
    ]
    if has_audio:
        af = f"atrim=start={start_frame / fps:.6f}:end={end_frame / fps:.6f},asetpts=PTS-STARTPTS"
        cmd = base + ["-vf", vf, "-af", af,
                      "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"] + tail
    else:
        cmd = (
            base
            + ["-f", "lavfi", "-t", f"{duration:.6f}", "-i", "anullsrc=r=48000:cl=stereo"]
            + ["-filter_complex", f"[0:v]{vf}[v]", "-map", "[v]", "-map", "1:a:0",
               "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest"]
            + tail
        )
    run_ffmpeg(cmd)
    if not tmp.exists() or tmp.stat().st_size < 1024:
        raise MediaError("切片产物为空")
    tmp.replace(out)
    return out


def trim_frames(src, out, frames: int, fps: int = 24) -> Path:
    """把单段产物裁到精确帧数（返修补齐窗口用）。"""
    return slice_frames(src, out, 0, frames, fps=fps, has_audio=None)


def pad_frames(src, out, frames: int, fps: int = 24) -> Path:
    """产物比窗口短时克隆末帧补齐（理论不触发，兜底）。"""
    src, out = Path(src), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    vf = f"tpad=stop_mode=clone:stop_duration={frames / fps:.6f},trim=end_frame={frames},setpts=PTS-STARTPTS"
    run_ffmpeg([
        "ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(out),
    ])
    return out


# ---------------------------------------------------------------- 拼接
def _escape_concat(path: Path) -> str:
    return str(path).replace("'", "'\\''")


def concat_clips(paths: list, out, fps: int = 24) -> Path:
    """按顺序拼接 current clips；copy 失败或时长偏差过大时重编码兜底。"""
    paths = [Path(p) for p in paths]
    if not paths:
        raise MediaError("没有可拼接的镜头")
    for path in paths:
        if not path.exists():
            raise MediaError(f"镜头文件缺失：{path.name}")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        for path in paths:
            fh.write(f"file '{_escape_concat(path)}'\n")
        list_path = Path(fh.name)
    tmp = out.with_suffix(".tmp.mp4")
    expected = sum(probe_duration(p) for p in paths)
    try:
        run_ffmpeg(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(list_path), "-c", "copy", "-movflags", "+faststart", str(tmp)])
        actual = probe_duration(tmp)
        if expected and abs(actual - expected) > 0.25:
            raise MediaError(f"concat 时长偏差过大（{actual:.2f}s vs {expected:.2f}s）")
    except MediaError as exc:
        logger.warning("concat -c copy 失败，重编码兜底：%s", exc)
        run_ffmpeg([
            "ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(tmp),
        ])
    finally:
        list_path.unlink(missing_ok=True)
    if not tmp.exists() or tmp.stat().st_size < 1024:
        raise MediaError("拼接产物为空")
    tmp.replace(out)
    return out


# ---------------------------------------------------------------- 抽帧
def extract_frame(clip, frame_index: int, out) -> Path:
    clip, out = Path(clip), Path(out)
    if not clip.exists():
        raise MediaError(f"镜头文件缺失：{clip.name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        "ffmpeg", "-y", "-v", "error", "-i", str(clip),
        "-vf", f"select=eq(n\\,{max(0, int(frame_index))})", "-vsync", "0",
        "-frames:v", "1", str(out),
    ])
    if not out.exists():
        raise MediaError("抽帧失败")
    return out


def make_sheet(clip_paths: list, out, cols: int = 4) -> Optional[Path]:
    """联系表：每镜抽中间帧，tile 拼图；失败不阻塞主流程。"""
    paths = [Path(p) for p in clip_paths if Path(p).exists()]
    if not paths:
        return None
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="drama_sheet_"))
    try:
        for index, clip in enumerate(paths):
            duration = probe_duration(clip)
            frames = max(1, int(round(duration * 24)))
            extract_frame(clip, max(0, frames // 2), tmp_dir / f"f_{index:03d}.png")
        rows = max(1, math.ceil(len(paths) / cols))
        run_ffmpeg([
            "ffmpeg", "-y", "-v", "error", "-i", str(tmp_dir / "f_%03d.png"),
            "-vf", f"tile={cols}x{rows}:padding=4:margin=4", "-frames:v", "1",
            "-q:v", "3", str(out),
        ])
        return out if out.exists() else None
    except MediaError as exc:
        logger.warning("联系表生成失败（不阻塞）：%s", exc)
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------- 接缝指标
def _ssim(a: Path, b: Path) -> Optional[float]:
    proc = _run(["ffmpeg", "-v", "info", "-i", str(a), "-i", str(b),
                 "-lavfi", "ssim", "-f", "null", "-"])
    match = re.search(r"All:([0-9.]+)", (proc.stderr or ""))
    return round(float(match.group(1)), 4) if match else None


def _yavg(image: Path) -> Optional[float]:
    proc = _run(["ffmpeg", "-v", "info", "-i", str(image),
                 "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                 "-f", "null", "-"])
    match = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", (proc.stderr or ""))
    return round(float(match.group(1)), 2) if match else None


def _mean_volume(clip: Path, start: float, duration: float) -> Optional[float]:
    proc = _run(["ffmpeg", "-v", "info", "-ss", f"{max(0.0, start):.3f}",
                 "-t", f"{duration:.3f}", "-i", str(clip), "-vn",
                 "-af", "volumedetect", "-f", "null", "-"])
    match = re.search(r"mean_volume:\s*(-?[0-9.]+)\s*dB", (proc.stderr or ""))
    return round(float(match.group(1)), 2) if match else None


def _level_of(ssim: Optional[float], d_y: Optional[float], audio_gap: Optional[float]) -> str:
    level = "ok"
    if ssim is not None:
        if ssim < 0.60:
            level = "fail"
        elif ssim < 0.75 and level == "ok":
            level = "warn"
    if d_y is not None:
        if d_y > 24:
            level = "fail"
        elif d_y > 12 and level == "ok":
            level = "warn"
    if audio_gap is not None:
        if audio_gap > 12 and level == "ok":
            level = "warn"
        elif audio_gap > 6 and level == "ok":
            level = "warn"
    return level


def seam_metrics(prev_clip, prev_frames: int, next_clip) -> dict:
    """前镜末帧 vs 后镜首帧：SSIM / ΔY / 音频差，取最差等级。"""
    prev_clip, next_clip = Path(prev_clip), Path(next_clip)
    with tempfile.TemporaryDirectory(prefix="drama_seam_") as tmp:
        a = Path(tmp) / "prev.png"
        b = Path(tmp) / "next.png"
        try:
            extract_frame(prev_clip, max(0, int(prev_frames) - 1), a)
            extract_frame(next_clip, 0, b)
        except MediaError as exc:
            logger.warning("接缝抽帧失败：%s", exc)
            return {"ssim": None, "d_y": None, "audio_db": None, "level": "unknown"}
        ssim = _ssim(a, b)
        ya, yb = _yavg(a), _yavg(b)
        d_y = round(abs(ya - yb), 2) if ya is not None and yb is not None else None
        prev_dur = probe_duration(prev_clip)
        db_a = _mean_volume(prev_clip, max(0.0, prev_dur - 0.5), 0.5)
        db_b = _mean_volume(next_clip, 0.0, 0.5)
        gap = round(abs(db_a - db_b), 2) if db_a is not None and db_b is not None else None
        return {"ssim": ssim, "d_y": d_y, "audio_db": gap, "level": _level_of(ssim, d_y, gap)}
