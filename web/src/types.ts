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
  /** 内容 md5 指纹（后端上传时计算） */
  md5?: string;
  size?: number;
  /** true = 命中库中同内容既有素材（未重复入库，直接复用） */
  duplicate?: boolean;
}

/** 资产管理页的上传素材列表项。 */
export interface UploadListItem {
  id: number;
  slot: string;
  filename: string;
  url: string;
  kind: "image" | "video" | "audio";
  created_at: string;
  /** 内容 md5 指纹（历史记录首次列出时后端补算） */
  md5: string;
  size: number;
  /** 同内容素材份数，>1 表示有重复 */
  dup_count: number;
}

/** 一组重复素材（同一 md5）。 */
export interface DedupGroup {
  md5: string;
  kind: "image" | "video" | "audio";
  filename: string;
  url: string;
  created_at: string;
  size: number;
  count: number;
  keep_id: number;
  remove_ids: number[];
  removable_bytes: number;
  protected_count: number;
}

/** 去重预览：可去除的重复素材汇总。 */
export interface DedupPreview {
  groups: DedupGroup[];
  group_count: number;
  removable_count: number;
  removable_bytes: number;
  protected_count: number;
}

/** 去重执行结果。 */
export interface DedupResult {
  removed: number;
  freed_bytes: number;
  groups: number;
  protected_count: number;
}

export type TaskStatus =
  // 电商广告片（advideo）图像阶段
  | "queued_images"
  | "generating_images"
  | "image_ready"
  | "queued"
  | "enhancing"
  | "generating_768p"
  | "upscaling"
  | "done"
  | "failed";

export interface Task {
  id: number;
  mode: "t2v" | "flf2v" | "r2v" | "director" | "advideo";
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
  // 电商广告片（advideo）：stage=images_queued/images_running/image_ready/video
  stage: string;
  /** 候选广告图地址（/files/adimage/<id>?index=N，需 fileUrl 附 token） */
  ad_image_urls: string[];
  /** 图像阶段提示词（与视频提示词隔离） */
  image_prompt: string;
  /** 人工确认选中的候选序号（-1 = 未确认） */
  chosen_index: number;
  chosen_image_url: string | null;
  /** 本次任务已扣的广告图积分（cost_ad_image × 张数）；与视频费 task.cost 独立计。0/缺省 = 未扣或已退 */
  ad_image_cost?: number;
  /** 自动模式：true=候选图出完自动确认（选第 1 张）直接进视频阶段，无需人工点击 */
  auto_confirm?: boolean;
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
  /** 每张候选广告图的积分单价（提交生成时按张数预扣；出图阶段系统失败全额退还） */
  cost_ad_image: number;
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
  role: "generate" | "upscale" | "image";   // image = 广告图节点
  tags: string[];
  healthy: boolean;
  busy: boolean;
  task_id: number | null;
  consecutive_fails: number;
  /** 间隙跑间隔（秒）：节点标签 gap:<n> 的固有属性；非间隙节点为 null */
  gap_sec: number | null;
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
  // 广告片图像阶段（进行中才需要轮询；image_ready 为等待人工确认，不轮询）
  "queued_images",
  "generating_images",
];

export const STATUS_LABEL: Record<TaskStatus, string> = {
  queued_images: "广告图排队中",
  generating_images: "广告图生成中",
  image_ready: "待确认广告图",
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
  advideo: "电商广告片",
};


// ---- 法律文本（用户协议 V2）----
export interface LegalDoc {
  key: string;
  title: string;
  path: string;
  version: string;
}

export interface LegalCurrent {
  version: string;
  updated: string;
  effective: string;
  docs: LegalDoc[];
}

// ---------------------------------------------------------------- 短剧流水线
// 七阶段：剧本 → 角色 → 分镜 → 就绪 → 生成 → 审片 → 交付
export type DramaEpisodeStatus =
  | "draft"
  | "script_ready"
  | "cast_ready"
  | "shots_ready"
  | "rendering"
  | "review"
  | "done";

export type DramaShotStatus = "ready" | "rendered" | "approved" | "rejected";
export type DramaRunStatus = "queued" | "postprocess" | "done" | "failed" | "cancelled";

export interface DramaStatus {
  enabled: boolean;
  admin_only: boolean;
  /** 当前账号能否使用：已启用，且非测试期或本人是管理员 */
  available: boolean;
  director_enabled: boolean;
  director_node_available: boolean;
  pricing: { cost_768p_5s: number; block_seconds: number };
  limits: { max_shots: number; max_seconds: number; min_shot: number; max_shot: number };
}

export interface DramaBeat {
  id: string;
  title: string;
  summary: string;
  mood: string;
  location: string;
  characters: string[];
  target_seconds: number;
  dialogue_hint: string;
}

export interface DramaCharacter {
  id: number;
  project_id: number;
  name: string;
  role: "main" | "support" | "extra" | string;
  gender: string;
  age: string;
  appearance: string;
  personality: string;
  costume: string;
  voice: string;
  card_upload_id: number | null;
  card_url: string | null;
  locked: boolean;
  first_episode_id: number | null;
}

export interface DramaScene {
  id: number;
  episode_id: number;
  scene_no: number;
  name: string;
  location: string;
  time_of_day: string;
  description: string;
  anchor_upload_id: number | null;
  anchor_url: string | null;
}

export interface DramaSeamMetric {
  ssim?: number | null;
  d_y?: number | null;
  audio_db?: number | null;
  level?: "ok" | "warn" | "fail" | string;
}

// ---- AI 辅助（P0+P1）----
/** 所有 AI 接口共用的来源信息。 */
export interface DramaAiMeta {
  source: string;
  provider: string;
  model: string;
  cached: boolean;
}

