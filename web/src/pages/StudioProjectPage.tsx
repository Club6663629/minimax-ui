/** 剧集工坊·项目页：左侧 80 集地图，右侧系列门与资产（角色四视图 / 场景 / 道具）。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  Check,
  CheckCircle2,
  Image as ImageIcon,
  RefreshCw,
  Sparkles,
  XCircle,
} from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, fileUrl } from "../api/client";
import {
  ASSET_KIND_LABEL,
  ASSET_STATUS_LABEL,
  ASSET_VIEWS,
  GATE_LABEL,
  VIEW_SPECS,
  errText,
  latestViews,
} from "../components/studio/labels";
import type {
  StudioAsset,
  StudioAssetView,
  StudioEpisode,
  StudioGates,
  StudioJob,
  StudioMapRow,
  StudioProject,
} from "../types";

type Tab = "gates" | "character" | "scene" | "prop";

const TABS: { key: Tab; label: string }[] = [
  { key: "gates", label: "系列门" },
  { key: "character", label: "角色" },
  { key: "scene", label: "场景" },
  { key: "prop", label: "道具" },
];

const ACTIVE_TASK = ["queued", "generating_images"];

function GatesBlock({ kind, gates }: { kind: string; gates: StudioGates }) {
  const passed = gates.gates.filter((g) => g.ok).length;
  return (
    <div className="panel p-4">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold text-foreground-900">
          {GATE_LABEL[kind] || kind}
        </span>
        <span
          className={`chip ${gates.passed ? "text-accent-700" : "text-red-600"}`}
        >
          {passed}/{gates.gates.length} 通过
        </span>
        {gates.passed ? (
          <CheckCircle2 size={14} className="ml-auto text-accent-600" />
        ) : (
          <XCircle size={14} className="ml-auto text-red-500" />
        )}
      </div>
      <div className="mt-2 space-y-1">
        {gates.gates
          .filter((g) => !g.ok)
          .map((g) => (
            <div key={g.id} className="text-xs text-red-700">
              {g.label}
              {g.detail ? `：${g.detail}` : ""}
            </div>
          ))}
        {gates.problems.map((p) => (
          <div key={p} className="text-xs text-amber-700">
            {p}
          </div>
        ))}
        {gates.passed && gates.problems.length === 0 && (
          <div className="text-xs text-foreground-500">全部通过</div>
        )}
      </div>
    </div>
  );
}

/** 一格视图：有图就预览，没图给出「出图」。锚点未确认时派生视图会被后端拦下。 */
function ViewCell({
  view,
  current,
  busy,
  onGenerate,
  onConfirm,
}: {
  view: string;
  current?: StudioAssetView;
  busy: boolean;
  onGenerate: () => void;
  onConfirm: () => void;
}) {
  const spec = VIEW_SPECS[view] || { ratio: "2:3", label: view };
  return (
    <div className="overflow-hidden rounded-lg border border-background-300 bg-background-50">
      <div
        className="relative w-full bg-background-200"
        style={{ aspectRatio: spec.ratio.replace(":", " / ") }}
      >
        {current ? (
          <img
            src={fileUrl(current.url)}
            alt={spec.label}
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-foreground-400">
            <ImageIcon size={18} />
            <span className="text-[11px]">未出图</span>
          </div>
        )}
        {current?.confirmed && (
          <span className="absolute right-1 top-1 rounded-full bg-accent-600 p-0.5 text-white">
            <Check size={11} />
          </span>
        )}
      </div>
      <div className="flex items-center gap-1 px-1.5 py-1">
        <span className="truncate text-[11px] text-foreground-600">
          {spec.label}
          {current ? ` v${current.version}` : ""}
        </span>
        {current && !current.confirmed && (
          <button
            type="button"
            className="ml-auto rounded px-1.5 py-0.5 text-[11px] text-accent-700 hover:bg-accent-100"
            onClick={onConfirm}
            disabled={busy}
          >
            确认
          </button>
        )}
        <button
          type="button"
          className={`${current && !current.confirmed ? "" : "ml-auto"} rounded px-1.5 py-0.5 text-[11px] text-primary-700 hover:bg-primary-100 disabled:opacity-50`}
          onClick={onGenerate}
          disabled={busy}
          title={current ? "重出一版（旧版保留）" : "出这一张"}
        >
          {busy ? "…" : current ? "重出" : "出图"}
        </button>
      </div>
    </div>
  );
}

