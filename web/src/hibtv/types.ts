/** 短剧工作台（HiBTV）接口类型：与 server/app/routers/hibtv.py 的响应一一对应。 */

export type DocKind = "outline" | "cast" | "art" | "script" | "storyboard";
export type JobKind = DocKind | "images" | "compose";

export interface Gates {
  status?: "ok" | "fail" | "skipped" | string;
  problems?: string[];
  // 说明行（质量门未运行的原因 / 单集项目豁免）：不是缺陷，不计缺口
  notes?: string[];
  checked_at?: string;
  runner?: string;
}

export interface DocMeta {
  status: string;
  version: number;
  gates: Gates;
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
  scriptName: string;
  artName: string;
  keeps: string;
  detected: Detected;
  status: string;
  createdAt: string;
  updatedAt: string;
  docs: Record<string, DocMeta>;
  assetCount: Record<string, number>;
  segmentCount: { total: number; done: number };
}

export interface Detected {
  episodes?: number;
  scenes?: number;
  lines?: number;
  beats?: number;
  cast?: number;
  scene?: number;
  prop?: number;
  effect?: number;
}

export interface HibtvDoc {
  kind: DocKind;
  ep: number;
  status: string;
  version: number;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: any;
  gates: Gates;
  error: string;
  updated_at: string;
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
  created_at: string;
  finished_at: string;
}

export interface ViewMeta {
  path?: string;
  url?: string;
  upload_id?: number;
  version?: number;
  uploaded?: boolean;
  stale?: boolean;
  failed?: boolean;
  fromSheet?: boolean;
  at?: string;
}

export interface HibtvAsset {
  id: number;
  kind: "cast" | "scene" | "prop" | string;
  code: string;
  name: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: any;
  views: Record<string, ViewMeta>;
  status: "empty" | "drawing" | "review" | "done" | string;
  version: number;
  hidden: boolean;
  sort: number;
  defs: string[][];
  anchor: string;
  // 多视图设定稿：一张 16:9 图装下全部视角，views 里的每张都是从它裁出来的
  sheet?: ViewMeta & { cells?: string[] };
  sheetSpec?: {
    // 出图画幅（Qwen-Image-2.1 官方 16:9 原生 2K = 2752×1536）
    size?: number[];
    // 每种资产的格子比例矩形 [view, x, y, w, h]；裁切以后端实际出图尺寸为准
    kinds?: Record<string, { brief?: string; cells?: (string | number)[][] }>;
  };
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
  refs?: number[];
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
  group: string;
  code: string;
  name: string;
  url: string;
  version: number;
  stale: boolean;
  at: string;
}

export interface HibtvStatus {
  enabled: boolean;
  llm: { enabled: boolean; provider: string; model: string; mode: string };
  image: { available?: boolean; worker?: string; template?: string; degraded?: boolean; note?: string };
  gates: { available: boolean; node: string; bin: string };
  compose: { available?: boolean; ffmpeg?: string; note?: string };
  segMaxSeconds: number;
  anchorConfirm: boolean;
  cost: { image: number; video: Record<string, number> };
  credits: number;
}

/** skill 文档的数据结构（宽松定义，按需取字段）。 */
export interface OutlineData {
  source?: string;
  params?: { episodes?: number; minutesPerEpisode?: number; genre?: string; adaptMode?: string };
  adaptation?: { core?: string; keep?: { what: string; why: string }[] };
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
  weight?: string;
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

export interface CastData {
  characters?: { id: string; code?: string; name: string; importance?: string; oneLiner?: string }[];
}

export interface ArtData {
  scenes?: { id: string; name: string }[];
  props?: { id: string; name: string }[];
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
  hookBeat?: number[];
  scenes?: ScriptScene[];
}

export interface StoryboardEpisode {
  ep: number;
  segments?: {
    id: string;
    sceneIndex?: number;
    cuts?: Cut[];
    h3Prompt?: string;
    blocking?: string;
    soundscape?: string;
    note?: string;
  }[];
}
