/** 段列表：段号/状态/镜数与时长；可选点击选中和自定义操作。 */
import type { ReactNode } from "react";
import { GitBranch } from "lucide-react";
import type { DramaSegment } from "../../types";
import {
  SEGMENT_ENTRY_LABEL,
  SEGMENT_STATUS_LABEL,
  SEGMENT_STATUS_TONE,
  fmtSecs,
} from "./labels";

export default function SegmentCards({
  segments,
  selectedId = null,
  onSelect,
  renderActions,
  emptyText = "还没有分段；保存分镜后自动分段，或在分镜页点「重新分段」。",
}: {
  segments: DramaSegment[];
  selectedId?: number | null;
  onSelect?: (id: number) => void;
  renderActions?: (segment: DramaSegment) => ReactNode;
  emptyText?: string;
}) {
  if (!segments.length) {
    return <p className="text-xs text-foreground-500">{emptyText}</p>;
  }
  return (
    <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
      {segments.map((segment) => {
        const active = selectedId === segment.id;
        return (
          <div
            key={segment.id}
            onClick={onSelect ? () => onSelect(segment.id) : undefined}
            className={`rounded-xl border p-2.5 text-xs transition ${
              active
                ? "border-primary-300 bg-primary-50"
                : "border-background-200 bg-background-100"
            } ${onSelect ? "cursor-pointer hover:border-primary-300" : ""}`}
          >
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-medium text-foreground-900">第 {segment.segment_no} 段</span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] ${SEGMENT_STATUS_TONE[segment.status]}`}>
                {SEGMENT_STATUS_LABEL[segment.status]}
              </span>
              {segment.stale && (
                <span
                  className="rounded bg-background-200 px-1.5 py-0.5 text-[10px] text-foreground-500"
                  title="段成片缺失或与最新分镜不一致"
                >
                  旧版
                </span>
              )}
              <span className="inline-flex items-center gap-0.5 rounded bg-background-200 px-1.5 py-0.5 text-[10px] text-foreground-600">
                <GitBranch size={10} />
                {SEGMENT_ENTRY_LABEL[segment.entry_mode] || segment.entry_mode}
              </span>
            </div>
            <p className="mt-1 truncate text-[11px] text-foreground-600" title={segment.title}>
              {segment.title || "（未命名）"}
            </p>
            <p className="mt-1 text-[10px] text-foreground-500">
              {segment.shot_count} 镜 · {fmtSecs(segment.duration)} · 预估 {segment.estimate_cost} 积分
              {segment.entry_mode === "continue" &&
                (segment.bridge_url ? " · 桥接已建" : " · 桥接待建")}
            </p>
            {segment.last_error && (
              <p className="mt-1 line-clamp-2 text-[10px] text-rose-600">失败：{segment.last_error}</p>
            )}
            {renderActions && (
              <div
                className="mt-1.5 flex flex-wrap items-center gap-1.5"
                onClick={(event) => event.stopPropagation()}
              >
                {renderActions(segment)}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
