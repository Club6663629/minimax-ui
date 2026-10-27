/** 短剧工作台全局状态：当前项目、作业轮询、文档读取、toast 与浮层。 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { hibtvApi, HibtvError } from "./api";
import type { DocKind, HibtvDoc, HibtvJob, HibtvProject, HibtvStatus, JobKind } from "./types";

const PROJECT_KEY = "hibtv_project_id";

export interface ToastAction {
  label: string;
  fn: () => void;
}

interface ToastItem {
  id: number;
  msg: string;
  action?: ToastAction;
}

interface HibtvCtx {
  ready: boolean;
  projects: HibtvProject[];
  project: HibtvProject | null;
  status: HibtvStatus | null;
  jobs: HibtvJob[];
  /** 每次有作业结束 / 文档被本地改写时自增，页面据此重取文档。 */
  revision: number;
  activeJobs: HibtvJob[];
  selectProject: (id: number) => void;
  refreshProject: () => Promise<void>;
  refreshJobs: () => Promise<void>;
  refreshProjects: () => Promise<void>;
  bump: () => void;
  toast: (msg: string, action?: ToastAction) => void;
  layer: ReactNode | null;
  layerClass: string;
  openLayer: (node: ReactNode, cls?: string) => void;
  closeLayer: () => void;
  setRatio: (ratio: string) => Promise<void>;
  runJob: (kind: JobKind, ep?: number, payload?: Record<string, unknown>) => Promise<HibtvJob | null>;
}

const Ctx = createContext<HibtvCtx | null>(null);

export function useHibtv(): HibtvCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useHibtv 必须在 HibtvProvider 内使用");
  return ctx;
}

