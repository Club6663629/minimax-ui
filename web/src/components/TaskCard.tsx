/** 任务卡片：状态 / 配置 / 输入媒体 / 结果视频 / 升级与重试；导演台任务附带分段信息。 */
import {
  AlertCircle,
  ArrowUpCircle,
  Check,
  Download,
  Loader2,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { api, fileUrl } from "../api/client";
import { humanizeError } from "../lib/humanize";
import {
  ACTIVE_STATUSES,
  MODE_LABEL,
  RES_LABEL,
  STATUS_LABEL,
  type Pricing,
  type Task,
  type TaskStatus,
} from "../types";

type RefKind = "image" | "video" | "audio";

export function StatusBadge({ status }: { status: TaskStatus }) {
  const color =
    status === "done"
      ? "bg-emerald-500/15 text-emerald-700"
      : status === "failed"
        ? "bg-rose-500/15 text-rose-700"
        : "bg-primary-500/15 text-primary-700";
  const spinning = ACTIVE_STATUSES.includes(status);
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs ${color}`}>
      {spinning && <Loader2 size={11} className="animate-spin" />}
      {STATUS_LABEL[status]}
    </span>
  );
}

export default function TaskCard({
  task,
  onChanged,
  pricing,
  onRefreshUser,
}: {
  task: Task;
  onChanged: () => void;
  pricing: Pricing | null;
  onRefreshUser?: () => void;
}) {
  const [retrying, setRetrying] = useState(false);
  const [upgrading, setUpgrading] = useState<"1k" | "2k" | "4k" | null>(null);

  async function retry() {
    setRetrying(true);
    try {
      await api.retryVideo(task.id);
      onChanged();
    } catch (err) {
      alert(err instanceof Error ? err.message : "重试失败");
    } finally {
      setRetrying(false);
    }
  }

  async function upgrade(res: "1k" | "2k" | "4k") {
    setUpgrading(res);
    try {
      await api.upgradeVideo(task.id, res);
      await onChanged();
      onRefreshUser?.();
    } catch (err) {
      alert(err instanceof Error ? err.message : "升级失败");
    } finally {
      setUpgrading(null);
    }
  }

  async function remove() {
    if (!confirm("删除该任务记录？")) return;
    try {
      await api.deleteVideo(task.id);
      onChanged();
    } catch (err) {
      alert(err instanceof Error ? err.message : "删除失败");
    }
  }

  const inputMedia: { kind: RefKind; url: string; label: string }[] = [
    ...(task.first_image_url ? [{ kind: "image" as const, url: task.first_image_url, label: "首帧" }] : []),
    ...(task.last_image_url ? [{ kind: "image" as const, url: task.last_image_url, label: "尾帧" }] : []),
    ...task.ref_image_urls.map((url, i) => ({ kind: "image" as const, url, label: `参考图 ${i + 1}` })),
    ...task.ref_video_urls.map((url, i) => ({ kind: "video" as const, url, label: `参考视频 ${i + 1}` })),
    ...task.ref_audio_urls.map((url, i) => ({ kind: "audio" as const, url, label: `参考音频 ${i + 1}` })),
  ];

  const isUpgradeTask = task.parent_task_id != null;
  const isDirector = task.mode === "director";
  const upgradeLabel = isUpgradeTask && task.upscale_target ? `升级 → ${RES_LABEL[task.upscale_target] ?? task.upscale_target}` : null;

  return (
    <div className="panel p-4">
      <div className="mb-2 flex items-center gap-2">
        <StatusBadge status={task.status} />
        <span className="chip">{MODE_LABEL[task.mode]}</span>
        <span className="chip">{task.aspect_ratio}</span>
        {isDirector ? (
          <span className="chip">共 {task.segments.length} 段 · {task.duration}s</span>
        ) : (
          <span className="chip">{task.duration}s</span>
        )}
        <span className="chip">{RES_LABEL[task.resolution] ?? task.resolution}</span>
        {upgradeLabel && <span className="chip !border-accent-500/30 !text-accent-700">{upgradeLabel}</span>}
        <span className="ml-auto text-xs text-foreground-500">
          {new Date(task.created_at).toLocaleString("zh-CN", { hour12: false })}
        </span>
      </div>

      {inputMedia.length > 0 && (
        <div className="mb-3 flex gap-2 overflow-x-auto">
          {inputMedia.map((m) =>
            m.kind === "image" ? (
              <img
                key={m.url}
                src={fileUrl(m.url)}
                alt={m.label}
                title={m.label}
                className="h-14 w-20 shrink-0 rounded-lg border border-background-300 object-cover"
              />
            ) : m.kind === "video" ? (
              <video
                key={m.url}
                src={fileUrl(m.url)}
                muted
                preload="metadata"
                title={m.label}
                className="h-14 w-20 shrink-0 rounded-lg border border-background-300 object-cover"
              />
            ) : (
              <audio
                key={m.url}
                src={fileUrl(m.url)}
                controls
                preload="metadata"
                title={m.label}
                className="h-14 w-44 shrink-0"
              />
            ),
          )}
        </div>
      )}

      <p className="mb-3 line-clamp-2 text-sm text-foreground-800" title={task.prompt}>
        {task.prompt}
      </p>

      {/* 导演台：分段信息折叠区 */}
      {isDirector && task.segments.length > 0 && (
        <details className="mb-3 rounded-lg border border-background-200 bg-background-50 px-3 py-2">
          <summary className="cursor-pointer text-xs text-foreground-600 select-none">
            分段信息（{task.segments.length} 段，点击展开）
          </summary>
          <ul className="mt-2 space-y-1.5">
            {task.segments.map((seg, i) => (
              <li key={i} className="text-xs leading-relaxed text-foreground-600">
                <span className="font-medium text-foreground-900">
                  #{i + 1}
                  {seg.frames != null && ` · ${seg.frames} 帧`}
                  {seg.start_frame != null && seg.end_frame != null && ` · 帧窗 ${seg.start_frame}-${seg.end_frame}`}
                </span>
                <span className="ml-2 line-clamp-1" title={seg.prompt}>
                  {seg.prompt}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {task.status === "done" && task.video_url && (
        <div className="space-y-2">
          <video controls preload="metadata" className="max-h-[360px] w-full rounded-xl bg-foreground-950" src={fileUrl(task.video_url)} />
          <div className="flex items-center gap-2">
            <a href={fileUrl(task.video_url)} download={`h3_video_${task.id}.mp4`} className="btn-primary !py-1.5 text-xs">
              <Download size={13} /> 下载视频
            </a>
            {/* 多分辨率下载选项 */}
            {Object.keys(task.upscale_urls).length > 0 && (
              <div className="flex items-center gap-1">
                {Object.entries(task.upscale_urls).map(([res, url]) => (
                  <a
                    key={res}
                    href={fileUrl(url)}
                    download={`h3_video_${task.id}_${res}.mp4`}
                    className="inline-flex items-center gap-1 rounded-full border border-background-300 bg-background-100 px-2 py-0.5 text-[10px] text-foreground-600 transition hover:border-primary-400 hover:text-foreground-900"
                  >
                    <Download size={10} /> {RES_LABEL[res]}
                  </a>
                ))}
              </div>
            )}
            <span className="text-xs text-foreground-500">消耗 {task.cost} 积分</span>
            <button onClick={remove} className="ml-auto rounded-lg p-1.5 text-foreground-500 hover:bg-background-200 hover:text-rose-600" title="删除">
              <Trash2 size={14} />
            </button>
          </div>
          {/* 768p 已完成：显示高清升级按钮（导演台长视频不支持升级） */}
          {task.resolution === "768p" && !isUpgradeTask && !isDirector && (
            <div className="flex items-center gap-2 border-t border-background-200 pt-2">
              <ArrowUpCircle size={13} className="text-accent-600" />
              <span className="text-xs text-foreground-500">高清升级：</span>
              {(["2k", "4k"] as const).map((res) => {
                const extraCost = res === "2k" ? (pricing?.cost_2k_extra ?? 15) : (pricing?.cost_4k_extra ?? 30);
                const hasUpgraded = Object.keys(task.upscale_urls).includes(res);
                return (
                  <button
                    key={res}
                    onClick={() => upgrade(res)}
                    disabled={upgrading !== null || hasUpgraded}
                    className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs transition ${
                      hasUpgraded
                        ? "cursor-not-allowed border border-background-200 bg-background-100 text-foreground-500"
                        : "border border-accent-500/30 bg-accent-500/10 text-accent-700 hover:border-accent-500/50 hover:bg-accent-500/20"
                    } disabled:cursor-not-allowed`}
                  >
                    {upgrading === res ? <Loader2 size={11} className="animate-spin" /> : null}
                    {RES_LABEL[res]}
                    <span className="text-[10px] opacity-60">{extraCost}积分</span>
                    {hasUpgraded && <Check size={10} className="text-emerald-600" />}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {task.status === "failed" && (
        <div className="flex items-start gap-3 rounded-xl bg-rose-500/[0.07] px-3 py-2.5">
          <AlertCircle size={15} className="mt-0.5 shrink-0 text-rose-600" />
          <div className="min-w-0 flex-1">
            <p className="text-xs text-rose-600">{humanizeError(task.error, "生成失败，积分已退还")}</p>
            {task.error && (
              <details className="mt-1">
                <summary className="cursor-pointer text-[11px] text-foreground-500">查看详情</summary>
                <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap rounded-lg bg-background-100 p-2 text-[11px] text-foreground-600">
                  {task.error}
                </pre>
              </details>
            )}
          </div>
          <button onClick={retry} disabled={retrying} className="btn-ghost !px-3 !py-1.5 text-xs">
            <RotateCcw size={13} /> {retrying ? "提交中…" : "重试"}
          </button>
        </div>
      )}

      {ACTIVE_STATUSES.includes(task.status) && (
        <div className="flex items-center gap-2 text-xs text-foreground-500">
          <div className="h-1 flex-1 overflow-hidden rounded-full bg-background-200">
            <div className="h-full w-1/3 animate-pulse rounded-full bg-gradient-to-r from-primary-500 to-accent-500" />
          </div>
          {isUpgradeTask
            ? task.status === "upscaling"
              ? task.worker_url
                ? "高清升级中，请耐心等待…"
                : "高清升级排队中"
              : "处理中，请耐心等待"
            : isDirector
              ? "导演台独占整卡生成中（长视频耗时较长，请耐心等待）"
              : "多卡并行处理中，请耐心等待"}
        </div>
      )}
    </div>
  );
}
