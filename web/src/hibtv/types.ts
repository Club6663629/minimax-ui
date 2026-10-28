/** 短剧工作台（HiBTV）接口类型：与 server/app/routers/hibtv.py 的响应一一对应。
 *
 * 口径：文本 7 段由 codex 网关产出（平台只搬字段），像素阶段是「一资产一张单图」+
 * 「每切一张关键帧」+ ref2v 段出片；多视图设定稿 / 4 格裁格已永久下线。
 */

export type DocKind = "outline" | "cast" | "art" | "refs" | "staging" | "script" | "storyboard";
/** 文档种类（refs 落到 cast 文档里，没有独立 doc） */
export type DocFileKind = "outline" | "cast" | "art" | "staging" | "script" | "storyboard";
export type JobKind = DocKind | "images" | "compose";

export interface Gates {
  status?: "ok" | "fail" | "skipped" | "stale" | string;
  passed?: number;
  total?: number;
  failed?: number;
  problems?: string[];
  // 说明行（门未运行的原因 / 展示层降级）：不是缺陷，不计缺口
  notes?: string[];
  checked_at?: string;
  // 门结果来源：.gates.jsonl / report.html 的文件名
  source?: string;
}

/** 产物溯源（docs.*.meta / job.usage 的形状） */
export interface DocTrace {
  stage?: string;
  codex_job_id?: string;
  mainSha8?: string;
  usage?: Record<string, unknown>;
  artifacts?: { path: string; bytes: number }[];
  applied_at?: string;
  report?: string;
  md?: string;
  manifest?: boolean;
  [key: string]: unknown;
}

export interface DocMeta {
  status: string;
  version: number;
  gates: Gates;
  meta: DocTrace;
  error: string;
}

export interface HibtvProject {
  id: number;
  name: string;
  genre: string;
  episodes: number;
  minutesMin: number;
  minutesMax: number;
  aspectRatio: "9:16" | "16:9" | string;
  novelName: string;
  hasNovel: boolean;
  keeps: string;
  detected: Detected;
  status: string;
  createdAt: string;
  updatedAt: string;
  docs: Record<string, DocMeta>;
  assetCount: Record<string, number>;
  /** 已出主图的资产张数（一资产一张） */
  assetMain: number;
  segmentCount: { total: number; done: number };
  /** 关键帧：每切一张 */
  frameCount: { total: number; done: number };
}

export interface Detected {
  episodes?: number;
  scenes?: number;
  lines?: number;
  beats?: number;
  cast?: number;
  scene?: number;
  prop?: number;
  source?: string;
}

export interface HibtvDoc {
  kind: DocFileKind;
  ep: number;
  status: string;
  version: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: any;
  gates: Gates;
  meta: DocTrace;
  error: string;
  updated_at: string;
}

export interface JobTokens {
  in: number;
  cached: number;
  out: number;
}

export interface HibtvJob {
  id: number;
  kind: JobKind;
  ep: number;
  ref: string;
  status: "queued" | "running" | "done" | "failed" | "cancelled" | string;
  progress: number;
  stage: string;
  error: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  payload: any;
  /** 网关侧 job id（文本 7 段才有） */
  codexJobId: string;
  artifacts: { path: string; abs: string; bytes: number }[];
  usage: Record<string, unknown>;
  tokens: JobTokens;
  elapsedSec: number;
  queuePosition: number | null;
  heartbeatAt: string;
  /** TIMEOUT total / TIMEOUT silence / CANCELLED by API / INTERRUPTED by restart */
  timeoutKind: string;
  created_at: string;
  finished_at: string;
}

/** 资产主图（一资产一张，没有视图维度） */
export interface AssetMain {
  path: string;
  url: string;
  uploadId: number;
  version: number;
  meta: Record<string, unknown>;
  at: string;
}

export interface HibtvAsset {
  id: number;
  kind: "cast" | "scene" | "prop" | string;
  code: string;
  name: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: any;
  /** 出图提示词（skill 的 image.prompt 原文） */
  prompt: string;
  negative: string;
  promptLocal: string;
  /** [宽, 高]：cast 896×1152 / scene 1280×720 / prop 1024×1024 */
  size: number[];
  main: AssetMain;
  /** 旧版多视图（只读兼容，历史项目才有） */
  legacyViews: { view: string; url: string; version: number }[];
  status: "empty" | "drawing" | "review" | "done" | string;
  version: number;
  hidden: boolean;
  sort: number;
}

/** 关键帧：f1..fN，与 manifest.pictures / prompt.md 的 Picture 序号一一对应 */
export interface HibtvFrame {
  id: number;
  ep: number;
  segId: string;
  cutIndex: number;
  status: "empty" | "drawing" | "review" | "done" | string;
  prompt: string;
  url: string;
  uploadId: number;
  version: number;
  meta: Record<string, unknown>;
}