function AssetCard({
  asset,
  busyKey,
  onGenerate,
  onConfirm,
}: {
  asset: StudioAsset;
  busyKey: string;
  onGenerate: (view: string) => void;
  onConfirm: (view: string) => void;
}) {
  const views = ASSET_VIEWS[asset.kind] || ["sheet"];
  const current = latestViews(asset.views);
  const locale = asset.data.image?.promptLocal || asset.data.summary || "";
  return (
    <div className="panel p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="font-mono text-[11px] text-foreground-500">{asset.ref_id}</span>
        <span className="truncate text-sm font-medium text-foreground-900">{asset.name}</span>
        <span className="chip ml-auto">{ASSET_STATUS_LABEL[asset.status] || asset.status}</span>
      </div>
      <div
        className={`grid gap-2 ${views.length > 1 ? "grid-cols-4" : "grid-cols-2"}`}
      >
        {views.map((view) => (
          <ViewCell
            key={view}
            view={view}
            current={current[view]}
            busy={busyKey === `${asset.id}:${view}`}
            onGenerate={() => onGenerate(view)}
            onConfirm={() => onConfirm(view)}
          />
        ))}
      </div>
      {locale && (
        <p className="mt-2 line-clamp-2 text-[11px] leading-4 text-foreground-500">{locale}</p>
      )}
    </div>
  );
}

