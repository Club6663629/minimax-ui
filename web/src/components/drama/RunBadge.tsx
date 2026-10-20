/** 活跃生成批次徽标：阶段 + 模式 + 费用。 */
import type { DramaRun } from "../../types";
import { RUN_PHASE_LABEL } from "./labels";

const PHASE_TONE: Record<string, string> = {
  queued: "bg-background-200 text-foreground-600",
  generating: "bg-amber-100 text-amber-700",
  postprocess: "bg-sky-100 text-sky-700",
  done: "bg-emerald-100 text-emerald-700",
  failed: "bg-rose-100 text-rose-700",
  cancelled: "bg-background-200 text-foreground-500",
};

export default function RunBadge({
  run,
  label,
}: {
  run: DramaRun | null | undefined;
  label?: string;
}) {
  if (!run) return null;
  const tone = PHASE_TONE[run.phase] || PHASE_TONE.queued;
  const phase = RUN_PHASE_LABEL[run.phase] || run.phase;
  return (
    <div className="panel space-y-1.5 px-3 py-2.5 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="text-foreground-500">{label || "当前批次"}</span>
        <span className={`rounded-md px-1.5 py-0.5 ${tone}`}>
          {run.mode === "patch"
            ? "返修"
            : run.mode === "segment" && run.segment_no
              ? `第 ${run.segment_no} 段`
              : "整集"}{" "}
          · #{run.id}
        </span>
      </div>
      <div className="flex items-center justify-between text-foreground-700">
        <span>{phase}</span>
        <span>{run.cost > 0 ? `${run.cost} 积分` : "0 积分"}</span>
      </div>
    </div>
  );
}