export interface FrameList {
  ep: number;
  frames: HibtvFrame[];
  done: number;
  total: number;
}

export interface Cut {
  beats?: number[];
  seconds: number;
  size?: string;
  camera?: string;
  characters?: string[];
  props?: string[];
  frame?: string;
  shot?: string;
  lens?: string;
  cameraPosition?: string;
  composition?: string;
  eyeline?: string;
  focus?: string;
  stability?: string;
}

export interface SegmentVersion {
  v: number;
  task_id?: number;
  path?: string;
  url?: string;
  seconds?: number;
  at?: string;
  failed?: boolean;
  error?: string;
}

export interface Segment {
  id: string;
  ep: number;
  idx: number;
  status: string;
  currentVersion: number;
  versions: SegmentVersion[];
  url: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: any;
  h3Prompt: string;
  cuts: Cut[];
  planSeconds: number;
  /** prompt.md = 原文；storyboard.h3Prompt = 退化来源 */
  promptSource: string;
  promptSha8: string;
  frameCount: number;
  pictureCount: number;
  manifestSeconds: number;
  /** 段长超过 13s：拦出片 + 提示重跑本集 storyboard */
  oversize: boolean;
  // 段详情才带：图位条与出片请求值
  frames?: HibtvFrame[];
  missingFrames?: number[];
  framesReady?: boolean;
  request?: { frames: number; seconds: number };
  capSeconds?: number;
}

export interface SegmentList {
  ep: number;
  segments: Segment[];
  done: number;
  total: number;
}

export interface ChangeItem {
  id: number;
  who: string;
  what: string;
  path: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  from: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  to: any;
  reverted: boolean;
  at: string;
}

export interface RenderItem {
  id: number;
  ep: number;
  version: number;
  path: string;
  url: string;
  durationSec: number;
  size: number;
  // 后端 cutlist 行：{index, file, plan, actual, start}（plan/actual 为 ffprobe 实测秒数）
  cutlist: { index?: number; file?: string; plan?: number; actual?: number; start?: number }[];
  createdAt: string;
}

export interface LibraryItem {
  id: string;
  kind: "image" | "video";
  /** cast / scene / prop / frame / segment / render */
  group: string;
  code: string;
  name: string;
  url: string;
  version: number;
  at: string;
}

export interface HibtvStatus {
  enabled: boolean;
  codex: {
    available: boolean;
    url: string;
    tokenPath: string;
    hasToken: boolean;
    projectPrefix: string;
    note: string;
  };
  image: { available?: boolean; worker?: string; template?: string; degraded?: boolean; note?: string };
  gates: { mode?: string; available: boolean; note?: string };
  compose: { available?: boolean; ffmpeg?: string; note?: string };
  segMaxSeconds: number;
  /** 13s 上限对应的合法帧数（311 帧 = 12.9583s） */
  segMaxFrames: number;
  frameSize: Record<string, number[]>;
  assetSizes: Record<string, number[]>;
  promptLang: string;
  cost: { image: number; video: Record<string, number> };
  credits: number;
}

/** GET /ai/codex/status（掉线时后端 503，body 就是这份的最小集） */
export interface CodexStatus {
  available: boolean;
  url: string;
  tokenPath: string;
  hasToken: boolean;
  projectPrefix: string;
  note: string;
  healthz?: {
    ok?: boolean;
    codex?: string;
    skills?: number;
    deepseek?: boolean;
    disk?: string;
    [key: string]: unknown;
  };
  skills?: { name: string; description?: string; [key: string]: unknown }[];
  stats?: {
    total?: number;
    byStage?: {
      stage: string;
      jobs?: number;
      done?: number;
      failed?: number;
      input_tokens?: number;
      output_tokens?: number;
      elapsed_sec?: number;
    }[];
    [key: string]: unknown;
  };
}

export interface ArtifactItem {
  path: string;
  name: string;
  bytes: number;
  kind: "html" | "md" | "json" | "other";
  url: string;
}

export interface ArtifactList {
  jobId: number;
  kind: string;
  ep: number;
  status: string;
  codexJobId: string;
  gates: Record<string, unknown>;
  items: ArtifactItem[];
  count: number;
}

/* ------------------------------------------------------------------ skill 产物结构 */
export interface OutlineData {
  source?: string;
  params?: {
    episodes?: number;
    minutesPerEpisode?: number;
    genre?: string;
    aspectRatio?: string;
    adaptMode?: string;
  };
  adaptation?: { core?: string; keep?: unknown; cut?: unknown; merge?: unknown; risks?: unknown };
  characters?: OutlineCharacter[];
  scenes?: OutlineScene[];
  props?: OutlineProp[];
  beats?: OutlineBeat[];
  episodes?: OutlineEpisode[];
  relations?: { a: string; b: string; type: string; note: string }[];
  cards?: { ep: number; at: string; name: string; note: string }[];
}

