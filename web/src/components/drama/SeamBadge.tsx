/** 接缝三色指标：SSIM / ΔY / 音量，hover 展示数值。 */
import type { DramaSeam, DramaSeamMetric } from "../../types";

function tone(metric: DramaSeamMetric): string {
  if (metric.level === "ok") return "bg-emerald-500";
  if (metric.level === "warn") return "bg-amber-500";
  if (metric.level === "fail") return "bg-rose-500";
  return "bg-background-300";
}

function fmt(value: number | null | undefined, digits: number): string {
  return typeof value === "number" ? value.toFixed(digits) : "-";
}

export default function SeamBadge({
  metric,
  seam,
  label,
}: {
  metric?: DramaSeamMetric | null;
  seam?: DramaSeam | null;
  label?: string;
}) {
  const data: DramaSeamMetric | null | undefined =
    metric || (seam ? { ssim: seam.ssim, d_y: seam.d_y, audio_db: seam.audio_db, level: seam.level } : null);
  if (!data) return null;
  const title = [
    label ? `接缝 ${label}` : "接缝",
    `SSIM ${fmt(data.ssim, 3)}`,
    `ΔY ${fmt(data.d_y, 1)}`,
    `音量 ${fmt(data.audio_db, 1)} dB`,
    `判定 ${data.level || "unknown"}`,
  ].join(" · ");
  const dot = tone(data);
  return (
    <span className="inline-flex items-center gap-1" title={title}>
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
      <span className={`h-1.5 w-1.5 rounded-full ${dot} opacity-60`} />
      <span className={`h-1.5 w-1.5 rounded-full ${dot} opacity-30`} />
    </span>
  );
}
