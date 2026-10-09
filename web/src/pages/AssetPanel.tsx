/** 资产管理：统一管理上传素材与生成视频。
 *
 * 能力（2026-09-28 增强）：
 * - 素材类型：图片 / 视频 / 音频（音频可直接试听）；
 * - 上传入口：按钮多选 + 整页拖拽，图片/视频/音频混传；
 * - 重复素材：按内容 md5 识别，卡片角标提示「重复 ×N」；**去重已默认自动执行**（无按钮、无弹窗），
 *   同内容保留最新一条，被历史任务引用的记录不删；
 * - 既有能力：搜索、类型筛选、下载、删除、统计。
 */
import {
  Check,
  Download,
  Film,
  Images,
  Image as ImageIcon,
  Loader2,
  Music,
  Plus,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, fileUrl } from "../api/client";
import type { DedupPreview, Task, UploadListItem } from "../types";

type FilterKey = "all" | "video" | "image" | "audio";

/** 卡片统一模型：上传素材（可删）与生成视频（只读）合并展示。 */
interface AssetCard {
  key: string;
  title: string;
  kind: "image" | "video" | "audio";
  createdAt: string;
  meta: string[];
  thumbUrl: string;
  downloadUrl: string;
  deletable: boolean;
  deleteFn?: () => Promise<void>;
  dupCount?: number;
}

const filters: { key: FilterKey; label: string; icon: React.ReactNode }[] = [
  { key: "all", label: "全部", icon: <Images size={14} /> },
  { key: "video", label: "视频", icon: <Film size={14} /> },
  { key: "image", label: "图片", icon: <ImageIcon size={14} /> },
  { key: "audio", label: "音频", icon: <Music size={14} /> },
];

/** 自动去重累计成果（存本地浏览器，用于资产页展示）。 */
interface DedupStat {
  removed: number;
  bytes: number;
}

const DEDUP_STAT_KEY = "minimax.asset.autoDedupStat";

function readDedupStat(): DedupStat {
  try {
    const raw = localStorage.getItem(DEDUP_STAT_KEY);
    if (!raw) return { removed: 0, bytes: 0 };
    const parsed = JSON.parse(raw) as Partial<DedupStat>;
    return { removed: Number(parsed.removed) || 0, bytes: Number(parsed.bytes) || 0 };
  } catch {
    return { removed: 0, bytes: 0 };
  }
}

function writeDedupStat(stat: DedupStat): void {
  try {
    localStorage.setItem(DEDUP_STAT_KEY, JSON.stringify(stat));
  } catch {
    /* 隐私模式下写入失败可忽略 */
  }
}

const AUDIO_EXT_RE = /\.(mp3|wav|m4a|aac|ogg|flac)$/i;
const VIDEO_EXT_RE = /\.(mp4|mov|webm|mkv)$/i;