export interface OutlineCharacter {
  id: string;
  name: string;
  role?: string;
  tier?: string;
  arc?: string;
  from?: string[];
}

export interface OutlineScene {
  id: string;
  name: string;
  primary?: boolean;
  reusePlan?: string;
}

export interface OutlineProp {
  id: string;
  name: string;
  function?: string;
}

export interface OutlineBeat {
  id: string;
  type: string;
  weight?: number | string;
  episode: number;
  setup?: string;
  payoff?: string;
}

export interface OutlineEpisode {
  ep: number;
  synopsis?: string;
  hook?: string;
  suspense?: string;
  sceneIds?: string[];
  characterIds?: string[];
  propIds?: string[];
  crowdPlan?: string;
  warnings?: string[];
}

export interface CastCharacter {
  id: string;
  code?: string;
  name: string;
  importance?: string;
  oneLiner?: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  persona?: any;
  image?: { prompt?: string; promptLocal?: string; negativePrompt?: string; tags?: string[] };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  voice?: any;
}

export interface CastData {
  characters?: CastCharacter[];
  /** character-refs 产出的分层底稿：{C01: asset.json, …} */
  refs?: Record<string, RefLayerDoc>;
  /** character-refs 产出的 md 原文：{文件名: 内容} */
  refsPrompts?: Record<string, string>;
}

export interface RefLayer {
  en?: string;
  zh?: string;
  source?: "stated" | "inferred" | string;
}

export interface RefLayerDoc {
  name?: string;
  layers?: Record<string, RefLayer>;
  outfits?: Record<string, { top?: RefLayer; bottom?: RefLayer; details?: RefLayer }>;
}

export interface ArtScene {
  id: string;
  name: string;
  primary?: boolean;
  summary?: string;
  anchors?: string[];
  lighting?: string[];
  usage?: { episodes?: number[]; beats?: number[] };
  variantOf?: string;
  changes?: string;
  image?: { prompt?: string; negativePrompt?: string };
}

export interface ArtProp {
  id: string;
  name?: string;
  scale?: string;
  states?: string[];
  relatedScenes?: string[];
  carriedBy?: string;
  image?: { prompt?: string; negativePrompt?: string };
}

export interface ArtData {
  scenes?: ArtScene[];
  props?: ArtProp[];
}

export interface ScriptFlowBeat {
  speaker?: string;
  line?: string;
  delivery?: string;
  action?: string;
}

export interface ScriptScene {
  sceneId: string;
  lighting?: string;
  characters?: string[];
  props?: string[];
  flow?: ScriptFlowBeat[];
}

export interface ScriptEpisode {
  ep: number;
  targetSeconds?: number;
  hook?: string;
  cliff?: string;
  beatsClaimed?: string[];
  hookBeat?: string | number[];
  scenes?: ScriptScene[];
}

export interface ScriptData {
  episodes?: ScriptEpisode[];
}

/** 站位：locations 是平面图物品表，scenes 是逐场人物点位 */
export interface StagingItem {
  name: string;
  alt?: string;
  /** [x, y, w, h]，0–1 比例坐标 */
  plan?: number[];
  on?: boolean;
}

export interface StagingLocation {
  id?: string;
  name: string;
  items?: StagingItem[];
}

export interface StagingPlace {
  id: string;
  item?: string;
  side?: string;
  along?: string;
  posture?: string;
  facing?: string;
  present?: boolean;
}

export interface StagingScene {
  ep: number;
  scene?: number;
  sceneId?: string;
  lighting?: string;
  cast?: string[];
  places?: StagingPlace[];
  extras?: number;
  extrasWhere?: string;
  why?: string;
}

export interface StagingData {
  locations?: StagingLocation[];
  scenes?: StagingScene[];
}

export interface StoryboardSegment {
  id: string;
  sceneIndex?: number;
  cuts?: Cut[];
  h3Prompt?: string;
  blocking?: string;
  soundscape?: string;
  music?: string;
  note?: string;
  // 搬运时补的对账字段
  promptSource?: string;
  promptSha8?: string;
  frameCount?: number;
  pictureCount?: number;
  manifestSeconds?: number;
  oversize?: boolean;
}

export interface StoryboardEpisode {
  ep: number;
  segments?: StoryboardSegment[];
}

export interface StoryboardData {
  params?: {
    maxSegmentSeconds?: number;
    minCutSeconds?: number;
    maxCutSeconds?: number;
    maxOnScreen?: number;
    tolerance?: number;
  };
  episodes?: StoryboardEpisode[];
}

/** manifest.json：图位清单（唯一出图依据） */
export interface ManifestSegment {
  segment: string;
  seconds?: number;
  cuts?: number;
  cutStarts?: number[];
  prompt?: string;
  pictures?: string[];
  missing?: string[];
}
