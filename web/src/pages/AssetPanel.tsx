/** 资产管理：统一管理上传素材与生成视频（列表 / 搜索 / 筛选 / 下载 / 删除）。 */
import {
  Download,
  Film,
  Images,
  Image as ImageIcon,
  Music,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, fileUrl } from "../api/client";
import type { Task, UploadListItem } from "../types";

type FilterKey = "all" | "video" | "image";

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
}

const filters: { key: FilterKey; label: string; icon: React.ReactNode }[] = [
  { key: "all", label: "全部", icon: <Images size={14} /> },
  { key: "video", label: "视频", icon: <Film size={14} /> },
  { key: "image", label: "图片", icon: <ImageIcon size={14} /> },
];

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

function isThisMonth(s: string): boolean {
  const d = new Date(s);
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth();
}

export default function AssetPanel({ onNotify }: { onNotify: (msg: string) => void }) {
  const [uploads, setUploads] = useState<UploadListItem[]>([]);
  const [videos, setVideos] = useState<Task[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterKey>("all");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [ups, vids] = await Promise.all([api.listUploads(), api.listVideos()]);
      setUploads(ups);
      setVideos(vids.filter((t) => t.status === "done" && t.video_url));
    } catch {
      /* 忽略加载错误 */
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const cards = useMemo<AssetCard[]>(() => {
    const uploadCards: AssetCard[] = uploads.map((u) => ({
      key: `up-${u.id}`,
      title: u.filename,
      kind: u.kind,
      createdAt: u.created_at,
      meta: [],
      thumbUrl: u.url,
      downloadUrl: u.url,
      deletable: true,
      deleteFn: async () => {
        await api.deleteUpload(u.id);
      },
    }));
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
      const matchType = filter === "all" || (filter === "video" ? c.kind === "video" : c.kind === "image");
      const matchQuery = !q || c.title.toLowerCase().includes(q);
      return matchType && matchQuery;
    });
  }, [cards, query, filter]);

  const uploadVideoCount = uploads.filter((u) => u.kind === "video").length;
  const uploadImageCount = uploads.filter((u) => u.kind === "image").length;
  const stats = [
    { label: "素材总数", value: uploads.length + videos.length, icon: <Images size={16} />, tone: "bg-primary-100 text-primary-700" },
    { label: "视频素材", value: uploadVideoCount + videos.length, icon: <Film size={16} />, tone: "bg-secondary-100 text-secondary-700" },
    { label: "图片素材", value: uploadImageCount, icon: <ImageIcon size={16} />, tone: "bg-accent-100 text-accent-700" },
    {
      label: "本月新增",
      value: uploads.filter((u) => isThisMonth(u.created_at)).length + videos.filter((t) => isThisMonth(t.created_at)).length,
      icon: <Upload size={16} />,
      tone: "bg-secondary-100 text-secondary-700",
    },
  ];

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
    <div className="space-y-5">
      {/* 标题行 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-secondary-100 text-secondary-700">
            <Images size={20} />
          </span>
          <div>
            <h2 className="text-base font-semibold text-foreground-950">资产管理</h2>
            <p className="text-xs text-foreground-500">统一管理生成与上传的视频、图片素材</p>
          </div>
        </div>
      </div>

      {/* 统计卡 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
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
            return (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`flex items-center gap-1.5 rounded-full px-4 py-1.5 text-sm whitespace-nowrap transition ${
                  active ? "bg-primary-500 text-background-50" : "text-foreground-600 hover:text-foreground-900"
                }`}
              >
                {f.icon}
                {f.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* 网格 */}
      {filtered.length === 0 ? (
        <div className="panel flex flex-col items-center justify-center gap-2 py-16 text-foreground-500">
          <Images size={24} />
          <p className="text-sm">{busy ? "加载中…" : "没有找到匹配的素材"}</p>
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
                  <div className="flex h-full w-full flex-col items-center justify-center gap-1 text-foreground-500">
                    <Music size={22} />
                    <span className="max-w-[80%] truncate text-xs">{card.title}</span>
                  </div>
                )}
                <span
                  className={`absolute left-2 top-2 rounded-full px-2 py-0.5 text-[11px] font-medium ${
                    card.kind === "video" ? "bg-secondary-500 text-background-50" : "bg-accent-500 text-background-50"
                  }`}
                >
                  {card.kind === "video" ? "视频" : card.kind === "image" ? "图片" : "音频"}
                </span>
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
                    <span key={m}>{m}</span>
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
