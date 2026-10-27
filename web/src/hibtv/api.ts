/** 短剧工作台 API：复用海白菜的 JWT 与 fileUrl 约定（<img>/<video> 带 token 查询参数）。 */
import { getToken, fileUrl } from "../api/client";
import type {
  ChangeItem,
  DocKind,
  HibtvAsset,
  HibtvDoc,
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
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
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
    const message = typeof detail === "string" ? detail : `请求失败 (${resp.status})`;
    throw new HibtvError(resp.status, message);
  }
  return data as T;
}

const base = "/api/hibtv";

export const mediaUrl = fileUrl;

/** 内网地址脱敏：http://127.0.0.1:8194 → 内网节点:8194（不把部署细节暴露到页面）。 */
export function maskHost(raw?: string): string {
  const value = (raw || "").trim();
  if (!value) return "";
  return value.replace(
    /^https?:\/\/(127\.0\.0\.1|localhost|0\.0\.0\.0)(:\d+)?/i,
    (_m, _host, port) => `内网节点${port || ""}`,
  );
}

export const hibtvApi = {
  status: () => request<HibtvStatus>(`${base}/status`),

  listProjects: () => request<HibtvProject[]>(`${base}/projects`),
  getProject: (id: number) => request<HibtvProject>(`${base}/projects/${id}`),
  createProject: (form: FormData) =>
    request<HibtvProject>(`${base}/projects`, { method: "POST", body: form }),
  patchProject: (id: number, body: Record<string, unknown>) =>
    request<HibtvProject>(`${base}/projects/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteProject: (id: number) => request<void>(`${base}/projects/${id}`, { method: "DELETE" }),

  getDoc: (id: number, kind: DocKind, ep = 0) =>
    request<HibtvDoc>(`${base}/projects/${id}/docs/${kind}?ep=${ep}`),
  patchDoc: (id: number, kind: DocKind, body: { path: string; value: unknown; ep?: number }) =>
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
  assetImage: (
    id: number,
    assetId: number,
    body: { action: "anchor" | "all" | "views" | "regen" | "confirm"; view?: string; views?: string[]; hint?: string },
  ) =>
    request<HibtvJob | HibtvAsset>(`${base}/projects/${id}/assets/${assetId}/image`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  uploadAsset: (id: number, assetId: number, file: File, view = "") => {
    const form = new FormData();
    form.append("file", file);
    form.append("view", view);
    return request<HibtvAsset>(`${base}/projects/${id}/assets/${assetId}/upload`, {
      method: "POST",
      body: form,
    });
  },

  listSegments: (id: number, ep: number) =>
    request<SegmentList>(`${base}/projects/${id}/segments?ep=${ep}`),
  getSegment: (id: number, segId: string) =>
    request<Segment>(`${base}/projects/${id}/segments/${segId}`),
  generateSegment: (id: number, segId: string, body: { prompt?: string }) =>
    request<{ ok: boolean; taskId: number; v: number; cost: number; seconds: number; refs: number; planSeconds: number }>(
      `${base}/projects/${id}/segments/${segId}/generate`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  chooseVersion: (id: number, segId: string, v: number) =>
    request<Segment>(`${base}/projects/${id}/segments/${segId}/choose`, {
      method: "POST",
      body: JSON.stringify({ v }),
    }),
  rebuildSegment: (id: number, segId: string) =>
    request<Segment>(`${base}/projects/${id}/segments/${segId}/rebuild`, { method: "POST" }),

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
