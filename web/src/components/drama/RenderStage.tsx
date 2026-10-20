/** S5 生成：段进度、当前段 run 阶段条、失败动作（重试 / 修复后处理 / 取消）。 */
import { useEffect, useState } from "react";
import {
  CheckCircle2,
  Circle,
  Clock3,
  Download,
  Layers,
  Loader2,
  PlayCircle,
  RotateCcw,
  Wrench,
  XCircle,
} from "lucide-react";
import type { DramaEpisode, DramaRun } from "../../types";
import { fileUrl } from "../../api/client";
import { RUN_PHASE_LABEL, fmtSecs, fmtTime } from "./labels";
import SegmentCards from "./SegmentCards";

const STEPS = [
  { key: "queued", label: "排队" },
  { key: "generating", label: "生成中" },
  { key: "postprocess", label: "后处理（切片 / 拼接 / 质检）" },
  { key: "done", label: "完成" },
];

function stepIndex(phase: string): number {
  if (phase === "queued") return 0;
  if (phase === "generating") return 1;
  if (phase === "postprocess") return 2;
  if (phase === "done") return 3;
  return -1;
}

function elapsedText(run: DramaRun, now: number): string {
  const start = run.started_at || run.created_at;
  if (!start) return "-";
  const end = run.finished_at ? new Date(run.finished_at).getTime() : now;
  const seconds = Math.max(0, Math.round((end - new Date(start).getTime()) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

export default function RenderStage({
  episode,
  run,
  onCancel,
  onRepair,
  onRetry,
  onReuse,
  onRenderMissing,
  onRenderSegment,
  onGoReady,
  onGoShots,
  notify,
}: {
  episode: DramaEpisode;
  run: DramaRun | null;
  onCancel: () => void | Promise<void>;
  onRepair: () => void | Promise<void>;
  onRetry: () => void | Promise<void>;
  onReuse: () => void | Promise<void>;
  onRenderMissing: () => void | Promise<void>;
  onRenderSegment: (segmentId: number, force: boolean) => void | Promise<void>;
  onGoReady: () => void;
  onGoShots: () => void;
  notify: (message: string) => void;
}) {
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!run || ["done", "failed", "cancelled"].includes(run.phase)) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [run]);

  const segments = episode.segments || [];
  const readyCount = segments.filter((item) => !item.stale).length;
  const missing = segments.filter((item) => item.stale);
  const locked = Boolean(episode.active_run);
  const segment = run?.segment_id
    ? segments.find((item) => item.id === run.segment_id) || null
    : null;

  const guard = async (action: () => void | Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  };

  const section = (
    <section className="panel space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="flex items-center gap-1.5 text-sm font-medium text-foreground-900">
          <Layers size={15} /> 段落进度
        </h2>
        <span className="text-xs text-foreground-500">
          {readyCount} / {segments.length} 段就绪
          {missing.length > 0 && ` · 待生成 ${missing.length} 段`}
        </span>
        {missing.length > 0 && !locked && (
          <button
            type="button"
            className="btn-primary ml-auto px-3 py-1.5 text-xs"
            disabled={busy}
            onClick={() => void guard(onRenderMissing)}
            title="按段顺序提交，前一段完成后自动续下一段"
          >
            <PlayCircle size={14} /> 生成缺失段
          </button>
        )}
      </div>
      <SegmentCards
        segments={segments}
        selectedId={segment?.id ?? null}
        renderActions={(item) => (
          <>
            {item.stale && (
              <button
                type="button"
                className="btn-ghost px-2 py-1 text-[11px]"
                disabled={busy || locked}
                onClick={() => void guard(() => onRenderSegment(item.id, false))}
              >
                生成本段
              </button>
            )}
            {item.cut_url && (
              <a
                className="btn-ghost px-2 py-1 text-[11px]"
                href={fileUrl(item.cut_url)}
                target="_blank"
                rel="noreferrer"
              >
                看段成片
              </a>
            )}
          </>
        )}
      />
      <p className="text-xs leading-relaxed text-foreground-500">
        按段顺序生成：每段完成会自动提交下一段；某段失败只停在该段，前面已完成的段不受影响。
        每段约 {segments[0] ? fmtSecs(Math.max(1, segments[0].target_seconds)) : "60.0s"}。
      </p>
    </section>
  );

  if (!run) {
    return (
      <div className="space-y-3">
        {section}
        <div className="panel flex flex-col items-center gap-3 px-6 py-12 text-center">
          <Clock3 size={22} className="text-foreground-400" />
          <p className="text-sm text-foreground-700">当前没有进行中的生成</p>
          <p className="text-xs text-foreground-500">
            到「就绪」提交生成，或在上方直接生成某个缺失段；审片页可对单镜发起返修。
          </p>
          <div className="flex gap-2">
            <button type="button" className="btn-primary" onClick={onGoReady}>
              去「就绪」生成
            </button>
            <button type="button" className="btn-ghost" onClick={onGoShots}>
              回分镜
            </button>
          </div>
        </div>
      </div>
    );
  }

  const current = stepIndex(run.phase);
  const failed = run.phase === "failed" || run.phase === "cancelled";
  const canCancel =
    run.status === "queued" && run.task_status === "queued" && !run.started_at;
  const canRepair = run.phase === "failed" && run.task_status === "done";
  const hasOldFinal = Boolean(episode.final_url);

  return (
    <div className="space-y-3">
      {section}
      <section className="panel space-y-4 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-medium text-foreground-900">
            {run.mode === "patch"
              ? "单镜返修"
              : segment
                ? `第 ${segment.segment_no} 段生成`
                : "整集生成"}{" "}
            · run #{run.id}
          </h2>
          <span
            className={`rounded-md px-2 py-0.5 text-xs ${
              run.phase === "done"
                ? "bg-emerald-100 text-emerald-700"
                : failed
                  ? "bg-rose-100 text-rose-700"
                  : "bg-amber-100 text-amber-700"
            }`}
          >
            {RUN_PHASE_LABEL[run.phase] || run.phase}
          </span>
          <span className="text-xs text-foreground-500">task #{run.task_id}</span>
          <span className="ml-auto text-xs text-foreground-500">
            已耗时 {elapsedText(run, now)}
          </span>
        </div>

        {!failed && (
          <ol className="flex flex-wrap items-center gap-2 text-xs">
            {STEPS.map((step, index) => {
              const done = current > index || run.phase === "done";
              const active = current === index && run.phase !== "done";
              return (
                <li key={step.key} className="flex items-center gap-2">
                  <span
                    className={`inline-flex items-center gap-1 ${
                      done
                        ? "text-emerald-600"
                        : active
                          ? "text-primary-600"
                          : "text-foreground-400"
                    }`}
                  >
                    {done ? (
                      <CheckCircle2 size={14} />
                    ) : active ? (
                      <Loader2 size={14} className="animate-spin" />
                    ) : (
                      <Circle size={14} />
                    )}
                    {step.label}
                  </span>
                  {index < STEPS.length - 1 && <span className="h-px w-5 bg-background-300" />}
                </li>
              );
            })}
          </ol>
        )}

        {segment && (
          <p className="text-xs leading-relaxed text-foreground-600">
            {segment.title || `第 ${segment.segment_no} 段`} · {segment.shot_count} 镜 ·{" "}
            {fmtSecs(segment.duration)} · 场 {segment.scene_nos.join("、") || "-"} ·{" "}
            {segment.entry_mode === "continue" ? "承接上段（桥接帧）" : "硬切"}
          </p>
        )}

        {!failed && (
          <p className="text-xs leading-relaxed text-foreground-500">
            导演台独占整卡；生成中不可取消。页面可以离开，回来会自动从本段活跃批次恢复。
            创作消耗 {run.cost > 0 ? `${run.cost} 积分` : "0 积分"}；失败自动退还。
          </p>
        )}

        {failed && (
          <div className="space-y-2 rounded-xl bg-rose-50 p-3 text-xs text-rose-700">
            <p className="flex items-center gap-1.5 font-medium">
              <XCircle size={14} />
              {run.phase === "cancelled" ? "本次生成已取消" : "本次生成失败，积分已退还"}
            </p>
            <p className="whitespace-pre-wrap leading-relaxed">
              {run.error || run.task_error || "未知错误"}
            </p>
            {run.task_error && run.error && run.task_error !== run.error && (
              <p className="text-[11px] text-rose-600/80">任务错误：{run.task_error}</p>
            )}
            {segment && run.mode === "segment" && (
              <p className="text-[11px] text-rose-600/80">
                重试只重跑第 {segment.segment_no} 段，不会影响其他段。
              </p>
            )}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 border-t border-background-200 pt-3">
          {canCancel && (
            <button
              type="button"
              className="btn-ghost"
              disabled={busy}
              onClick={() => {
                if (!window.confirm("确认取消本次排队中的生成？取消后不扣费。")) return;
                void guard(onCancel);
              }}
            >
              取消生成
            </button>
          )}
          {failed && (
            <>
              <button
                type="button"
                className="btn-primary"
                disabled={busy}
                onClick={() => {
                  if (!window.confirm("重试会重新扣费生成一版新素材，确认继续？")) return;
                  void guard(onRetry);
                }}
              >
                <RotateCcw size={14} /> 重试生成（重新扣费）
              </button>
              {canRepair && (
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={busy}
                  onClick={() => void guard(onRepair)}
                >
                  <Wrench size={14} /> 修复后处理（0 成本）
                </button>
              )}
              {hasOldFinal && (
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={busy}
                  onClick={() => void guard(onReuse)}
                  title="重新提交并在指纹一致时复用旧成片，0 积分"
                >
                  沿用旧成片（0 积分）
                </button>
              )}
              {run.master_url && (
                <a className="btn-ghost" href={fileUrl(run.master_url)} download target="_blank" rel="noreferrer">
                  <Download size={14} /> 下载原始 master
                </a>
              )}
            </>
          )}
          {run.phase === "done" && (
            <span className="text-xs text-emerald-700">
              {run.mode === "patch" ? "本镜返修完成" : "本段生成与后处理完成"}
              ，正在刷新审片数据…
            </span>
          )}
          <button type="button" className="btn-ghost ml-auto" onClick={onGoShots} disabled={busy}>
            回分镜
          </button>
          <span className="text-[11px] text-foreground-400">
            {fmtTime(run.created_at)} 提交
          </span>
        </div>
      </section>
    </div>
  );
}
