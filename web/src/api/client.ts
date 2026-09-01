/** 轻量 fetch 封装：自动附带 JWT、统一错误处理。 */
import type {
  AdminStats,
  AdminUser,
  CreditLog,
  Pricing,
  RedeemCodeOut,
  Task,
  TokenOut,
  UploadOut,
  User,
} from "../types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const TOKEN_KEY = "h3_token";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

/** <video>/<img> 无法携带请求头，文件地址统一附带 token 查询参数。 */
export function fileUrl(path: string): string {
  if (!path) return "";
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}token=${encodeURIComponent(getToken())}`;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string>),
  };
  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 204) return undefined as T;

  let data: unknown = null;
  try {
    data = await resp.json();
  } catch {
    /* 非 JSON 响应 */
  }
  if (!resp.ok) {
    const detail =
      typeof data === "object" && data !== null && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : `请求失败 (${resp.status})`;
    throw new ApiError(resp.status, detail);
  }
  return data as T;
}

export const api = {
  // 认证
  register: (email: string, username: string, password: string) =>
    request<TokenOut>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, username, password }),
    }),
  login: (email: string, password: string) =>
    request<TokenOut>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: () => request<User>("/api/auth/me"),

  // 视频任务
  pricing: () => request<Pricing>("/api/videos/pricing"),
  createVideo: (body: Record<string, unknown>) =>
    request<Task>("/api/videos", { method: "POST", body: JSON.stringify(body) }),
  listVideos: () => request<Task[]>("/api/videos"),
  retryVideo: (id: number) =>
    request<Task>(`/api/videos/${id}/retry`, { method: "POST" }),
  deleteVideo: (id: number) => request<void>(`/api/videos/${id}`, { method: "DELETE" }),

  // 上传
  uploadImage: (file: File, slot: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("slot", slot);
    return request<UploadOut>("/api/uploads", { method: "POST", body: form });
  },

  // 积分
  creditLogs: () => request<CreditLog[]>("/api/credits/logs"),
  redeem: (code: string) =>
    request<{ credits: number; added: number }>("/api/credits/redeem", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),

  // 管理后台
  adminUsers: () => request<AdminUser[]>("/api/admin/users"),
  adminAdjust: (userId: number, amount: number, note: string) =>
    request<{ credits: number }>(`/api/admin/users/${userId}/adjust`, {
      method: "POST",
      body: JSON.stringify({ amount, note }),
    }),
  adminTasks: () => request<Task[]>("/api/admin/tasks"),
  adminGenCodes: (value: number, count: number) =>
    request<RedeemCodeOut[]>("/api/admin/redeem-codes", {
      method: "POST",
      body: JSON.stringify({ value, count }),
    }),
  adminCodes: () => request<RedeemCodeOut[]>("/api/admin/redeem-codes"),
  adminStats: () => request<AdminStats>("/api/admin/stats"),
};