/** 剧本节拍局部重写的候选值（不落库）。 */
export interface DramaAiBeatCandidate {
  title: string;
  summary: string;
  mood: string;
  location: string;
  dialogue_hint: string;
}

/** 单镜提示词改写候选值（不落库）。 */
export interface DramaAiShotRewrite {
  prompt: string;
  continuity_note: string;
  audio_note: string;
  dialogue: string;
  changes: string[];
}

/** 按定妆图反填的角色字段候选值。 */
export interface DramaAiCharacterFields {
  appearance: string;
  costume: string;
  gender: string;
  age: string;
}

export interface DramaAiCharacterDiff {
  field: string;
  in_image: string;
  in_text: string;
  severity: "high" | "mid" | "low" | string;
}

export type DramaAiIssueType =
  | "identity"
  | "artifact"
  | "text"
  | "composition"
  | "lighting"
  | "continuity";

export interface DramaAiPrecheckIssue {
  type: DramaAiIssueType | string;
  severity: "high" | "mid" | "low" | string;
  evidence: string;
  suggestion: string;
}

export interface DramaAiPrecheckShot {
  shot_no: number;
  score: number;
  issues: DramaAiPrecheckIssue[];
}

/** 落在 `shot.qa.ai` 里的预检结果；spec_hash 变化后由后端标 stale。 */
export interface DramaAiShotQa {
  spec_hash?: string;
  scope?: "episode" | "shot" | string;
  score?: number;
  issues?: DramaAiPrecheckIssue[];
  at?: string;
  stale?: boolean;
}

/** 交付文案候选值。 */
export interface DramaAiCopy {
  title: string;
  logline: string;
  synopsis: string;
  cover_prompt: string;
}

export interface DramaShot {
  id: number;
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
  status: DramaShotStatus;
  version_no: number;
  repair_count: number;
  repair_cost_total: number;
  clip_url: string | null;
  stale: boolean;
  qa: { in?: DramaSeamMetric; out?: DramaSeamMetric; ai?: DramaAiShotQa };
}

/** `/shots/plan` 响应与 `PUT /shots` 入参共用的草稿字段。 */
export interface DramaShotDraft {
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
}

export interface DramaEpisodeStats {
  shots: number;
  total_seconds: number;
  rendered: number;
  approved: number;
  rejected: number;
  stale: number;
  repair_cost_total: number;
  full_cost: number;
}

export interface DramaEpisode {
  id: number;
  project_id: number;
  episode_no: number;
  title: string;
  brief: string;
  status: DramaEpisodeStatus;
  spec_hash: string;
  final_duration: number;
  final_url: string | null;
  final_stale: boolean;
  last_error: string;
  beats: DramaBeat[];
  script: string;
  stats: DramaEpisodeStats;
  /** 本集所有 run 的实际消耗合计 */
  spent: number;
  run_count: number;
  /** 当前镜头版本去重数量 */
  version_count: number;
  shots: DramaShot[];
  active_run: { id: number; status: string } | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface DramaEpisodeSummary {
  id: number;
  episode_no: number;
  title: string;
  status: DramaEpisodeStatus;
  final_url: string | null;
  final_duration: number;
  shot_count: number;
}

export interface DramaProject {
  id: number;
  user_id: number;
  title: string;
  logline: string;
  genre: string;
  style_bible: string;
  aspect_ratio: string;
  target_episodes: number;
  status: string;
  created_at: string | null;
  updated_at: string | null;
  episode_count?: number;
  episodes?: DramaEpisodeSummary[];
}

export interface DramaProjectDetail {
  project: DramaProject;
  episodes: DramaEpisode[];
  characters: DramaCharacter[];
}

export interface DramaEpisodeDetail {
  episode: DramaEpisode;
  project: DramaProject;
  characters: DramaCharacter[];
  scenes: DramaScene[];
}

export interface DramaRunShot {
  shot_no: number;
  clip_url: string | null;
  version_no: number;
  status: DramaShotStatus;
  stale: boolean;
}

export interface DramaRun {
  id: number;
  mode: "full" | "patch";
  status: DramaRunStatus;
  /** 展示用派生阶段：queued / generating / postprocess / done / failed / cancelled */
  phase: string;
  task_id: number;
  task_status: string;
  task_error: string;
  error: string;
  cost: number;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  shot_ids: number[];
  master_url: string | null;
  shots: DramaRunShot[];
}

export interface DramaIssue {
  code: string;
  message: string;
  shot_no?: number;
}

export interface DramaPlanSegment {
  shot_no: number;
  frames: number;
  visible_frames: number;
  ref_count: number;
  prompt: string;
}

export interface DramaChecklist {
  ready: boolean;
  shots: number;
  total_seconds: number;
  est_cost: number;
  balance: number;
  sufficient: boolean;
  issues: DramaIssue[];
  warnings: DramaIssue[];
  plan: {
    total_frames: number;
    fps: number;
    segments: DramaPlanSegment[];
    windows: { shot_no: number; frames: number; duration: number }[];
  } | null;
}

export interface DramaSeam {
  between: [number, number];
  ssim: number | null;
  d_y: number | null;
  audio_db: number | null;
  level: string;
}

export interface DramaReview {
  episode: DramaEpisode;
  stats: DramaEpisodeStats;
  shots: DramaShot[];
  seams: DramaSeam[];
  sheet_url: string;
  warnings: DramaIssue[];
}

export interface DramaArtifactVersion {
  id: number;
  version_no: number;
  url: string | null;
  duration: number;
  frames: number;
  is_current: boolean;
  stale: boolean;
  created_at: string | null;
}

export interface DramaRenderResult {
  reused: boolean;
  run: DramaRun;
  cost: number;
}

