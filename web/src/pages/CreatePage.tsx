/** 创作页：模式菜单（单段 / 分镜 / 资产）+ 单段创作输入卡（参考图 + 提示词 + 模式/配置）+ 任务流。
 *
 * 单段模式输入方式对齐官方产品形态：是否上传参考图与「全能参考 / 首尾帧」选项共同决定
 * 生成模式（未上传任何输入图时为文生视频），不再用独立 tab 区分模式。
 * 分镜模式 = 长视频导演台（DirectorPanel）；资产管理 = 素材库（AssetPanel）。 */
import {
  Box,
  Check,
  ChevronDown,
  Clapperboard,
  Coins,
  Film,
  Images,
  Loader2,
  Maximize2,
  Music,
  Package,
  Plus,
  SlidersHorizontal,
  Sparkles,
  UploadCloud,
  Wand2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, fileUrl } from "../api/client";
import AdvideoPanel from "./AdvideoPanel";
import AssetPanel from "./AssetPanel";
import DirectorPanel from "./DirectorPanel";
import TaskCard from "../components/TaskCard";
import { useAuth } from "../state/auth";
import {
  ACTIVE_STATUSES,
  RES_LABEL,
  type Pricing,
  type Task,
  type UploadListItem,
  type UploadOut,
} from "../types";

const ASPECTS = ["16:9", "9:16", "1:1"] as const;
const DURATIONS = [5, 8, 10] as const;
const SCENES = [
  { value: "general", label: "通用" },
  { value: "drama", label: "AI 短剧" },
  { value: "ecommerce", label: "电商" },
  { value: "music", label: "音乐创作" },
] as const;
type Scene = (typeof SCENES)[number]["value"];
const RESOLUTIONS = ["768p", "2k", "4k"] as const;
type Resolution = (typeof RESOLUTIONS)[number];
const MAX_REFS = 9; // 全能参考官方上限 9 个（图片/视频/音频混存）
const MAX_VIDEO_REFS = 3; // 官方上限 3 个参考视频
const MAX_AUDIO_REFS = 3; // 官方上限 3 个独立参考音频

type RefMode = "r2v" | "flf2v";
type RefKind = "image" | "video" | "audio";
type CreateMode = "single" | "advideo" | "director" | "assets";

const IMG_EXT_RE = /\.(jpe?g|png|webp)$/i;
const VID_EXT_RE = /\.(mp4|mov|webm|mkv)$/i;
const AUD_EXT_RE = /\.(mp3|wav|m4a|aac|ogg|flac)$/i;

/** 按文件名扩展名判定参考媒体类型（后端按扩展名分流到节点槽位）。 */
function refKindOf(filename: string): RefKind {
  if (VID_EXT_RE.test(filename)) return "video";
  if (AUD_EXT_RE.test(filename)) return "audio";
  return "image";
}

function fileKindOf(file: File): RefKind {
  return refKindOf(file.name);
}

/** 点击外部自动关闭的弹层 */
function usePopover() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);
  return { open, setOpen, ref };
}

const chipCls =
  "inline-flex items-center gap-1.5 rounded-full border border-background-300 bg-background-50 px-3 py-1.5 text-xs text-foreground-700 transition hover:border-primary-400";