/** 错误统一转成一句话，交给 toast。 */
export function errText(err: unknown): string {
  if (err instanceof HibtvError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

export function HibtvProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<HibtvProject[]>([]);
  const [projectId, setProjectId] = useState<number>(() => Number(localStorage.getItem(PROJECT_KEY) || 0));
  const [project, setProject] = useState<HibtvProject | null>(null);
  const [status, setStatus] = useState<HibtvStatus | null>(null);
  const [jobs, setJobs] = useState<HibtvJob[]>([]);
  const [revision, setRevision] = useState(0);
  const [ready, setReady] = useState(false);
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [layer, setLayer] = useState<ReactNode | null>(null);
  const [layerClass, setLayerClass] = useState("");
  // 浮层与 toast 必须落在 .hibtv-root 内，否则拿不到暗金作用域样式（也会被根层遮住）
  const [portalEl, setPortalEl] = useState<HTMLElement | null>(null);
  const seq = useRef(1);
  const prevJobs = useRef<Record<number, string>>({});

  useEffect(() => {
    setPortalEl(document.querySelector<HTMLElement>(".hibtv-root"));
  }, []);

  const bump = useCallback(() => setRevision((n) => n + 1), []);

  const toast = useCallback((msg: string, action?: ToastAction) => {
    const id = seq.current++;
    setToasts((list) => [...list, { id, msg, action }]);
    window.setTimeout(
      () => setToasts((list) => list.filter((t) => t.id !== id)),
      action ? 7000 : 3400,
    );
  }, []);

  const openLayer = useCallback((node: ReactNode, cls = "") => {
    setLayer(node);
    setLayerClass(cls);
  }, []);
  const closeLayer = useCallback(() => {
    setLayer(null);
    setLayerClass("");
  }, []);

  const refreshProjects = useCallback(async () => {
    try {
      const rows = await hibtvApi.listProjects();
      setProjects(rows);
      setProjectId((cur) => cur || rows[0]?.id || 0);
    } catch (err) {
      toast(errText(err));
    }
  }, [toast]);

  const refreshProject = useCallback(async () => {
    if (!projectId) return;
    try {
      setProject(await hibtvApi.getProject(projectId));
    } catch {
      /* 轮询失败不打断页面 */
    }
  }, [projectId]);

  const refreshJobs = useCallback(async () => {
    if (!projectId) return;
    try {
      const rows = await hibtvApi.listJobs(projectId);
      setJobs(rows);
      let finished = false;
      for (const job of rows) {
        const before = prevJobs.current[job.id];
        if (before && before !== job.status && ["done", "failed"].includes(job.status)) {
          finished = true;
        }
        prevJobs.current[job.id] = job.status;
      }
      if (finished) {
        setRevision((n) => n + 1);
        void refreshProject();
      }
    } catch {
      /* 轮询失败不打断页面 */
    }
  }, [projectId, refreshProject]);

  useEffect(() => {
    void (async () => {
      try {
        setStatus(await hibtvApi.status());
      } catch {
        /* 未启用时 status 也可能 503，页面按降级处理 */
      }
      await refreshProjects();
      setReady(true);
    })();
  }, [refreshProjects]);

  useEffect(() => {
    if (!projectId) {
      setProject(null);
      return;
    }
    localStorage.setItem(PROJECT_KEY, String(projectId));
    void refreshProject();
    void refreshJobs();
  }, [projectId, refreshProject, refreshJobs]);

  useEffect(() => {
    if (!projectId) return;
    const timer = window.setInterval(() => {
      void refreshJobTick();
    }, 4000);
    const refreshJobTick = async () => {
      await refreshJobs();
    };
    return () => window.clearInterval(timer);
  }, [projectId, refreshJobs]);

  const selectProject = useCallback((id: number) => setProjectId(id), []);

  const setRatio = useCallback(
    async (ratio: string) => {
      if (!project || project.aspectRatio === ratio) return;
      try {
        const next = await hibtvApi.patchProject(project.id, { aspect_ratio: ratio });
        setProject(next);
        setProjects((list) => list.map((p) => (p.id === next.id ? next : p)));
        toast(`画面比例改为 ${ratio}；已出过的图不追改`);
      } catch (err) {
        toast(errText(err));
      }
    },
    [project, toast],
  );

  const runJob = useCallback(
    async (kind: JobKind, ep = 0, payload?: Record<string, unknown>) => {
      if (!project) return null;
      try {
        const job = await hibtvApi.createJob(project.id, { kind, ep, payload });
        toast(`已加入生成队列：${labelOfKind(kind)}${ep ? ` E${String(ep).padStart(2, "0")}` : ""}`);
        void refreshJobs();
        void refreshProject();
        return job;
      } catch (err) {
        toast(errText(err));
        return null;
      }
    },
    [project, refreshJobs, refreshProject, toast],
  );

  const activeJobs = useMemo(
    () => jobs.filter((j) => j.status === "queued" || j.status === "running"),
    [jobs],
  );

  const value: HibtvCtx = {
    ready,
    projects,
    project,
    status,
    jobs,
    revision,
    activeJobs,
    selectProject,
    refreshProject,
    refreshJobs,
    refreshProjects,
    bump,
    toast,
    layer,
    layerClass,
    openLayer,
    closeLayer,
    setRatio,
    runJob,
  };

  return (
    <Ctx.Provider value={value}>
      {children}
      {createPortal(
        <>
          <div className="toasts">
            {toasts.map((t) => (
              <div className="toast" key={t.id}>
                <span>{t.msg}</span>
                {t.action && (
                  <span
                    className="act"
                    onClick={() => {
                      t.action?.fn();
                      setToasts((list) => list.filter((x) => x.id !== t.id));
                    }}
                  >
                    {t.action.label}
                  </span>
                )}
              </div>
            ))}
          </div>
          <div className={`layer ${layer ? "open" : ""} ${layerClass}`.trim()}>
            {layer && <div className="scrim" onClick={closeLayer} />}
            {layer}
          </div>
        </>,
        portalEl || document.body,
      )}
    </Ctx.Provider>
  );
}

export function labelOfKind(kind: string): string {
  const map: Record<string, string> = {
    outline: "大纲",
    cast: "角色卡",
    art: "美术设定",
    script: "剧本",
    storyboard: "分镜",
    images: "出图",
    compose: "整集拼接",
  };
  return map[kind] || kind;
}

/** 读一份文档；作业一结束（revision 变化）自动重取。 */
export function useDoc(kind: DocKind, ep = 0) {
  const { project, revision, toast } = useHibtv();
  const [doc, setDoc] = useState<HibtvDoc | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!project) return;
    setLoading(true);
    try {
      setDoc(await hibtvApi.getDoc(project.id, kind, ep));
    } catch (err) {
      toast(errText(err));
    } finally {
      setLoading(false);
    }
  }, [project, kind, ep, toast]);

  useEffect(() => {
    void load();
  }, [load, revision]);

  return { doc, data: doc?.data ?? null, loading, reload: load, setDoc };
}

/** 读一份文档，并提供「改一个 JSON 路径 + 撤销」的就地编辑能力。 */
export function usePatch(kind: DocKind, ep = 0) {
  const { project, toast, bump } = useHibtv();
  const { doc, data, loading, reload, setDoc } = useDoc(kind, ep);

  const patch = useCallback(
    async (path: string, value: unknown, label?: string) => {
      if (!project) return;
      try {
        const next = await hibtvApi.patchDoc(project.id, kind, { path, value, ep });
        setDoc(next);
        bump();
        const changes = await hibtvApi.listChanges(project.id);
        const last = changes[0];
        toast(`已改 1 处 · ${label || path}`, {
          label: "撤销",
          fn: () => {
            if (!last) return;
            void hibtvApi.undoChange(project.id, last.id).then(() => {
              void reload();
              toast("已撤销 1 处改动");
            });
          },
        });
      } catch (err) {
        toast(errText(err));
      }
    },
    [project, kind, ep, toast, bump, setDoc, reload],
  );

  return { doc, data, loading, reload, patch };
}

/** 作业进度：取本 kind(+ep) 最近一条。 */
export function useJob(kind: string, ep = 0): HibtvJob | null {
  const { jobs } = useHibtv();
  return (
    jobs.find((j) => j.kind === kind && (ep ? j.ep === ep : true)) ?? null
  );
}