function fmtTime(s: string): string {
  return new Date(s).toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function fmtSize(bytes?: number): string {
  if (!bytes || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 上传前按扩展名预判类型（后端按 content-type 二次校验）。 */
function fileKind(file: File): "image" | "video" | "audio" {
  if (AUDIO_EXT_RE.test(file.name)) return "audio";
  if (VIDEO_EXT_RE.test(file.name)) return "video";
  return "image";
}

export default function AssetPanel({ onNotify }: { onNotify: (msg: string) => void }) {
  const [uploads, setUploads] = useState<UploadListItem[]>([]);
  const [videos, setVideos] = useState<Task[]>([]);
  const [dedup, setDedup] = useState<DedupPreview | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterKey>("all");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dedupStat, setDedupStat] = useState<DedupStat>(readDedupStat);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const autoDedupBusy = useRef(false);
  const dedupStatRef = useRef(dedupStat);
  const notifyRef = useRef(onNotify);
  notifyRef.current = onNotify;

  /** 默认自动去重：发现同内容重复素材即静默清理（保留每组最新一条，被历史任务引用的不删）。 */
  const autoDedup = useCallback(async () => {
    if (autoDedupBusy.current) return;
    autoDedupBusy.current = true;
    try {
      const preview = await api.listDuplicates();
      setDedup(preview);
      if (!preview.removable_count) return;
      const staleIds = preview.groups.flatMap((g) => g.remove_ids);
      const res = await api.dedupUploads();
      // 留痕：本次自动删除的素材 id 清单打到控制台，便于回溯
      console.info("[asset] auto dedup", {
        removed_ids: staleIds.slice(0, res.removed),
        removed: res.removed,
        freed_bytes: res.freed_bytes,
        protected_count: res.protected_count,
      });
      const next = {
        removed: dedupStatRef.current.removed + res.removed,
        bytes: dedupStatRef.current.bytes + res.freed_bytes,
      };
      dedupStatRef.current = next;
      setDedupStat(next);
      writeDedupStat(next);
      const extra = res.protected_count ? "，另有 " + res.protected_count + " 个被历史任务引用已保留" : "";
      notifyRef.current("已自动去除 " + res.removed + " 个重复素材，释放 " + (fmtSize(res.freed_bytes) || "0") + extra);
      const [ups, vids, after] = await Promise.all([
        api.listUploads(),
        api.listVideos(),
        api.listDuplicates().catch(() => null),
      ]);
      setUploads(ups);
      setVideos(vids.filter((t) => t.status === "done" && t.video_url));
      if (after) setDedup(after);
    } catch {
      /* 自动去重失败不打扰用户，主列表照常可用 */
    } finally {
      autoDedupBusy.current = false;
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const [ups, vids] = await Promise.all([api.listUploads(), api.listVideos()]);
      setUploads(ups);
      setVideos(vids.filter((t) => t.status === "done" && t.video_url));
    } catch {
      /* 忽略加载错误 */
    }
    await autoDedup();
  }, [autoDedup]);

  useEffect(() => {
    load();
  }, [load]);

  const cards = useMemo<AssetCard[]>(() => {
    const uploadCards: AssetCard[] = uploads.map((u) => {
      const meta = [fmtSize(u.size)].filter(Boolean) as string[];
      if (u.dup_count > 1) meta.push(`同内容 ${u.dup_count} 份`);
      return {
        key: `up-${u.id}`,
        title: u.filename,
        kind: u.kind,
        createdAt: u.created_at,
        meta,
        thumbUrl: u.url,
        downloadUrl: u.url,
        deletable: true,
        deleteFn: async () => {
          await api.deleteUpload(u.id);
        },
        dupCount: u.dup_count,
      };
    });
    const videoCards: AssetCard[] = videos.map((t) => ({
      key: `task-${t.id}`,
      title: `作品 #${t.id}`,
      kind: "video",
      createdAt: t.created_at,
      meta: [`${t.duration}s`, t.aspect_ratio],
      thumbUrl: t.video_url!,
      downloadUrl: t.video_url!,
      deletable: false,
    }));
    return [...uploadCards, ...videoCards].sort(
      (a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
    );
  }, [uploads, videos]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return cards.filter((c) => {
      const matchType = filter === "all" || c.kind === filter;
      const matchQuery = !q || c.title.toLowerCase().includes(q);
      return matchType && matchQuery;
    });
  }, [cards, query, filter]);

  const counts = useMemo(() => {
    const c = { image: 0, video: 0, audio: 0 };
    for (const u of uploads) c[u.kind] += 1;
    c.video += videos.length;
    return c;
  }, [uploads, videos]);

  const stats = [
    { label: "素材总数", value: uploads.length + videos.length, icon: <Images size={16} />, tone: "bg-primary-100 text-primary-700" },
    { label: "视频素材", value: counts.video, icon: <Film size={16} />, tone: "bg-secondary-100 text-secondary-700" },
    { label: "图片素材", value: counts.image, icon: <ImageIcon size={16} />, tone: "bg-accent-100 text-accent-700" },
    { label: "音频素材", value: counts.audio, icon: <Music size={16} />, tone: "bg-primary-100 text-primary-700" },
    {
      label: "已自动去重（累计）",
      value: dedupStat.removed,
      icon: <Trash2 size={16} />,
      tone: "bg-background-200 text-foreground-600",
    },
    {
      label: "累计释放空间",
      value: fmtSize(dedupStat.bytes) || "0",
      icon: <Download size={16} />,
      tone: "bg-background-200 text-foreground-600",
    },
  ];

  /** 上传入口：按钮多选 / 拖拽共用，逐张上传（后端若命中同内容既有素材会自动复用）。 */
  async function uploadFiles(files: File[]) {
    if (!files.length) return;
    setUploading(true);
    let ok = 0;
    let merged = 0;
    const failed: string[] = [];
    try {
      for (const f of files) {
        try {
          const res = await api.uploadMedia(f, "reference");
          ok += 1;
          if (res.duplicate) merged += 1;
        } catch (err) {
          failed.push(`${f.name}：${err instanceof Error ? err.message : "上传失败"}`);
        }
      }
      await load();
      const kindText = ["image", "video", "audio"].map((k) => {
        const n = files.filter((f) => fileKind(f) === k).length;
        return n ? `${k === "image" ? "图片" : k === "video" ? "视频" : "音频"} ${n}` : "";
      }).filter(Boolean).join(" · ");
      const parts = [`已上传 ${ok} 个素材`];
      if (kindText) parts.push(`(${kindText})`);
      if (merged) parts.push(`其中 ${merged} 个为重复内容，已复用不重复入库`);
      if (failed.length) parts.push(`失败 ${failed.length} 个：${failed.slice(0, 3).join("；")}`);
      onNotify(parts.join("，"));
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(card: AssetCard) {
    if (!card.deletable || !card.deleteFn) return;
    if (!confirm(`删除素材「${card.title}」？`)) return;
    setBusy(true);
    try {
      await card.deleteFn();
      await load();
      onNotify("素材已删除");
    } catch (err) {
      alert(err instanceof Error ? err.message : "删除失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="relative space-y-5"
      onDragEnter={(e) => {
        e.preventDefault();
        dragDepth.current += 1;
        setDragging(true);
      }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={(e) => {
        e.preventDefault();
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (dragDepth.current === 0) setDragging(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        dragDepth.current = 0;
        setDragging(false);
        const files = Array.from(e.dataTransfer.files ?? []);
        if (files.length) uploadFiles(files);
      }}
    >
      {/* 拖拽提示遮罩 */}
      {dragging && (
        <div className="pointer-events-none absolute inset-0 z-20 flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-primary-500 bg-background-100/90 text-primary-700">
          <Upload size={26} />
          <p className="text-sm font-medium">松开鼠标即可上传素材（图片 / 视频 / 音频）</p>
        </div>
      )}

      {/* 标题行 + 上传入口（重复素材默认自动清理，不再单独设按钮） */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-secondary-100 text-secondary-700">
            <Images size={20} />
          </span>
          <div>
            <h2 className="text-base font-semibold text-foreground-950">资产管理</h2>
            <p className="text-xs text-foreground-500">统一管理上传与生成的视频、图片、音频素材；重复内容自动合并</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm,video/x-matroska,audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac"
            className="hidden"
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              e.target.value = "";
              if (files.length) uploadFiles(files);
            }}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="btn-primary !rounded-full"
            title="上传图片 / 视频 / 音频素材"
          >
            {uploading ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
            {uploading ? "上传中…" : "上传素材"}
          </button>
        </div>
      </div>

      {/* 统计卡 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {stats.map((stat) => (
          <div key={stat.label} className="flex items-center gap-3 rounded-xl border border-background-200 bg-background-100 p-4">
            <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${stat.tone}`}>
              {stat.icon}
            </span>
            <div className="min-w-0">
              <p className="text-xl font-semibold text-foreground-950">{stat.value}</p>
              <p className="truncate text-xs text-foreground-500">{stat.label}</p>
            </div>
          </div>
        ))}
      </div>

      {(dedup?.protected_count ?? 0) > 0 && (
        <p className="text-xs text-foreground-500">
          另有 {dedup?.protected_count} 个重复素材被历史任务引用，已自动保留（删除会影响历史任务，故不清理）。
        </p>
      )}

      {/* 搜索 + 筛选 */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative w-full sm:max-w-xs">
          <span className="pointer-events-none absolute left-3 top-1/2 flex h-4 w-4 -translate-y-1/2 items-center justify-center text-foreground-500">
            <Search size={15} />
          </span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索素材名称…"
            className="w-full rounded-lg border border-background-300 bg-background-100 py-2 pl-9 pr-4 text-sm text-foreground-900 placeholder-foreground-500 outline-none transition focus:border-primary-500"
          />
        </div>

        <div className="inline-flex self-start rounded-full bg-background-200 p-1 sm:self-auto">
          {filters.map((f) => {
            const active = filter === f.key;
            const label = f.key === "all" ? f.label : `${f.label} ${counts[f.key as "image" | "video" | "audio"]}`;
            return (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`flex items-center gap-1.5 rounded-full px-4 py-1.5 text-sm whitespace-nowrap transition ${
                  active ? "bg-primary-500 text-background-50" : "text-foreground-600 hover:text-foreground-900"
                }`}
              >
                {f.icon}
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {/* 网格 */}
      {filtered.length === 0 ? (
        <div className="panel flex flex-col items-center justify-center gap-2 py-16 text-foreground-500">
          <Images size={24} />
          <p className="text-sm">{busy || uploading ? "加载中…" : "没有找到匹配的素材，点「上传素材」或把文件拖到这里"}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filtered.map((card) => (
            <div
              key={card.key}
              className="group overflow-hidden rounded-xl border border-background-200 bg-background-100 transition hover:border-primary-300"
            >
              <div className="relative aspect-video overflow-hidden bg-background-200">
                {card.kind === "image" ? (
                  <img
                    src={fileUrl(card.thumbUrl)}
                    alt={card.title}
                    className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
                  />
                ) : card.kind === "video" ? (
                  <video
                    src={fileUrl(card.thumbUrl)}
                    muted
                    preload="metadata"
                    className="h-full w-full object-cover"
                  />
                ) : (
                  <div className="flex h-full w-full flex-col items-center justify-center gap-2 bg-background-100 px-3">
                    <Music size={22} className="text-foreground-500" />
                    <audio src={fileUrl(card.thumbUrl)} controls preload="metadata" className="w-full" />
                  </div>
                )}
                <span
                  className={`absolute left-2 top-2 rounded-full px-2 py-0.5 text-[11px] font-medium ${
                    card.kind === "video"
                      ? "bg-secondary-500 text-background-50"
                      : card.kind === "audio"
                        ? "bg-primary-500 text-background-50"
                        : "bg-accent-500 text-background-50"
                  }`}
                >
                  {card.kind === "video" ? "视频" : card.kind === "image" ? "图片" : "音频"}
                </span>
                {(card.dupCount ?? 1) > 1 && (
                  <span
                    className="absolute right-2 top-2 rounded-full bg-rose-500 px-2 py-0.5 text-[11px] font-medium text-background-50"
                    title={`该内容在素材库中有 ${card.dupCount} 份重复`}
                  >
                    重复 ×{card.dupCount}
                  </span>
                )}
                <div className="absolute inset-0 flex items-center justify-center gap-2 bg-foreground-950/60 opacity-0 transition-opacity duration-200 group-hover:opacity-100">
                  <a
                    href={fileUrl(card.downloadUrl)}
                    download={card.title}
                    title="下载"
                    className="flex h-9 w-9 items-center justify-center rounded-full bg-background-50 text-foreground-900 transition hover:bg-primary-500 hover:text-background-50"
                  >
                    <Download size={15} />
                  </a>
                  {card.deletable && (
                    <button
                      onClick={() => handleDelete(card)}
                      title="删除"
                      disabled={busy}
                      className="flex h-9 w-9 items-center justify-center rounded-full bg-background-50 text-foreground-900 transition hover:bg-rose-500 hover:text-background-50 disabled:opacity-50"
                    >
                      <Trash2 size={15} />
                    </button>
                  )}
                </div>
              </div>
              <div className="p-3">
                <p className="truncate text-sm font-medium text-foreground-900" title={card.title}>
                  {card.title}
                </p>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-foreground-500">
                  <span>{fmtTime(card.createdAt)}</span>
                  {card.meta.map((m) => (
                    <span key={m} className="inline-flex items-center gap-1">
                      {m.startsWith("同内容") || m.startsWith("重复") ? <Check size={12} /> : null}
                      {m}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
