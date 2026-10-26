/** 剧集工坊的展示常量与小工具。视图规范与后端 `studio/prompts.py` 保持一致。 */
import type { StudioAssetView } from "../../types";

export function errText(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

export const GATE_LABEL: Record<string, string> = {
  outline: "大纲",
  script: "剧本",
  cast: "角色",
  art: "美术",
  staging: "分段",
  storyboard: "切表",
  series: "索引",
};

export const ASSET_KIND_LABEL: Record<string, string> = {
  character: "角色",
  scene: "场景",
  prop: "道具",
};

/** 景别 → 中文。与 shuohao `SHOT_SIZES` 一致。 */
export const SHOT_SIZE_LABEL: Record<string, string> = {
  "extreme-wide": "大远景",
  wide: "全景",
  medium: "中景",
  close: "特写",
  "extreme-close": "大特写",
};

/** 运镜 → 中文。H3 官方词表，与 shuohao `CAMERA_MOVES` 一致。 */
export const CAMERA_LABEL: Record<string, string> = {
  "Static Shot": "固定",
  "Push In": "推",
  "Pull Out": "拉",
  "Zoom In": "变焦推",
  "Zoom Out": "变焦拉",
  "Pan Left": "左摇",
  "Pan Right": "右摇",
  "Truck Left": "左移",
  "Truck Right": "右移",
  "Tilt Up": "仰摇",
  "Tilt Down": "俯摇",
  "Pedestal Up": "升",
  "Pedestal Down": "降",
  "Arc Shot": "环绕",
  "Tracking Shot": "跟拍",
  "Shake Slightly": "轻微晃动",
  "Shake Strongly": "强烈晃动",
  POV: "主观视角",
  "Roll Clockwise": "顺旋",
  "Roll Counterclockwise": "逆旋",
};

export const STABILITY_LABEL: Record<string, string> = {
  stable: "稳定",
  "slight-shake": "微晃",
  handheld: "手持",
};

/** 视图 → 画幅与中文名。 */
export const VIEW_SPECS: Record<string, { ratio: string; label: string }> = {
  "front-full": { ratio: "2:3", label: "正面全身" },
  "face-front": { ratio: "4:5", label: "正脸" },
  "side-full": { ratio: "2:3", label: "侧面" },
  "back-full": { ratio: "2:3", label: "背面" },
  sheet: { ratio: "2:3", label: "设定图" },
};

/** 每类资产要出哪几个视图（角色四视图，场景/道具单张）。 */
export const ASSET_VIEWS: Record<string, string[]> = {
  character: ["front-full", "face-front", "side-full", "back-full"],
  scene: ["sheet"],
  prop: ["sheet"],
};

export const ASSET_STATUS_LABEL: Record<string, string> = {
  draft: "待出图",
  imaging: "出图中",
  ready: "已就绪",
};

/** 同一视图取最高版本——列表里只展示最新一张，历史版本留给后端。 */
export function latestViews(views: StudioAssetView[]): Record<string, StudioAssetView> {
  const out: Record<string, StudioAssetView> = {};
  for (const view of views) {
    const cur = out[view.view];
    if (!cur || view.version >= cur.version) out[view.view] = view;
  }
  return out;
}
