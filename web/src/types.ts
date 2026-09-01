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

export type TaskStatus =
  | "queued"
  | "enhancing"
  | "generating_768p"
  | "upscaling_2k"
  | "done"
  | "failed";

export interface Task {
  id: number;
  mode: "t2v" | "flf2v" | "r2v";
  prompt: string;
  enhanced_prompt: string;
  aspect_ratio: string;
  duration: number;
  resolution: string;
  enhance: boolean;
  status: TaskStatus;
  error: string;
  cost: number;
  video_url: string | null;
  first_image_url: string | null;
  last_image_url: string | null;
  ref_image_urls: string[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  user_email?: string | null;
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
  cost_768p_10s: number;
  cost_2k_extra: number;
  cloud_enabled: boolean;
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

export const ACTIVE_STATUSES: TaskStatus[] = [
  "queued",
  "enhancing",
  "generating_768p",
  "upscaling_2k",
];

export const STATUS_LABEL: Record<TaskStatus, string> = {
  queued: "排队中",
  enhancing: "提示词增强中",
  generating_768p: "视频生成中",
  upscaling_2k: "2K 升级中",
  done: "已完成",
  failed: "失败",
};

export const MODE_LABEL: Record<Task["mode"], string> = {
  t2v: "文生视频",
  flf2v: "首尾帧",
  r2v: "全能参考",
};
