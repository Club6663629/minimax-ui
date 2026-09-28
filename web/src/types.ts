/** 与后端 schema 对齐的类型定义。 */

export interface User {
  id: number;
  email: string;
  username: string;
  role: "user" | "admin";
  credits: number;
  created_at: string;
}

export interface TokenOut {
  access_token: string;
  token_type: string;
  user: User;
}

export interface UploadOut {
  id: number;
  slot: string;
  filename: string;
  url: string;
}

/** 资产管理页的上传素材列表项。 */
export interface UploadListItem {
  id: number;
  slot: string;
  filename: string;
  url: string;
  kind: "image" | "video" | "audio";
  created_at: string;
}

export type TaskStatus =
  | "queued"
  | "enhancing"
  | "generating_768p"
  | "upscaling"
  | "done"
  | "failed";

export interface Task {
  id: number;
  mode: "t2v" | "flf2v" | "r2v" | "director";
  prompt: string;
  enhanced_prompt: string;
  aspect_ratio: string;
  duration: number;
  resolution: string;
  enhance: boolean;
  scene: string;
  status: TaskStatus;
  error: string;
  cost: number;
  video_url: string | null;
  upscale_urls: Record<string, string>;
  first_image_url: string | null;
  last_image_url: string | null;
  ref_image_urls: string[];
  ref_video_urls: string[];
  ref_audio_urls: string[];
  parent_task_id: number | null;
  upscale_target: string | null;
  worker_url: string;
  // 导演台段清单（后端吸附后的 frames/start_frame/end_frame/seed 原样回传）
  segments: DirectorSegment[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  user_email?: string | null;
}

/** 导演台单段（后端 plan_director_segments 输出 + 提交时的输入字段）。 */
export interface DirectorSegment {
  prompt: string;
  duration?: number;
  ref_image_ids?: number[];
  index?: number;
  frames?: number;
  start_frame?: number;
  end_frame?: number;
  seed?: number;
}

export interface CreditLog {
  id: number;
  amount: number;
  type: "signup" | "consume" | "refund" | "redeem" | "adjust";
  note: string;
  task_id: number | null;
  created_at: string;
}

export interface Package {
  name: string;
  credits: number;
  price: string;
  tag: string;
  description: string;
}

export interface Pricing {
  signup_bonus: number;
  cost_768p_5s: number;
  cost_768p_8s: number;
  cost_768p_10s: number;
  cost_768p_15s: number;
  cost_1k_extra: number;
  cost_2k_extra: number;
  cost_4k_extra: number;
  cloud_enabled: boolean;
  upscale_enabled: boolean;
  packages: Package[];
}

export interface AdminUser {
  id: number;
  email: string;
  username: string;
  role: string;
  credits: number;
  task_count: number;
  created_at: string;
}

export interface RedeemCodeOut {
  id: number;
  code: string;
  value: number;
  status: "unused" | "used";
  used_by_email: string | null;
  used_at: string | null;
  created_at: string;
}

export interface AdminStats {
  user_count: number;
  task_count: number;
  today_tasks: number;
  queued: number;
  running: number;
  done: number;
  failed: number;
  credits_consumed: number;
}

/** Worker 池监控（集群调度） */
export interface WorkerInfo {
  url: string;
  role: "generate" | "upscale";
  tags: string[];
  healthy: boolean;
  busy: boolean;
  task_id: number | null;
  consecutive_fails: number;
  // 云端实例（clouds/*.env）；非云节点为 null / false
  platform: string | null;
  instance_id: string | null;      // 形如 autodl:pro-7889ca37d10f
  instance_uuid: string | null;
  instance_status: string | null;  // running / shutdown / ...
  op_state: string | null;         // idle | starting | stopping
  power_controllable: boolean;
  // 最近一次手动开关机结果（后端持久化；刷新页面后仍能显示失败原因）
  last_op_action: "on" | "off" | "status" | null;
  last_op_ok: boolean | null;
  last_op_code: string | null;
  last_op_msg: string | null;
  last_op_at: number | null;       // unix 秒
}

export interface WorkerPoolOut {
  mock: boolean;
  workers: WorkerInfo[];
  queued: number;
  generating: number;
  upscaling: number;
}

// 云端实例开机/关机结果
export interface CloudPowerResult {
  ok: boolean;
  action: "on" | "off";
  instance: string;
  status: string;
  instance_status?: string | null;
  eta_s?: number | null;
  already?: boolean;
  msg?: string;
  request_id?: string;
}

export const ACTIVE_STATUSES: TaskStatus[] = [
  "queued",
  "enhancing",
  "generating_768p",
  "upscaling",
];

export const STATUS_LABEL: Record<TaskStatus, string> = {
  queued: "排队中",
  enhancing: "提示词增强中",
  generating_768p: "视频生成中",
  upscaling: "高清升级中",
  done: "已完成",
  failed: "失败",
};

export const RES_LABEL: Record<string, string> = {
  "768p": "768P",
  "1k": "1K",
  "2k": "2K",
  "4k": "4K",
};

export const MODE_LABEL: Record<Task["mode"], string> = {
  t2v: "文生视频",
  flf2v: "首尾帧",
  r2v: "全能参考",
  director: "导演台",
};