export default function StudioProjectPage() {
  const { pid } = useParams();
  const projectId = Number(pid);
  const navigate = useNavigate();

  const [project, setProject] = useState<StudioProject | null>(null);
  const [episodes, setEpisodes] = useState<StudioEpisode[]>([]);
  const [counts, setCounts] = useState({ episodes: 0, assets: 0 });
  const [rows, setRows] = useState<StudioMapRow[]>([]);
  const [tab, setTab] = useState<Tab>("gates");
  const [seriesGates, setSeriesGates] = useState<Record<string, StudioGates>>({});
  const [assets, setAssets] = useState<StudioAsset[]>([]);
  const [jobs, setJobs] = useState<StudioJob[]>([]);
  const [busyKey, setBusyKey] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadBase = useCallback(async () => {
    const [detail, map] = await Promise.all([
      api.studioProject(projectId),
      api.studioMap(projectId),
    ]);
    setProject(detail.project);
    setEpisodes(detail.episodes);
    setCounts(detail.counts);
    setRows(map.items);
  }, [projectId]);

  const loadSeriesGates = useCallback(async () => {
    const kinds = ["outline", "cast", "art"] as const;
    const results = await Promise.all(
      kinds.map((kind) => api.studioGates(projectId, kind).catch(() => null)),
    );
    const next: Record<string, StudioGates> = {};
    kinds.forEach((kind, i) => {
      const result = results[i];
      if (result) next[kind] = result;
    });
    setSeriesGates(next);
  }, [projectId]);

  const loadAssets = useCallback(
    async (kind: string) => {
      const result = await api.studioAssets(projectId, kind);
      setAssets(result.items);
    },
    [projectId],
  );

  const loadJobs = useCallback(async () => {
    const result = await api.studioJobs(projectId, "image").catch(() => null);
    if (result) setJobs(result.items);
  }, [projectId]);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      setError("");
      try {
        await loadBase();
        if (alive) await loadSeriesGates();
        if (alive) await loadJobs();
      } catch (exc) {
        if (alive) setError(errText(exc));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [loadBase, loadSeriesGates, loadJobs]);

  useEffect(() => {
    if (tab === "gates") return;
    loadAssets(tab).catch((exc) => setError(errText(exc)));
  }, [tab, loadAssets]);

  const activeJobs = useMemo(
    () => jobs.filter((j) => ACTIVE_TASK.includes(j.task_status || "") || ["queued", "running"].includes(j.status)),
    [jobs],
  );

  // 有在跑的任务就轮询：任务结束立刻刷资产，出图结果不用手动点刷新
  useEffect(() => {
    if (activeJobs.length === 0) return;
    const timer = setInterval(async () => {
      await loadJobs();
      if (tab !== "gates") {
        await loadAssets(tab).catch(() => undefined);
      }
    }, 4000);
    return () => clearInterval(timer);
  }, [activeJobs.length, loadJobs, loadAssets, tab]);

  async function handleGenerate(asset: StudioAsset, view: string) {
    setBusyKey(`${asset.id}:${view}`);
    setError("");
    try {
      await api.studioAssetImage(projectId, asset.id, { view });
      await loadJobs();
    } catch (exc) {
      setError(errText(exc));
    } finally {
      setBusyKey("");
    }
  }

  async function handleConfirm(asset: StudioAsset, view: string) {
    setBusyKey(`${asset.id}:${view}`);
    setError("");
    try {
      await api.studioConfirmView(projectId, asset.id, view);
      await loadAssets(asset.kind);
      if (tab === "gates") await loadSeriesGates();
    } catch (exc) {
      setError(errText(exc));
    } finally {
      setBusyKey("");
    }
  }

  const totalScenes = rows.reduce((sum, row) => sum + (row.scenes || 0), 0);
  const totalSeconds = rows.reduce((sum, row) => sum + (row.seconds || 0), 0);
  const totalCuts = rows.reduce((sum, row) => sum + (row.cuts || 0), 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link
          to="/studio"
          className="inline-flex items-center gap-1 text-xs text-foreground-600 hover:text-foreground-900"
        >
          <ArrowLeft size={14} /> 项目
        </Link>
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold text-foreground-950">
            {project?.title || "…"}
          </h1>
          <p className="mt-0.5 text-xs text-foreground-500">
            {counts.episodes} 集 · {totalScenes} 场 · {Math.round(totalSeconds)} 秒
            {totalCuts ? ` · 已出 ${totalCuts} 切` : ""} · {counts.assets} 个资产
          </p>
        </div>
        <button
          type="button"
          className="btn-ghost ml-auto !px-3 !py-1.5 text-xs"
          onClick={async () => {
            setLoading(true);
            await loadBase().catch((exc) => setError(errText(exc)));
            if (tab === "gates") await loadSeriesGates().catch(() => undefined);
            else await loadAssets(tab).catch(() => undefined);
            setLoading(false);
          }}
        >
          <RefreshCw size={13} /> 刷新
        </button>
        <span className="chip">{activeJobs.length > 0 ? `${activeJobs.length} 张出图中` : "空闲"}</span>
      </div>

      {error && (
        <div className="panel border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
        {/* 80 集地图 */}
        <div className="panel max-h-[70vh] overflow-y-auto p-2">
          <div className="px-2 py-1 text-xs font-medium text-foreground-600">分集地图</div>
          <div className="space-y-0.5">
            {episodes.map((ep) => {
              const row = rows.find((r) => r.ep_no === ep.ep_no);
              return (
                <button
                  key={ep.id}
                  type="button"
                  onClick={() => navigate(`/studio/${projectId}/ep/${ep.ep_no}`)}
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left transition hover:bg-background-200"
                >
                  <span className="w-10 shrink-0 font-mono text-[11px] text-foreground-500">
                    EP{String(ep.ep_no).padStart(2, "0")}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-xs text-foreground-800">
                    {ep.name || "—"}
                  </span>
                  <span className="shrink-0 text-[11px] text-foreground-400">
                    {row?.scenes ?? 0}场{row?.cuts ? ` · ${row.cuts}切` : ""}
                  </span>
                </button>
              );
            })}
            {episodes.length === 0 && (
              <div className="px-2 py-4 text-xs text-foreground-500">
                {loading ? "加载中…" : "还没有分集，先回到项目页导入。"}
              </div>
            )}
          </div>
        </div>

        {/* 右区 */}
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-1.5">
            {TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => setTab(t.key)}
                className={`rounded-lg px-3 py-1.5 text-sm transition ${
                  tab === t.key
                    ? "bg-primary-100 text-primary-700"
                    : "text-foreground-600 hover:bg-background-200"
                }`}
              >
                {t.label}
                {t.key !== "gates" && tab === t.key && ` ${assets.length}`}
              </button>
            ))}
            {tab !== "gates" && (
              <span className="ml-auto flex items-center gap-1 text-[11px] text-foreground-500">
                <Sparkles size={12} /> 派生视图只参考锚点，重出不覆盖旧版
              </span>
            )}
          </div>

          {tab === "gates" ? (
            <div className="grid gap-3 md:grid-cols-3">
              {(["outline", "cast", "art"] as const).map((kind) =>
                seriesGates[kind] ? (
                  <GatesBlock key={kind} kind={kind} gates={seriesGates[kind]} />
                ) : (
                  <div key={kind} className="panel p-4 text-xs text-foreground-500">
                    {GATE_LABEL[kind]} 未跑
                  </div>
                ),
              )}
            </div>
          ) : assets.length === 0 ? (
            <div className="panel p-6 text-sm text-foreground-500">
              {loading ? "加载中…" : `还没有${ASSET_KIND_LABEL[tab]}资产。`}
            </div>
          ) : (
            <div
              className={
                tab === "character"
                  ? "grid gap-3 md:grid-cols-2"
                  : "grid gap-3 md:grid-cols-3"
              }
            >
              {assets.map((asset) => (
                <AssetCard
                  key={asset.id}
                  asset={asset}
                  busyKey={busyKey}
                  onGenerate={(view) => handleGenerate(asset, view)}
                  onConfirm={(view) => handleConfirm(asset, view)}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