/** 模式菜单：单段模式 / 电商广告片 / 分镜模式 / 资产管理。 */
function ModeTabs({
  mode,
  onChange,
  showAdvideo = false,
}: {
  mode: CreateMode;
  onChange: (m: CreateMode) => void;
  showAdvideo?: boolean;
}) {
  const tabs: { key: CreateMode; label: string; icon: React.ReactNode }[] = [
    { key: "single", label: "单段模式", icon: <Film size={14} /> },
    // 广告片=开发中：仅白名单账号（后端 advideo_admin_only）渲染该入口
    ...(showAdvideo
      ? [{ key: "advideo" as CreateMode, label: "广告片", icon: <Package size={14} /> }]
      : []),
    { key: "director", label: "分镜模式", icon: <Clapperboard size={14} /> },
    { key: "assets", label: "资产管理", icon: <Images size={14} /> },
  ];
  return (
    <div className="inline-flex rounded-full bg-background-200 p-1">
      {tabs.map((tab) => {
        const active = mode === tab.key;
        return (
          <button
            key={tab.key}
            onClick={() => onChange(tab.key)}
            className={`flex items-center gap-2 rounded-full px-5 py-2 text-sm font-medium whitespace-nowrap transition-colors duration-200 ${
              active ? "bg-primary-500 text-background-50" : "text-foreground-600 hover:text-foreground-900"
            }`}
          >
            {tab.icon}
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

/** 全局提示（轻量 toast）。 */
function Toast({ message, visible }: { message: string; visible: boolean }) {
  if (!visible) return null;
  return (
    <div className="fixed bottom-6 left-1/2 z-40 -translate-x-1/2 rounded-full bg-foreground-950/90 px-5 py-2.5 text-sm text-background-50 shadow-lg">
      {message}
    </div>
  );
}

/** 全能参考：已上传参考缩略图（角标即提示词中的 <Picture/Video/Audio N> 按类型序号） */
function RefThumb({ kind, seq, value, onRemove }: { kind: RefKind; seq: number; value: UploadOut; onRemove: () => void }) {
  const badge = kind === "video" ? `视频 ${seq}` : kind === "audio" ? `音频 ${seq}` : `图 ${seq}`;
  return (
    <div className="group relative h-24 w-[76px] shrink-0 overflow-hidden rounded-xl border border-background-300">
      {kind === "image" && (
        <img src={fileUrl(value.url)} alt={`参考 ${seq}`} className="h-full w-full object-cover" />
      )}
      {kind === "video" && (
        <video src={fileUrl(value.url)} muted preload="metadata" className="h-full w-full object-cover" />
      )}
      {kind === "audio" && (
        <div className="flex h-full w-full flex-col items-center justify-center gap-1 bg-background-100 px-1">
          <Music size={16} className="text-foreground-500" />
          <span className="max-w-[64px] truncate text-[10px] text-foreground-600">{value.filename}</span>
        </div>
      )}
      <span className="absolute left-1 top-1 rounded bg-foreground-950/60 px-1 text-[10px] text-background-50">{badge}</span>
      <button
        type="button"
        onClick={onRemove}
        className="absolute right-1 top-1 hidden rounded-md bg-foreground-950/60 p-0.5 text-background-50 group-hover:block"
        title="移除"
      >
        <X size={12} />
      </button>
    </div>
  );
}

/** 资产列表项 → 参考/帧槽位需要的 UploadOut 形态（复用同一记录 id，不重复上传文件）。 */
function toUploadOut(it: UploadListItem): UploadOut {
  return { id: it.id, slot: it.slot, filename: it.filename, url: it.url, md5: it.md5, size: it.size };
}

/** 素材体积友好显示。 */
function fmtSize(bytes?: number): string {
  if (!bytes || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 最近使用过的素材 id（P0-③ 排序用：最近使用优先）。仅本地记录，不上报。 */
const RECENT_ASSETS_KEY = "minimax.ref.recent";

function useRecentAssets() {
  const [recent, setRecent] = useState<number[]>(() => {
    try {
      const raw = JSON.parse(localStorage.getItem(RECENT_ASSETS_KEY) ?? "[]");
      return Array.isArray(raw) ? raw.filter((x) => typeof x === "number") : [];
    } catch {
      return [];
    }
  });
  const push = useCallback((ids: number[]) => {
    if (!ids.length) return;
    setRecent((prev) => {
      const next = [...ids, ...prev.filter((x) => !ids.includes(x))].slice(0, 60);
      try {
        localStorage.setItem(RECENT_ASSETS_KEY, JSON.stringify(next));
      } catch {
        /* 存储不可用时忽略 */
      }
      return next;
    });
  }, []);
  return [recent, push] as const;
}

/** 素材选择/上传的结果：added>0 表示成功加入并可关闭；否则 hint 为页内内联提示（不再用 alert）。 */
type AddResult = { added: number; hint?: string };

/** 全能参考：统一入口瓦片（P0-①：本地文件选择器与「从资产选」合并为一个入口）。 */
function AddAssetTile({
  count,
  counts,
  busy,
  onClick,
}: {
  count: number;
  counts: { image: number; video: number; audio: number };
  busy: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      title={`添加参考素材（素材库 / 本地上传）\n上限：图片 ${counts.image}/9 · 视频 ${counts.video}/${MAX_VIDEO_REFS} · 音频 ${counts.audio}/${MAX_AUDIO_REFS}`}
      className="flex h-24 w-[76px] shrink-0 flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-background-300 bg-background-50 text-foreground-500 transition hover:border-primary-400 hover:text-foreground-800 disabled:opacity-60"
    >
      {busy ? <Loader2 size={18} className="animate-spin" /> : <Plus size={18} />}
      <span className="text-xs">添加素材</span>
      <span className="text-[10px] text-foreground-500">
        ({count}/{MAX_REFS})
      </span>
    </button>
  );
}

/** 已选素材缩略条（P1-⑤）：横向列出当前已选参考，每项可单独移除。 */
function SelectedStrip({
  items,
  title,
  onRemove,
}: {
  items: UploadOut[];
  title: string;
  onRemove: (index: number) => void;
}) {
  if (!items.length) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5 rounded-xl border border-background-200 bg-background-50 px-2 py-1.5">
      <span className="mr-1 text-[11px] text-foreground-500">{title}</span>
      {items.map((it, i) => {
        const kind = refKindOf(it.filename);
        return (
          <span
            key={`${it.id}-${i}`}
            className="inline-flex items-center gap-1 rounded-lg border border-background-300 bg-background-100 py-0.5 pl-0.5 pr-1"
            title={`${it.filename}${kind === "image" ? " · 图片" : kind === "video" ? " · 视频" : " · 音频"}`}
          >
            {kind === "image" ? (
              <img src={fileUrl(it.url)} alt={it.filename} className="h-7 w-7 rounded object-cover" />
            ) : kind === "video" ? (
              <video src={fileUrl(it.url)} muted preload="metadata" className="h-7 w-7 rounded object-cover" />
            ) : (
              <span className="flex h-7 w-7 items-center justify-center rounded bg-background-200">
                <Music size={12} className="text-foreground-500" />
              </span>
            )}
            <span className="max-w-[86px] truncate text-[11px] text-foreground-700">{it.filename}</span>
            <button
              type="button"
              onClick={() => onRemove(i)}
              title="移除"
              className="rounded p-0.5 text-foreground-500 transition hover:bg-background-200 hover:text-rose-600"
            >
              <X size={11} />
            </button>
          </span>
        );
      })}
    </div>
  );
}

/** 素材预览浮层（P1-④）：图片大图 / 视频内嵌播放 / 音频试听。 */
function AssetPreview({ item, onClose }: { item: UploadListItem; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-foreground-950/70 p-4" onClick={onClose}>
      <div
        className="w-full max-w-3xl overflow-hidden rounded-2xl border border-background-200 bg-background-100 p-3 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-2 flex items-center gap-2">
          <span className="truncate text-sm text-foreground-900" title={item.filename}>
            {item.filename}
          </span>
          <span className="ml-auto whitespace-nowrap text-[11px] text-foreground-500">
            {item.kind === "image" ? "图片" : item.kind === "video" ? "视频" : "音频"}
            {fmtSize(item.size) ? ` · ${fmtSize(item.size)}` : ""}
          </span>
          <button type="button" onClick={onClose} className="rounded-md p-1 text-foreground-500 transition hover:bg-background-200">
            <X size={16} />
          </button>
        </div>
        {item.kind === "image" ? (
          <img src={fileUrl(item.url)} alt={item.filename} className="max-h-[70vh] w-full object-contain" />
        ) : item.kind === "video" ? (
          <video src={fileUrl(item.url)} controls autoPlay className="max-h-[70vh] w-full" />
        ) : (
          <audio src={fileUrl(item.url)} controls autoPlay className="w-full" />
        )}
      </div>
    </div>
  );
}

/** 统一素材抽屉（P0-①②③ + P1④⑤）：左侧两个来源（我的资产 / 本地上传），
 *  单击选中、双击直接加入并关闭；按槽位剩余额度置灰类型 tab、默认选最缺类型、最近使用优先排序。 */
function AssetDrawer({
  open,
  kindFilter,
  counts,
  room,
  existingIds,
  recentIds,
  onClose,
  onAddAssets,
  onAddFiles,
}: {
  open: null | "ref" | "first" | "last";
  kindFilter?: RefKind;
  counts: { image: number; video: number; audio: number };
  room: { total: number; video: number; audio: number };
  existingIds: number[];
  recentIds: number[];
  onClose: () => void;
  onAddAssets: (items: UploadListItem[]) => AddResult;
  onAddFiles: (files: File[]) => Promise<AddResult>;
}) {
  const [source, setSource] = useState<"assets" | "upload">("assets");
  const [items, setItems] = useState<UploadListItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<"all" | RefKind>("all");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<number[]>([]);
  const [hint, setHint] = useState("");
  const [preview, setPreview] = useState<UploadListItem | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const maxPick = kindFilter ? 1 : Math.max(1, room.total);

  /** 某类型剩余额度（参考总数 / 参考视频 / 参考音频）。 */
  const kindRoom = useCallback(
    (k: RefKind) => (k === "video" ? Math.min(room.total, room.video) : k === "audio" ? Math.min(room.total, room.audio) : room.total),
    [room.total, room.video, room.audio],
  );

  /** 默认 tab = 最缺类型（有剩余额度且已选最少）。 */
  const defaultTab = useCallback((): "all" | RefKind => {
    if (kindFilter) return kindFilter;
    const cands = (["image", "video", "audio"] as RefKind[]).filter((k) => kindRoom(k) > 0);
    if (!cands.length) return "all";
    return cands.reduce((best, k) => (counts[k] < counts[best] ? k : best), cands[0]);
  }, [kindFilter, kindRoom, counts]);

  // 用 ref 拿最新默认 tab，避免额度变化时重置已选
  const defaultTabRef = useRef(defaultTab);
  defaultTabRef.current = defaultTab;

  useEffect(() => {
    if (!open) return;
    setPicked([]);
    setQuery("");
    setHint("");
    setPreview(null);
    setSource("assets");
    setTab(kindFilter ?? defaultTabRef.current());
    setLoading(true);
    api
      .listUploads()
      .then(setItems)
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, kindFilter]);

  // Esc 关闭上层浮层
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (preview) setPreview(null);
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, preview, onClose]);

  if (!open) return null;

  const recentIdx = new Map<number, number>();
  recentIds.forEach((id, i) => {
    if (!recentIdx.has(id)) recentIdx.set(id, i);
  });
  const visible = items
    .filter((it) => (kindFilter ? it.kind === kindFilter : tab === "all" || it.kind === tab))
    .filter((it) => !query.trim() || it.filename.toLowerCase().includes(query.trim().toLowerCase()))
    .sort((a, b) => {
      const ra = recentIdx.has(a.id) ? (recentIdx.get(a.id) as number) : Number.MAX_SAFE_INTEGER;
      const rb = recentIdx.has(b.id) ? (recentIdx.get(b.id) as number) : Number.MAX_SAFE_INTEGER;
      if (ra !== rb) return ra - rb;
      return (b.created_at ?? "").localeCompare(a.created_at ?? "");
    });

  /** 校验一批素材能否加入（超额 → 返回内联提示，不再 alert）。 */
  function checkChosen(list: UploadListItem[]): string {
    let total = room.total;
    let video = room.video;
    let audio = room.audio;
    for (const it of list) {
      if (existingIds.includes(it.id)) return `「${it.filename}」已在参考中`;
      if (total <= 0) return kindFilter ? "该槽位已占用，请先移除后再选" : `参考总数已达上限 ${MAX_REFS} 个`;
      if (it.kind === "video") {
        if (video <= 0) return `参考视频最多 ${MAX_VIDEO_REFS} 个`;
        video -= 1;
      }
      if (it.kind === "audio") {
        if (audio <= 0) return `参考音频最多 ${MAX_AUDIO_REFS} 个`;
        audio -= 1;
      }
      total -= 1;
    }
    return "";
  }

  function toggle(it: UploadListItem) {
    if (picked.includes(it.id)) {
      setHint("");
      setPicked((prev) => prev.filter((x) => x !== it.id));
      return;
    }
    if (picked.length >= maxPick) {
      setHint(`这里最多选择 ${maxPick} 个`);
      return;
    }
    const bad = checkChosen([it]);
    if (bad) {
      setHint(bad);
      return;
    }
    setHint("");
    setPicked((prev) => [...prev, it.id]);
  }

  /** 双击 = 直接加入并关闭（P0-②）。 */
  function addNow(it: UploadListItem) {
    const ids = picked.includes(it.id) ? picked : [...picked, it.id];
    const list = ids.map((id) => items.find((x) => x.id === id)).filter(Boolean) as UploadListItem[];
    const bad = checkChosen(list);
    if (bad) {
      setHint(bad);
      return;
    }
    const res = onAddAssets(list);
    if (res.added > 0) onClose();
    else setHint(res.hint ?? "加入失败");
  }

  function commitPicked() {
    const list = picked.map((id) => items.find((x) => x.id === id)).filter(Boolean) as UploadListItem[];
    if (!list.length) {
      setHint("请先选择素材");
      return;
    }
    const bad = checkChosen(list);
    if (bad) {
      setHint(bad);
      return;
    }
    const res = onAddAssets(list);
    if (res.added > 0) onClose();
    else setHint(res.hint ?? "加入失败");
  }

  async function doUpload(files: File[]) {
    let list = files;
    if (kindFilter) list = files.filter((f) => refKindOf(f.name) === kindFilter);
    if (!list.length) {
      setHint(kindFilter ? "该槽位只支持图片文件（jpg / png / webp）" : "没有可用的素材文件");
      return;
    }
    if (!kindFilter) {
      const accepted: File[] = [];
      let total = room.total - picked.length;
      let video = room.video;
      let audio = room.audio;
      for (const f of list) {
        if (total <= 0) {
          setHint(`参考总数已达上限 ${MAX_REFS} 个`);
          break;
        }
        const k = fileKindOf(f);
        if (k === "video" && video <= 0) {
          setHint(`参考视频最多 ${MAX_VIDEO_REFS} 个`);
          continue;
        }
        if (k === "audio" && audio <= 0) {
          setHint(`参考音频最多 ${MAX_AUDIO_REFS} 个`);
          continue;
        }
        accepted.push(f);
        if (k === "video") video -= 1;
        if (k === "audio") audio -= 1;
        total -= 1;
      }
      list = accepted;
      if (!list.length) return;
    }
    setBusy(true);
    setHint("");
    try {
      const res = await onAddFiles(list);
      if (res.added > 0) onClose();
      else setHint(res.hint ?? "上传失败");
    } finally {
      setBusy(false);
    }
  }

  const tabs: { key: "all" | RefKind; label: string }[] = [
    { key: "all", label: "全部" },
    { key: "image", label: "图片" },
    { key: "video", label: "视频" },
    { key: "audio", label: "音频" },
  ];
  const pickTitle = kindFilter === "image" ? "选择图片" : "添加素材";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground-950/50 p-4">
      <div className="flex max-h-[86vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-background-200 bg-background-100 shadow-xl">
        <div className="flex items-center justify-between border-b border-background-200 px-4 py-3">
          <div>
            <h3 className="text-sm font-semibold text-foreground-950">{pickTitle}</h3>
            <p className="text-xs text-foreground-500">
              {kindFilter
                ? "单击选中、双击直接使用；也可切换到「本地上传」"
                : `单击选中、双击直接加入；参考还能加 ${room.total} 个（视频余 ${room.video} · 音频余 ${room.audio}）`}
            </p>
          </div>
          <button type="button" onClick={onClose} className="rounded-md p-1 text-foreground-500 transition hover:bg-background-200">
            <X size={16} />
          </button>
        </div>

        {/* 来源切换（P0-①） */}
        <div className="flex items-center gap-2 border-b border-background-200 px-4 py-2">
          <div className="inline-flex rounded-full bg-background-200 p-0.5">
            <button
              type="button"
              onClick={() => setSource("assets")}
              className={`rounded-full px-3 py-1 text-xs transition ${
                source === "assets" ? "bg-primary-500 text-background-50" : "text-foreground-600 hover:text-foreground-900"
              }`}
            >
              我的资产
            </button>
            <button
              type="button"
              onClick={() => setSource("upload")}
              className={`rounded-full px-3 py-1 text-xs transition ${
                source === "upload" ? "bg-primary-500 text-background-50" : "text-foreground-600 hover:text-foreground-900"
              }`}
            >
              本地上传
            </button>
          </div>
          {source === "assets" && (
            <>
              {!kindFilter && (
                <div className="inline-flex rounded-full bg-background-200 p-0.5">
                  {tabs.map((t) => {
                    const disabled = t.key === "all" ? room.total <= 0 : kindRoom(t.key) <= 0;
                    return (
                      <button
                        key={t.key}
                        type="button"
                        disabled={disabled}
                        onClick={() => {
                          setTab(t.key);
                          setHint("");
                        }}
                        title={disabled ? (t.key === "all" ? "参考总数已达上限" : `该类型已无剩余额度`) : `只看${t.label}`}
                        className={`rounded-full px-3 py-1 text-xs transition ${
                          tab === t.key ? "bg-primary-500 text-background-50" : "text-foreground-600 hover:text-foreground-900"
                        } disabled:cursor-not-allowed disabled:opacity-40`}
                      >
                        {t.label}
                      </button>
                    );
                  })}
                </div>
              )}
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索素材名称…"
                className="ml-auto w-full max-w-[220px] rounded-lg border border-background-300 bg-background-50 px-3 py-1.5 text-xs text-foreground-900 placeholder-foreground-500 outline-none transition focus:border-primary-500"
              />
            </>
          )}
          {source === "upload" && (
            <span className="text-xs text-foreground-500">
              {kindFilter ? "支持 jpg / png / webp，≤50MB" : "支持图片 / 视频 / 音频，单文件 ≤50MB"}
            </span>
          )}
        </div>

        <div className="min-h-[220px] flex-1 overflow-y-auto p-4">
          {source === "upload" ? (
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                const files = Array.from(e.dataTransfer?.files ?? []);
                if (files.length) void doUpload(files);
              }}
              className={`flex h-52 flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed text-sm transition ${
                dragging ? "border-primary-500 bg-primary-500/5 text-foreground-900" : "border-background-300 text-foreground-500"
              }`}
            >
              <input
                ref={inputRef}
                type="file"
                multiple={!kindFilter}
                accept={kindFilter === "image" ? "image/jpeg,image/png,image/webp" : "image/*,video/mp4,video/quicktime,video/webm,video/x-matroska,audio/*"}
                className="hidden"
                onChange={(e) => {
                  const files = Array.from(e.target.files ?? []);
                  e.target.value = "";
                  if (files.length) void doUpload(files);
                }}
              />
              {busy ? <Loader2 size={20} className="animate-spin" /> : <UploadCloud size={20} />}
              <span>{busy ? "上传中…" : "把文件拖到这里，或"}</span>
              {!busy && (
                <button type="button" onClick={() => inputRef.current?.click()} className="btn-primary !rounded-full">
                  选择本地文件
                </button>
              )}
              <span className="text-[11px] text-foreground-500">上传后自动加入当前槽位，同内容素材会自动复用不会重复入库</span>
            </div>
          ) : loading ? (
            <div className="flex h-40 items-center justify-center gap-2 text-sm text-foreground-500">
              <Loader2 size={16} className="animate-spin" /> 加载素材库…
            </div>
          ) : visible.length === 0 ? (
            <div className="flex h-40 flex-col items-center justify-center gap-2 text-sm text-foreground-500">
              <Images size={20} />
              素材库里还没有这类素材，可切到「本地上传」直接上传
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
              {visible.map((it) => {
                const isPicked = picked.includes(it.id);
                const isUsed = existingIds.includes(it.id);
                return (
                  <div
                    key={it.id}
                    role="button"
                    tabIndex={isUsed ? -1 : 0}
                    onClick={() => !isUsed && toggle(it)}
                    onDoubleClick={() => !isUsed && addNow(it)}
                    onKeyDown={(e) => {
                      if (isUsed) return;
                      if (e.key === "Enter") addNow(it);
                      if (e.key === " ") {
                        e.preventDefault();
                        toggle(it);
                      }
                    }}
                    title={isUsed ? "已在当前槽位中" : `${it.filename}（单击选中 / 双击直接加入）`}
                    className={`group relative aspect-video cursor-pointer overflow-hidden rounded-lg border-2 bg-background-200 transition ${
                      isPicked ? "border-primary-500" : "border-background-200 hover:border-primary-300"
                    } ${isUsed ? "cursor-not-allowed opacity-40" : ""}`}
                  >
                    {it.kind === "image" ? (
                      <img src={fileUrl(it.url)} alt={it.filename} className="h-full w-full object-cover" />
                    ) : it.kind === "video" ? (
                      <video src={fileUrl(it.url)} muted preload="metadata" className="h-full w-full object-cover" />
                    ) : (
                      <div className="flex h-full w-full flex-col items-center justify-center gap-1 px-1">
                        <Music size={18} className="text-foreground-500" />
                        <span className="max-w-full truncate text-[10px] text-foreground-600">{it.filename}</span>
                      </div>
                    )}
                    <span className="absolute left-1 top-1 rounded bg-foreground-950/60 px-1 text-[10px] text-background-50">
                      {it.kind === "image" ? "图片" : it.kind === "video" ? "视频" : "音频"}
                    </span>
                    {/* P1-④ 预览入口 */}
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setPreview(it);
                      }}
                      title="预览"
                      className="absolute bottom-1 right-1 hidden rounded-md bg-foreground-950/60 p-1 text-background-50 group-hover:block"
                    >
                      <Maximize2 size={12} />
                    </button>
                    <span className="absolute bottom-0 left-0 right-0 truncate bg-gradient-to-t from-foreground-950/70 to-transparent px-1 pb-0.5 pt-3 text-[10px] text-background-50">
                      {it.filename}
                    </span>
                    {isPicked && (
                      <span className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded-full bg-primary-500 text-background-50">
                        <Check size={12} />
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {hint && (
          <div className="border-t border-background-200 px-4 py-2 text-xs text-amber-700">{hint}</div>
        )}

        <div className="flex items-center justify-between border-t border-background-200 px-4 py-3">
          <span className="text-xs text-foreground-500">
            已选 {picked.length} / {maxPick}
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-full border border-background-300 bg-background-50 px-4 py-1.5 text-sm text-foreground-700 transition hover:border-primary-400"
            >
              取消
            </button>
            <button type="button" disabled={!picked.length} onClick={commitPicked} className="btn-primary !rounded-full disabled:opacity-50">
              添加{picked.length ? `（${picked.length}）` : ""}
            </button>
          </div>
        </div>
      </div>

      {preview && <AssetPreview item={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

/** 首尾帧：单个帧槽位（P0-①：空槽点击进入统一素材抽屉，本地文件也走抽屉的「本地上传」） */
function FrameSlot({
  label,
  value,
  onOpenPicker,
  onChange,
}: {
  label: string;
  value: UploadOut | null;
  onOpenPicker: () => void;
  onChange: (u: UploadOut | null) => void;
}) {
  return (
    <div className="relative h-24 w-[76px] shrink-0">
      {value ? (
        <div className="group h-full w-full overflow-hidden rounded-xl border border-background-300">
          <img src={fileUrl(value.url)} alt={label} className="h-full w-full object-cover" />
          <button
            type="button"
            onClick={() => onChange(null)}
            className="absolute right-1 top-1 rounded-md bg-foreground-950/60 p-0.5 text-background-50 opacity-0 group-hover:opacity-100"
            title="移除"
          >
            <X size={12} />
          </button>
          <span className="absolute bottom-1 left-1 rounded bg-foreground-950/60 px-1 text-[10px] text-background-50">{label}</span>
        </div>
      ) : (
        <button
          type="button"
          onClick={onOpenPicker}
          title={`点击从素材库或本地选择${label}`}
          className="flex h-full w-full flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-background-300 bg-background-50 text-foreground-500 transition hover:border-primary-400 hover:text-foreground-800"
        >
          <Plus size={18} />
          <span className="text-xs">{label}</span>
          <span className="text-[10px] text-foreground-500">选择素材</span>
        </button>
      )}
    </div>
  );
}
/** 生成模式下拉：全能参考 / 首尾帧 */
function ModeMenu({ value, onChange }: { value: RefMode; onChange: (m: RefMode) => void }) {
  const { open, setOpen, ref } = usePopover();
  const items = [
    { key: "r2v" as const, label: "全能参考", icon: Images },
    { key: "flf2v" as const, label: "首/尾帧", icon: Film },
  ];
  const cur = items.find((i) => i.key === value)!;
  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={() => setOpen(!open)} className={chipCls}>
        <cur.icon size={14} /> {cur.label} <ChevronDown size={13} className="text-foreground-500" />
      </button>
      {open && (
        <div className="absolute left-0 top-full z-20 mt-2 w-44 rounded-xl border border-background-200 bg-background-100 p-1 shadow-xl">
          {items.map((it) => (
            <button
              key={it.key}
              type="button"
              onClick={() => {
                onChange(it.key);
                setOpen(false);
              }}
              className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm transition ${
                value === it.key ? "bg-primary-100 text-primary-700" : "text-foreground-600 hover:bg-background-200 hover:text-foreground-900"
              }`}
            >
              <it.icon size={15} /> {it.label}
              {value === it.key && <Check size={14} className="ml-auto text-primary-600" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** 分辨率 / 时长 / 画幅 组合设置弹层 */
function SettingsMenu({
  resolution,
  onResolution,
  duration,
  onDuration,
  aspect,
  onAspect,
  upscaleEnabled,
}: {
  resolution: Resolution;
  onResolution: (r: Resolution) => void;
  duration: (typeof DURATIONS)[number];
  onDuration: (d: (typeof DURATIONS)[number]) => void;
  aspect: (typeof ASPECTS)[number];
  onAspect: (a: (typeof ASPECTS)[number]) => void;
  upscaleEnabled: boolean;
}) {
  const { open, setOpen, ref } = usePopover();
  const optCls = (active: boolean, disabled = false) =>
    `rounded-md px-2.5 py-1 text-xs transition ${
      active ? "bg-primary-100 text-primary-700" : "text-foreground-600 hover:bg-background-200 hover:text-foreground-900"
    } ${disabled ? "cursor-not-allowed opacity-40" : ""}`;
  // 1K/2K 走本地超分池；超分池关闭时不可提交（云端仅保留提示词增强）
  const disabledOf = (r: Resolution) => r !== "768p" && !upscaleEnabled;
  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={() => setOpen(!open)} className={chipCls}>
        <SlidersHorizontal size={13} />
        {RES_LABEL[resolution]} · {duration}s · {aspect}
      </button>
      {open && (
        <div className="absolute left-0 top-full z-20 mt-2 w-56 space-y-2.5 rounded-xl border border-background-200 bg-background-100 p-3 shadow-xl">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-foreground-500">分辨率</span>
            <div className="flex gap-1">
              {RESOLUTIONS.map((r) => (
                <button
                  key={r}
                  type="button"
                  disabled={disabledOf(r)}
                  title={disabledOf(r) ? "本地超分池未启用" : ""}
                  onClick={() => onResolution(r)}
                  className={optCls(resolution === r, disabledOf(r))}
                >
                  {RES_LABEL[r]}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-foreground-500">时长</span>
            <div className="flex gap-1">
              {DURATIONS.map((d) => (
                <button key={d} type="button" onClick={() => onDuration(d)} className={optCls(duration === d)}>
                  {d}s
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-foreground-500">画幅</span>
            <div className="flex gap-1">
              {ASPECTS.map((a) => (
                <button key={a} type="button" onClick={() => onAspect(a)} className={optCls(aspect === a)}>
                  {a}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function CreatePage() {
  const { user, refreshUser } = useAuth();
  const [mode, setMode] = useState<CreateMode>("single");
  // 广告片（开发中）可见性：以后端 advideo_admin_only 闸门为唯一真源
  // （非白名单账号探针 403 → 不渲染入口，且即使误入也只见占位文案）
  const [advideoAllowed, setAdvideoAllowed] = useState(false);
  useEffect(() => {
    let alive = true;
    api
      .advideoStatus()
      .then(() => {
        if (alive) setAdvideoAllowed(true);
      })
      .catch(() => {
        if (alive) setAdvideoAllowed(false);
      });
    return () => {
      alive = false;
    };
  }, [user?.id]);
  const [pricing, setPricing] = useState<Pricing | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [toast, setToast] = useState({ message: "", visible: false });
  const toastTimerRef = useRef<number | null>(null);

  // 表单状态：参考图上传方式 + 全能参考/首尾帧 选项共同决定生成模式
  const [refMode, setRefMode] = useState<RefMode>("r2v");
  const [refImages, setRefImages] = useState<UploadOut[]>([]);
  const [firstImage, setFirstImage] = useState<UploadOut | null>(null);
  const [lastImage, setLastImage] = useState<UploadOut | null>(null);
  const [uploading, setUploading] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [enhance, setEnhance] = useState(true);
  const [scene, setScene] = useState<Scene>("general");
  const [aspect, setAspect] = useState<(typeof ASPECTS)[number]>("16:9");
  const [duration, setDuration] = useState<(typeof DURATIONS)[number]>(5);
  const [resolution, setResolution] = useState<Resolution>("768p");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // 资产选择弹窗：null=关闭；ref=参考区；first/last=首尾帧槽位
  const [assetPicker, setAssetPicker] = useState<null | "ref" | "first" | "last">(null);
  // 最近使用过的素材（P0-③ 排序：最近使用优先）
  const [recentIds, pushRecent] = useRecentAssets();

  const showToast = useCallback((message: string) => {
    setToast({ message, visible: true });
    if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
    toastTimerRef.current = window.setTimeout(
      () => setToast((t) => ({ ...t, visible: false })),
      2600,
    );
  }, []);

  useEffect(() => {
    return () => {
      if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
    };
  }, []);

  const loadTasks = useCallback(async () => {
    try {
      setTasks(await api.listVideos());
    } catch {
      /* 忽略轮询偶发错误 */
    }
  }, []);

  useEffect(() => {
    api.pricing().then(setPricing).catch(() => {});
    loadTasks();
  }, [loadTasks]);

  // 有进行中任务时轮询刷新
  const hasActive = tasks.some((t) => ACTIVE_STATUSES.includes(t.status));
  useEffect(() => {
    if (!hasActive) return;
    const timer = setInterval(loadTasks, 4000);
    return () => clearInterval(timer);
  }, [hasActive, loadTasks]);

  const cost = useMemo(() => {
    if (!pricing) return 0;
    const _BASE: Record<number, number> = {
      5: pricing.cost_768p_5s,
      8: pricing.cost_768p_8s,
      10: pricing.cost_768p_10s,
      15: pricing.cost_768p_15s,
    };
    let c = _BASE[duration] ?? pricing.cost_768p_10s;
    if ((resolution as string) === "1k") c += pricing.cost_1k_extra;
    if (resolution === "2k") c += pricing.cost_2k_extra;
    if (resolution === "4k") c += pricing.cost_4k_extra;
    return c;
  }, [pricing, duration, resolution]);

  const cloudEnabled = pricing?.cloud_enabled ?? false;
  const upscaleEnabled = pricing?.upscale_enabled ?? true;

  // 是否上传参考图 + 模式选项 → 实际生成模式
  const effMode: Task["mode"] =
    refMode === "flf2v" ? (firstImage || lastImage ? "flf2v" : "t2v") : refImages.length > 0 ? "r2v" : "t2v";

  const refCounts = useMemo(() => {
    const c = { image: 0, video: 0, audio: 0 };
    for (const u of refImages) c[refKindOf(u.filename)] += 1;
    return c;
  }, [refImages]);

  /** 参考剩余额度（P0-③ 智能过滤/置灰、默认 tab 选最缺类型用）。 */
  const refRoom = useMemo(
    () => ({
      total: Math.max(0, MAX_REFS - refImages.length),
      video: Math.max(0, MAX_VIDEO_REFS - refCounts.video),
      audio: Math.max(0, MAX_AUDIO_REFS - refCounts.audio),
    }),
    [refImages.length, refCounts],
  );

  /** 本地上传参考（P0-①：统一抽屉的「本地上传」来源）：按类型上限校验，超额走页内提示。 */
  async function addRefFiles(files: File[]): Promise<AddResult> {
    const counts = { ...refCounts };
    let room = MAX_REFS - refImages.length;
    const accepted: File[] = [];
    let hint: string | undefined;
    for (const f of files) {
      if (room <= 0) {
        hint = `参考总数已达上限 ${MAX_REFS} 个`;
        break;
      }
      const kind = fileKindOf(f);
      if (kind === "video" && counts.video >= MAX_VIDEO_REFS) {
        hint = `参考视频最多 ${MAX_VIDEO_REFS} 个`;
        continue;
      }
      if (kind === "audio" && counts.audio >= MAX_AUDIO_REFS) {
        hint = `参考音频最多 ${MAX_AUDIO_REFS} 个`;
        continue;
      }
      accepted.push(f);
      counts[kind] += 1;
      room -= 1;
    }
    if (!accepted.length) return { added: 0, hint };
    let added = 0;
    setUploading(true);
    try {
      for (const f of accepted) {
        const up = await api.uploadMedia(f, "reference");
        setRefImages((prev) => [...prev, up]);
        pushRecent([up.id]);
        added += 1;
      }
    } catch (err) {
      return { added, hint: err instanceof Error ? err.message : "上传失败" };
    } finally {
      setUploading(false);
    }
    return { added, hint };
  }

  /** 从素材库选参考：复用既有记录 id（不重复上传文件），并按类型上限校验。 */
  function addAssetRefs(items: UploadListItem[]): AddResult {
    const counts = { ...refCounts };
    let room = MAX_REFS - refImages.length;
    const accepted: UploadOut[] = [];
    let hint: string | undefined;
    for (const it of items) {
      if (refImages.some((r) => r.id === it.id) || accepted.some((a) => a.id === it.id)) continue; // 已在参考中
      if (room <= 0) {
        hint = `参考总数已达上限 ${MAX_REFS} 个`;
        break;
      }
      if (it.kind === "video" && counts.video >= MAX_VIDEO_REFS) {
        hint = `参考视频最多 ${MAX_VIDEO_REFS} 个`;
        continue;
      }
      if (it.kind === "audio" && counts.audio >= MAX_AUDIO_REFS) {
        hint = `参考音频最多 ${MAX_AUDIO_REFS} 个`;
        continue;
      }
      accepted.push(toUploadOut(it));
      counts[it.kind] += 1;
      room -= 1;
    }
    if (!accepted.length) return { added: 0, hint };
    setRefImages((prev) => [...prev, ...accepted]);
    pushRecent(accepted.map((a) => a.id));
    showToast(`已从资产添加 ${accepted.length} 个参考`);
    return { added: accepted.length, hint };
  }

  /** 首尾帧：从素材库取图（复用既有记录 id，不重复上传文件）。 */
  function addFrameAsset(items: UploadListItem[], which: "first" | "last"): AddResult {
    const it = items[0];
    if (!it) return { added: 0, hint: "请选择一张图片" };
    if (it.kind !== "image") return { added: 0, hint: "首尾帧只支持图片素材" };
    const other = which === "first" ? lastImage : firstImage;
    if (other && other.id === it.id) return { added: 0, hint: "该图片已用于另一个帧槽位" };
    if (which === "first") setFirstImage(toUploadOut(it));
    else setLastImage(toUploadOut(it));
    pushRecent([it.id]);
    showToast(which === "first" ? "已设置首帧" : "已设置尾帧");
    return { added: 1 };
  }

  /** 首尾帧：本地上传单张图。 */
  async function addFrameFiles(files: File[], which: "first" | "last"): Promise<AddResult> {
    const f = files.find((x) => IMG_EXT_RE.test(x.name));
    if (!f) return { added: 0, hint: "首尾帧只支持图片（jpg / png / webp）" };
    const other = which === "first" ? lastImage : firstImage;
    setUploading(true);
    try {
      const up = await api.uploadImage(f, which);
      if (other && other.id === up.id) return { added: 0, hint: "该图片已用于另一个帧槽位" };
      if (which === "first") setFirstImage(up);
      else setLastImage(up);
      pushRecent([up.id]);
      showToast(which === "first" ? "已设置首帧" : "已设置尾帧");
      return { added: 1 };
    } catch (err) {
      return { added: 0, hint: err instanceof Error ? err.message : "上传失败" };
    } finally {
      setUploading(false);
    }
  }

  async function submit() {
    setError("");
    if (!prompt.trim()) {
      setError("请输入提示词");
      return;
    }
    if (effMode === "r2v" && refImages.length > MAX_REFS) {
      setError(`参考图最多 ${MAX_REFS} 张`);
      return;
    }
    setSubmitting(true);
    try {
      await api.createVideo({
        mode: effMode,
        prompt: prompt.trim(),
        enhance,
        scene,
        aspect_ratio: aspect,
        duration,
        resolution,
        first_image_id: effMode === "flf2v" ? (firstImage?.id ?? null) : null,
        last_image_id: effMode === "flf2v" ? (lastImage?.id ?? null) : null,
        ref_image_ids: effMode === "r2v" ? refImages.map((r) => r.id) : [],
      });
      await Promise.all([loadTasks(), refreshUser()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* ---- 模式菜单 ---- */}
      <div className="flex justify-center">
        <ModeTabs mode={mode} onChange={setMode} showAdvideo={advideoAllowed} />
      </div>

      {mode === "director" && (
        <DirectorPanel
          pricing={pricing}
          onSubmitted={() => {
            Promise.all([loadTasks(), refreshUser()]);
            showToast("导演台任务已提交");
          }}
        />
      )}

      {mode === "advideo" && !advideoAllowed && (
        <div className="panel p-10 text-center text-sm text-foreground-500">
          广告片功能开发中，敬请期待。
        </div>
      )}

      {mode === "advideo" && advideoAllowed && (
        <AdvideoPanel
          pricing={pricing}
          tasks={tasks}
          onChanged={() => {
            Promise.all([loadTasks(), refreshUser()]);
          }}
          showToast={showToast}
          onRefreshUser={refreshUser}
        />
      )}

      {mode === "assets" && <AssetPanel onNotify={showToast} />}

      {mode === "single" && (
        <>
          {/* ---- 创作输入卡 ---- */}
          <div className="panel p-4 sm:p-5">
            <div className="flex flex-col gap-4 sm:flex-row">
              {/* 左侧：参考图 / 首尾帧 上传区 */}
              <div className="flex shrink-0 gap-2">
                {refMode === "r2v" ? (
                  <>
                    {(() => {
                      const seqBy: Record<RefKind, number> = { image: 0, video: 0, audio: 0 };
                      return refImages.map((img, i) => {
                        const kind = refKindOf(img.filename);
                        const seq = ++seqBy[kind];
                        return (
                          <RefThumb
                            key={img.id}
                            kind={kind}
                            seq={seq}
                            value={img}
                            onRemove={() => setRefImages((prev) => prev.filter((_, j) => j !== i))}
                          />
                        );
                      });
                    })()}
                    {refImages.length < MAX_REFS && (
                      <AddAssetTile count={refImages.length} counts={refCounts} busy={uploading} onClick={() => setAssetPicker("ref")} />
                    )}
                  </>
                ) : (
                  <>
                    <FrameSlot label="首帧" value={firstImage} onOpenPicker={() => setAssetPicker("first")} onChange={setFirstImage} />
                    <FrameSlot label="尾帧" value={lastImage} onOpenPicker={() => setAssetPicker("last")} onChange={setLastImage} />
                  </>
                )}
              </div>

              {/* 右侧：提示词 */}
              <div className="flex-1">
                <textarea
                  className="min-h-[110px] w-full resize-none bg-transparent text-sm leading-relaxed text-foreground-900 placeholder-foreground-500 outline-none sm:min-h-[120px]"
                  placeholder="描述您想要生成的视频内容，例如，“一个孩子在公园里放风筝，金色阳光，镜头上移。”"
                  maxLength={2000}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                />
                {refMode === "r2v" && refImages.length > 0 && (
                  <p className="mt-1 text-[11px] leading-relaxed text-foreground-500">
                    提示词可引用：<code>&lt;Picture N&gt;</code> 第 N 张参考图、<code>&lt;Video N&gt;</code> 第 N 个参考视频、
                    <code>&lt;Audio N&gt;</code> 第 N 个参考音频（各自按类型顺序编号）
                  </p>
                )}
              </div>
            </div>

            {/* 已选参考缩略条（P1-⑤）：每项可单独移除 */}
            {refMode === "r2v" && (
              <SelectedStrip
                items={refImages}
                title={`已选 ${refImages.length}/${MAX_REFS}`}
                onRemove={(i) => setRefImages((prev) => prev.filter((_, j) => j !== i))}
              />
            )}

            {/* 底部工具栏 */}
            <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-background-200 pt-4">
              <span className={chipCls}>
                <Box size={14} /> MiniMax H3
              </span>
              <ModeMenu value={refMode} onChange={setRefMode} />
              <SettingsMenu
                resolution={resolution}
                onResolution={setResolution}
                duration={duration}
                onDuration={setDuration}
                aspect={aspect}
                onAspect={setAspect}
                upscaleEnabled={upscaleEnabled}
              />
              <select
                value={scene}
                onChange={(e) => setScene(e.target.value as Scene)}
                title="场景预设"
                className="rounded-full border border-background-300 bg-background-50 px-3 py-1.5 text-xs text-foreground-800 outline-none transition hover:border-primary-400 focus:border-primary-500"
              >
                {SCENES.map((sc) => (
                  <option key={sc.value} value={sc.value} className="bg-background-100 text-foreground-900">
                    {sc.label}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => setEnhance(!enhance)}
                disabled={!cloudEnabled}
                title={cloudEnabled ? "智能提示词增强" : "智能提示词增强（当前不可用）"}
                className={`rounded-full p-2 transition ${
                  enhance && cloudEnabled ? "bg-accent-500/15 text-accent-700" : "text-foreground-500 hover:bg-background-200"
                } disabled:cursor-not-allowed disabled:opacity-40`}
              >
                <Sparkles size={15} />
              </button>

              <div className="ml-auto flex items-center gap-3">
                <span className="flex items-center gap-1.5 text-sm text-foreground-800" title={`余额 ${user?.credits ?? 0} 积分`}>
                  <Coins size={15} className="text-amber-500" /> {cost}
                </span>
                {(user?.credits ?? 0) < cost && (
                  <Link to="/recharge" className="text-xs text-primary-600 hover:underline">
                    去充值
                  </Link>
                )}
                <button onClick={submit} disabled={submitting} className="btn-primary !rounded-full">
                  {submitting ? (
                    <>
                      <Loader2 size={15} className="animate-spin" /> 提交中…
                    </>
                  ) : (
                    <>
                      <Sparkles size={15} /> 创作
                    </>
                  )}
                </button>
              </div>
            </div>

            {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
          </div>

          {/* ---- 我的作品 ---- */}
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold text-foreground-950">我的作品</h2>
              <span className="text-xs text-foreground-500">共 {tasks.length} 条</span>
            </div>

            {tasks.length === 0 ? (
              <div className="panel flex flex-col items-center justify-center gap-2 py-20 text-foreground-500">
                <Wand2 size={28} />
                <p className="text-sm">还没有作品，输入提示词开始创作吧</p>
              </div>
            ) : (
              <div className="grid gap-4 xl:grid-cols-2">
                {tasks.map((t) => (
                  <TaskCard key={t.id} task={t} onChanged={loadTasks} pricing={pricing} onRefreshUser={refreshUser} />
                ))}
              </div>
            )}
          </div>
        </>
      )}

      <AssetDrawer
        open={assetPicker}
        kindFilter={assetPicker === "first" || assetPicker === "last" ? "image" : undefined}
        counts={refCounts}
        room={assetPicker === "ref" ? refRoom : { total: 1, video: 0, audio: 0 }}
        existingIds={
          assetPicker === "ref"
            ? refImages.map((r) => r.id)
            : assetPicker === "first"
              ? lastImage
                ? [lastImage.id]
                : []
              : assetPicker === "last"
                ? firstImage
                  ? [firstImage.id]
                  : []
                : []
        }
        recentIds={recentIds}
        onClose={() => setAssetPicker(null)}
        onAddAssets={(items) =>
          assetPicker === "ref" ? addAssetRefs(items) : addFrameAsset(items, assetPicker === "first" ? "first" : "last")
        }
        onAddFiles={(files) =>
          assetPicker === "ref" ? addRefFiles(files) : addFrameFiles(files, assetPicker === "first" ? "first" : "last")
        }
      />

      <Toast message={toast.message} visible={toast.visible} />
    </div>
  );
}
