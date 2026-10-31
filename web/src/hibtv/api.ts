/** 短剧工作台 API：复用海白菜的 JWT 与 fileUrl 约定（<img>/<video> 带 token 查询参数）。 */
import { getToken, fileUrl } from "../api/client";
import type {
  ArtifactList,
  ChangeItem,
  CodexStatus,
  DocFileKind,
  FrameList,
  HibtvAsset,
  HibtvDoc,
  HibtvFrame,
  HibtvJob,
  HibtvProject,
  HibtvStatus,
  JobKind,
  LibraryItem,
  RenderItem,
  Segment,
  SegmentList,
} from "./types";

export class HibtvError extends Error {
  status: number;
  /** 503 时后端会把自检结果塞在 detail 里（掉线原因要能看见） */
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(options.headers as Record<string, string>) };
  if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 204) return undefined as T;
  let data: unknown = null;
  try {
    data = await resp.json();
  } catch {
    /* 非 JSON */
  }
  if (!resp.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    let message = `请求失败 (${resp.status})`;
    if (typeof detail === "string") message = detail;
    else if (detail && typeof detail === "object") {
      const note = (detail as { note?: string }).note;
      if (note) message = note;
    }
    throw new HibtvError(resp.status, message, detail);
  }
  return data as T;
}

const base = "/api/hibtv";

export const mediaUrl = fileUrl;

