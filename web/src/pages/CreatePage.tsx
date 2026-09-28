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
  Music,
  Plus,
  SlidersHorizontal,
  Sparkles,
  Wand2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, fileUrl } from "../api/client";
import AssetPanel from "./AssetPanel";
import DirectorPanel from "./DirectorPanel";
import TaskCard from "../components/TaskCard";
import { useAuth } from "../state/auth";
import {
  ACTIVE_STATUSES,
  RES_LABEL,
  type Pricing,
  type Task,
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
type CreateMode = "single" | "director" | "assets";

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

/** 模式菜单：单段模式 / 分镜模式 / 资产管理。 */
function ModeTabs({ mode, onChange }: { mode: CreateMode; onChange: (m: CreateMode) => void }) {
  const tabs: { key: CreateMode; label: string; icon: React.ReactNode }[] = [
    { key: "single", label: "单段模式", icon: <Film size={14} /> },
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

/** 全能参考：追加参考瓦片（接受图片/视频/音频） */
function RefAddTile({ count, counts, busy, onPick }: { count: number; counts: { image: number; video: number; audio: number }; busy: boolean; onPick: (f: File[]) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <button
      type="button"
      onClick={() => inputRef.current?.click()}
      title={`上限：图片 ${counts.image}/9 · 视频 ${counts.video}/${MAX_VIDEO_REFS} · 音频 ${counts.audio}/${MAX_AUDIO_REFS}`}
      className="flex h-24 w-[76px] shrink-0 flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-background-300 bg-background-50 text-foreground-500 transition hover:border-primary-400 hover:text-foreground-800"
    >
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm,video/x-matroska,audio/*"
        multiple
        className="hidden"
        onClick={(e) => e.stopPropagation()}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length) onPick(files);
        }}
      />
      {busy ? <Loader2 size={18} className="animate-spin" /> : <Plus size={18} />}
      <span className="text-xs">参考</span>
      <span className="text-[10px] text-foreground-500">
        ({count}/{MAX_REFS})
      </span>
    </button>
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

/** 首尾帧：单个帧槽位 */
function FrameSlot({ label, value, onChange }: { label: string; value: UploadOut | null; onChange: (u: UploadOut | null) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    try {
      onChange(await api.uploadImage(file, label === "首帧" ? "first" : "last"));
    } catch (err) {
      alert(err instanceof Error ? err.message : "上传失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative h-24 w-[76px] shrink-0">
      <input ref={inputRef} type="file" accept="image/jpeg,image/png,image/webp" className="hidden" onChange={onFile} />
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
          onClick={() => inputRef.current?.click()}
          className="flex h-full w-full flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-background-300 bg-background-50 text-foreground-500 transition hover:border-primary-400 hover:text-foreground-800"
        >
          {busy ? <Loader2 size={18} className="animate-spin" /> : <Plus size={18} />}
          <span className="text-xs">{label}</span>
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

  async function addRefs(files: File[]) {
    // 当前各类参考数量（基于已有参考）
    const counts = { ...refCounts };
    let room = MAX_REFS - refImages.length;
    if (room <= 0) {
      alert(`参考总数最多 ${MAX_REFS} 个`);
      return;
    }
    const accepted: File[] = [];
    for (const f of files) {
      if (room <= 0) break;
      const kind = fileKindOf(f);
      if (kind === "video" && counts.video >= MAX_VIDEO_REFS) {
        alert(`参考视频最多 ${MAX_VIDEO_REFS} 个`);
        continue;
      }
      if (kind === "audio" && counts.audio >= MAX_AUDIO_REFS) {
        alert(`参考音频最多 ${MAX_AUDIO_REFS} 个`);
        continue;
      }
      accepted.push(f);
      counts[kind] += 1;
      room -= 1;
    }
    if (!accepted.length) return;
    setUploading(true);
    try {
      for (const f of accepted) {
        const up = await api.uploadMedia(f, "reference");
        setRefImages((prev) => [...prev, up]);
      }
    } catch (err) {
      alert(err instanceof Error ? err.message : "上传失败");
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
        <ModeTabs mode={mode} onChange={setMode} />
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
                    {refImages.length < MAX_REFS && <RefAddTile count={refImages.length} counts={refCounts} busy={uploading} onPick={addRefs} />}
                  </>
                ) : (
                  <>
                    <FrameSlot label="首帧" value={firstImage} onChange={setFirstImage} />
                    <FrameSlot label="尾帧" value={lastImage} onChange={setLastImage} />
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
                title={cloudEnabled ? "智能提示词增强" : "智能提示词增强（未配置云端 API）"}
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

      <Toast message={toast.message} visible={toast.visible} />
    </div>
  );
}
