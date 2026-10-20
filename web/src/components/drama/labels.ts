/** 短剧文案与轻量格式化工具：各阶段组件共用，避免文案漂移。 */
import type { DramaEpisodeStatus, DramaSegmentStatus, DramaShotStatus } from "../../types";

export const EPISODE_STATUS_LABEL: Record<DramaEpisodeStatus, string> = {
  draft: "草稿",
  script_ready: "剧本就绪",
  cast_ready: "角色就绪",
  shots_ready: "分镜就绪",
  rendering: "生成中",
  review: "审片中",
  done: "已交付",
};

export const EPISODE_STATUS_TONE: Record<DramaEpisodeStatus, string> = {
  draft: "bg-background-200 text-foreground-600",
  script_ready: "bg-primary-100 text-primary-700",
  cast_ready: "bg-primary-100 text-primary-700",
  shots_ready: "bg-primary-100 text-primary-700",
  rendering: "bg-amber-100 text-amber-700",
  review: "bg-sky-100 text-sky-700",
  done: "bg-emerald-100 text-emerald-700",
};

export const SHOT_STATUS_LABEL: Record<DramaShotStatus, string> = {
  ready: "待生成",
  rendered: "待审",
  approved: "已通过",
  rejected: "待返修",
};

export const SHOT_STATUS_TONE: Record<DramaShotStatus, string> = {
  ready: "bg-background-200 text-foreground-600",
  rendered: "bg-sky-100 text-sky-700",
  approved: "bg-emerald-100 text-emerald-700",
  rejected: "bg-rose-100 text-rose-700",
};

export const SEGMENT_STATUS_LABEL: Record<DramaSegmentStatus, string> = {
  ready: "待生成",
  rendering: "生成中",
  review: "待审",
  approved: "已通过",
};

export const SEGMENT_STATUS_TONE: Record<DramaSegmentStatus, string> = {
  ready: "bg-background-200 text-foreground-600",
  rendering: "bg-amber-100 text-amber-700",
  review: "bg-sky-100 text-sky-700",
  approved: "bg-emerald-100 text-emerald-700",
};

export const SEGMENT_ENTRY_LABEL: Record<string, string> = {
  cut: "硬切",
  continue: "承接上段",
};

export const RUN_PHASE_LABEL: Record<string, string> = {
  queued: "排队中",
  generating: "生成中",
  postprocess: "后处理中",
  done: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export function fmtSecs(value: number | null | undefined, digits = 1): string {
  if (!value) return "0.0s";
  return `${value.toFixed(digits)}s`;
}

export function fmtTime(value: string | null | undefined): string {
  if (!value) return "-";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

/** ApiError/普通 Error → 可直接展示的中文文案。 */
export function errText(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

export function shotDraftFromShot(shot: {
  shot_no: number;
  scene_no: number;
  beat_id: string;
  prompt: string;
  duration_s: number;
  shot_size: string;
  camera: string;
  movement: string;
  character_ids: number[];
  ref_upload_ids: number[];
  continuity_note: string;
  dialogue: string;
  audio_note: string;
}) {
  return {
    shot_no: shot.shot_no,
    scene_no: shot.scene_no,
    beat_id: shot.beat_id,
    prompt: shot.prompt,
    duration_s: shot.duration_s,
    shot_size: shot.shot_size,
    camera: shot.camera,
    movement: shot.movement,
    character_ids: [...shot.character_ids],
    ref_upload_ids: [...shot.ref_upload_ids],
    continuity_note: shot.continuity_note,
    dialogue: shot.dialogue,
    audio_note: shot.audio_note,
  };
}