/** 内网地址脱敏：http://127.0.0.1:8194 → 内网节点:8194（不把部署细节暴露到页面）。 */
export function maskHost(raw?: string): string {
  const value = (raw || "").trim();
  if (!value) return "";
  // 回环 + 私网地址一律不上屏：内网拓扑不该出现在用户界面里
  return value.replace(
    /^https?:\/\/(127\.0\.0\.1|localhost|0\.0\.0\.0|10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(:\d+)?/i,
    (_m, _host, port) => `内网节点${port || ""}`,
  );
}

export interface CreateProjectForm {
  script?: File | null;
  art?: File | null;
  name?: string;
  genre?: string;
  episodes?: number;
  minutesMin?: number;
  minutesMax?: number;
  ratio?: string;
  keeps?: string;
}

export const hibtvApi = {
  status: () => request<HibtvStatus>(`${base}/status`),
  codexStatus: () => request<CodexStatus>(`${base}/ai/codex/status`),

  listProjects: () => request<HibtvProject[]>(`${base}/projects`),
  getProject: (id: number) => request<HibtvProject>(`${base}/projects/${id}`),
  createProject: (form: CreateProjectForm) => {
    const body = new FormData();
    if (form.script) body.append("script", form.script);
    if (form.art) body.append("art", form.art);
    body.append("name", form.name || "");
    body.append("genre", form.genre || "");
    body.append("episodes", String(form.episodes || 0));
    body.append("minutes_min", String(form.minutesMin ?? 2));
    body.append("minutes_max", String(form.minutesMax ?? 5));
    body.append("ratio", form.ratio || "9:16");
    body.append("keeps", form.keeps || "");
    return request<HibtvProject>(`${base}/projects`, { method: "POST", body });
  },
  patchProject: (id: number, body: Record<string, unknown>) =>
    request<HibtvProject>(`${base}/projects/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  replaceSources: (id: number, script: File, art: File) => {
    const body = new FormData();
    body.append("script", script);
    body.append("art", art);
    return request<HibtvProject>(`${base}/projects/${id}/sources`, { method: "PUT", body });
  },
  addAsset: (id:number, kind:string, name:string, prompt:string) => request<HibtvAsset>(`${base}/projects/${id}/assets?kind=${kind}`,
    {method:"POST",body:JSON.stringify({name,data:{image:{prompt}}})}),
  deleteProject: (id: number) => request<void>(`${base}/projects/${id}`, { method: "DELETE" }),

  getDoc: (id: number, kind: DocFileKind, ep = 0) =>
    request<HibtvDoc>(`${base}/projects/${id}/docs/${kind}?ep=${ep}`),
  patchDoc: (id: number, kind: DocFileKind, body: { path: string; value: unknown; ep?: number }) =>
    request<HibtvDoc>(`${base}/projects/${id}/docs/${kind}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  listJobs: (id: number, limit = 30) =>
    request<HibtvJob[]>(`${base}/projects/${id}/jobs?limit=${limit}`),
  createJob: (id: number, body: { kind: JobKind; ep?: number; payload?: Record<string, unknown> }) =>
    request<HibtvJob>(`${base}/projects/${id}/jobs`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancelJob: (id: number, jobId: number) =>
    request<HibtvJob>(`${base}/projects/${id}/jobs/${jobId}/cancel`, { method: "POST" }),
  /** 文本段：取消跑在网关上的作业（不调 DELETE，产物还能下载） */
  cancelRemoteJob: (id: number, jobId: number) =>
    request<HibtvJob>(`${base}/projects/${id}/jobs/${jobId}/cancel-remote`, { method: "POST" }),

  artifacts: (id: number, jobId = 0) =>
    request<ArtifactList>(`${base}/projects/${id}/artifacts?job_id=${jobId}`),
  artifactsZipUrl: (id: number, jobId = 0) =>
    mediaUrl(`/api/hibtv/projects/${id}/artifacts/download?job_id=${jobId}`),

  listAssets: (id: number, kind = "") =>
    request<HibtvAsset[]>(`${base}/projects/${id}/assets${kind ? `?kind=${kind}` : ""}`),
  patchAsset: (
    id: number,
    assetId: number,
    body: { name?: string; hidden?: boolean; data?: Record<string, unknown> },
  ) =>
    request<HibtvAsset>(`${base}/projects/${id}/assets/${assetId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  /** draw=出锚点 / regen=重出锚点 / confirm=确认锚点 / draw_view=出指定派生视图（需带 view） */
  assetImage: (
    id: number,
    assetId: number,
    body: { action: "draw" | "regen" | "confirm" | "draw_view"; hint?: string; view?: string },
  ) =>
    request<HibtvJob | HibtvAsset>(`${base}/projects/${id}/assets/${assetId}/image`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  uploadAsset: (id: number, assetId: number, file: File, view = "") => {
    const form = new FormData();
    form.append("file", file);
    if (view) form.append("view", view);
    return request<HibtvAsset>(`${base}/projects/${id}/assets/${assetId}/upload`, {
      method: "POST",
      body: form,
    });
  },

  listFrames: (id: number, ep: number, segId = "") =>
    request<FrameList>(
      `${base}/projects/${id}/frames?ep=${ep}${segId ? `&seg_id=${encodeURIComponent(segId)}` : ""}`,
    ),
  frameImage: (id: number, frameId: number, prompt = "") =>
    request<HibtvJob>(`${base}/projects/${id}/frames/${frameId}/image`, {
      method: "POST",
      body: JSON.stringify({ prompt }),
    }),
  uploadFrame: (id: number, frameId: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<HibtvFrame>(`${base}/projects/${id}/frames/${frameId}/upload`, {
      method: "POST",
      body: form,
    });
  },

  listSegments: (id: number, ep: number) =>
    request<SegmentList>(`${base}/projects/${id}/segments?ep=${ep}`),
  getSegment: (id: number, segId: string) =>
    request<Segment>(`${base}/projects/${id}/segments/${segId}`),
  generateSegment: (id: number, segId: string, body: { prompt?: string }) =>
    request<{
      ok: boolean;
      taskId: number;
      v: number;
      cost: number;
      seconds: number;
      frames: number;
      refs: number;
      refSource: string;
      planSeconds: number;
    }>(`${base}/projects/${id}/segments/${segId}/generate`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  chooseVersion: (id: number, segId: string, v: number) =>
    request<Segment>(`${base}/projects/${id}/segments/${segId}/choose`, {
      method: "POST",
      body: JSON.stringify({ v }),
    }),
  /** 镜头重修 = 重跑本集 storyboard（平台不改写提示词） */
  rebuildSegment: (id: number, segId: string) =>
    request<HibtvJob>(`${base}/projects/${id}/segments/${segId}/rebuild`, { method: "POST" }),

  renderEpisode: (id: number, ep: number) =>
    request<HibtvJob>(`${base}/projects/${id}/episodes/${ep}/render`, { method: "POST" }),
  listRenders: (id: number, ep: number) =>
    request<RenderItem[]>(`${base}/projects/${id}/episodes/${ep}/renders`),

  listChanges: (id: number) => request<ChangeItem[]>(`${base}/projects/${id}/changes`),
  undoChange: (id: number, changeId: number) =>
    request<{ ok: boolean }>(`${base}/projects/${id}/changes/${changeId}/undo`, { method: "POST" }),

  library: (id: number) =>
    request<{ items: LibraryItem[]; count: number }>(`${base}/projects/${id}/library`),
  exportProject: (id: number) =>
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    request<{ project: HibtvProject; docs: Record<string, any>; renders: any[] }>(
      `${base}/projects/${id}/export`,
    ),
};
