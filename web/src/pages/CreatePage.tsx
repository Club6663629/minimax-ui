/** 创作页：统一创作输入卡（参考图 + 提示词 + 模式/配置）+ 任务流（轮询刷新）。
 *
 * 输入方式对齐官方产品形态：是否上传参考图与「全能参考 / 首尾帧」选项共同决定
 * 生成模式（未上传任何输入图时为文生视频），不再用独立 tab 区分模式。
 */
import {
  AlertCircle,
  ArrowUpCircle,
  Box,
  Check,
  ChevronDown,
  Coins,
  Download,
  Film,
  Images,
  Loader2,
  Plus,
  RotateCcw,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Wand2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, fileUrl } from "../api/client";
import { useAuth } from "../state/auth";
import {
  ACTIVE_STATUSES,
  MODE_LABEL,
  RES_LABEL,
  STATUS_LABEL,
  type Pricing,
  type Task,
  type TaskStatus,
  type UploadOut,
} from "../types";

const ASPECTS = ["16:9", "9:16", "1:1"] as const;
const DURATIONS = [5, 8, 10, 15] as const;
const RESOLUTIONS = ["768p", "1k", "2k"] as const;
type Resolution = (typeof RESOLUTIONS)[number];
const MAX_REFS = 9; // 全能参考官方上限 9 张参考图

type RefMode = "r2v" | "flf2v";

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
  "inline-flex items-center gap-1.5 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-zinc-300 transition hover:border-white/25";

function StatusBadge({ status }: { status: TaskStatus }) {
  const color =
    status === "done"
      ? "bg-emerald-500/15 text-emerald-400"
      : status === "failed"
        ? "bg-rose-500/15 text-rose-400"
        : "bg-indigo-500/15 text-indigo-300";
  const spinning = ACTIVE_STATUSES.includes(status);
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs ${color}`}>
      {spinning && <Loader2 size={11} className="animate-spin" />}
      {STATUS_LABEL[status]}
    </span>
  );
}

/** 全能参考：追加参考图瓦片 */
function RefAddTile({ count, busy, onPick }: { count: number; busy: boolean; onPick: (f: File[]) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <button
      type="button"
      onClick={() => inputRef.current?.click()}
      className="flex h-24 w-[76px] shrink-0 flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-white/15 bg-white/[0.03] text-zinc-500 transition hover:border-indigo-400/50 hover:text-zinc-300"
    >
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
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
      <span className="text-[10px] text-zinc-600">
        ({count}/{MAX_REFS})
      </span>
    </button>
  );
}

/** 全能参考：已上传参考图缩略图（角标即提示词中的 <Picture N> 序号） */
function RefThumb({ index, value, onRemove }: { index: number; value: UploadOut; onRemove: () => void }) {
  return (
    <div className="group relative h-24 w-[76px] shrink-0 overflow-hidden rounded-xl border border-white/10">
      <img src={fileUrl(value.url)} alt={`参考 ${index + 1}`} className="h-full w-full object-cover" />
      <span className="absolute left-1 top-1 rounded bg-black/60 px-1 text-[10px] text-zinc-200">{index + 1}</span>
      <button
        type="button"
        onClick={onRemove}
        className="absolute right-1 top-1 hidden rounded-md bg-black/60 p-0.5 text-zinc-300 group-hover:block"
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
        <div className="group h-full w-full overflow-hidden rounded-xl border border-white/10">
          <img src={fileUrl(value.url)} alt={label} className="h-full w-full object-cover" />
          <button
            type="button"
            onClick={() => onChange(null)}
            className="absolute right-1 top-1 rounded-md bg-black/60 p-0.5 text-zinc-300 opacity-0 group-hover:opacity-100"
            title="移除"
          >
            <X size={12} />
          </button>
          <span className="absolute bottom-1 left-1 rounded bg-black/60 px-1 text-[10px] text-zinc-200">{label}</span>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="flex h-full w-full flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-white/15 bg-white/[0.03] text-zinc-500 transition hover:border-indigo-400/50 hover:text-zinc-300"
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
        <cur.icon size={14} /> {cur.label} <ChevronDown size={13} className="text-zinc-500" />
      </button>
      {open && (
        <div className="absolute left-0 top-full z-20 mt-2 w-44 rounded-xl border border-white/10 bg-ink-800 p-1 shadow-2xl">
          {items.map((it) => (
            <button
              key={it.key}
              type="button"
              onClick={() => {
                onChange(it.key);
                setOpen(false);
              }}
              className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-sm transition ${
                value === it.key ? "bg-white/[0.08] text-white" : "text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
              }`}
            >
              <it.icon size={15} /> {it.label}
              {value === it.key && <Check size={14} className="ml-auto text-indigo-400" />}
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
  cloudEnabled,
}: {
  resolution: Resolution;
  onResolution: (r: Resolution) => void;
  duration: (typeof DURATIONS)[number];
  onDuration: (d: (typeof DURATIONS)[number]) => void;
  aspect: (typeof ASPECTS)[number];
  onAspect: (a: (typeof ASPECTS)[number]) => void;
  upscaleEnabled: boolean;
  cloudEnabled: boolean;
}) {
  const { open, setOpen, ref } = usePopover();
  const optCls = (active: boolean, disabled = false) =>
    `rounded-md px-2.5 py-1 text-xs transition ${
      active ? "bg-indigo-500/20 text-indigo-300" : "text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
    } ${disabled ? "cursor-not-allowed opacity-40" : ""}`;
  // 1K/2K 走本地超分池；超分池关闭时 2K 仍可通过云端降级通道（需配置云端 API）
  const disabledOf = (r: Resolution) => r !== "768p" && !upscaleEnabled && !(r === "2k" && cloudEnabled);
  return (
    <div className="relative" ref={ref}>
      <button type="button" onClick={() => setOpen(!open)} className={chipCls}>
        <SlidersHorizontal size={13} />
        {RES_LABEL[resolution]} · {duration}s · {aspect}
      </button>
      {open && (
        <div className="absolute left-0 top-full z-20 mt-2 w-56 space-y-2.5 rounded-xl border border-white/10 bg-ink-800 p-3 shadow-2xl">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-zinc-500">分辨率</span>
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
            <span className="text-xs text-zinc-500">时长</span>
            <div className="flex gap-1">
              {DURATIONS.map((d) => (
                <button key={d} type="button" onClick={() => onDuration(d)} className={optCls(duration === d)}>
                  {d}s
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-zinc-500">画幅</span>
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

/** 任务卡片 */
function TaskCard({ task, onChanged, pricing, onRefreshUser }: { task: Task; onChanged: () => void; pricing: Pricing | null; onRefreshUser?: () => void }) {
  const [retrying, setRetrying] = useState(false);
  const [upgrading, setUpgrading] = useState<"1k" | "2k" | null>(null);

  async function retry() {
    setRetrying(true);
    try {
      await api.retryVideo(task.id);
      onChanged();
    } catch (err) {
      alert(err instanceof Error ? err.message : "重试失败");
    } finally {
      setRetrying(false);
    }
  }

  async function upgrade(res: "1k" | "2k") {
    setUpgrading(res);
    try {
      await api.upgradeVideo(task.id, res);
      await onChanged();
      onRefreshUser?.();
    } catch (err) {
      alert(err instanceof Error ? err.message : "升级失败");
    } finally {
      setUpgrading(null);
    }
  }

  async function remove() {
    if (!confirm("删除该任务记录？")) return;
    try {
      await api.deleteVideo(task.id);
      onChanged();
    } catch (err) {
      alert(err instanceof Error ? err.message : "删除失败");
    }
  }

  const inputImages = [
    ...(task.first_image_url ? [{ url: task.first_image_url, label: "首帧" }] : []),
    ...(task.last_image_url ? [{ url: task.last_image_url, label: "尾帧" }] : []),
    ...task.ref_image_urls.map((url, i) => ({ url, label: `参考 ${i + 1}` })),
  ];

  const isUpgradeTask = task.parent_task_id != null;
  const upgradeLabel = isUpgradeTask && task.upscale_target ? `升级 → ${RES_LABEL[task.upscale_target] ?? task.upscale_target}` : null;

  return (
    <div className="panel p-4">
      <div className="mb-2 flex items-center gap-2">
        <StatusBadge status={task.status} />
        <span className="chip">{MODE_LABEL[task.mode]}</span>
        <span className="chip">{task.aspect_ratio}</span>
        <span className="chip">{task.duration}s</span>
        <span className="chip">{RES_LABEL[task.resolution] ?? task.resolution}</span>
        {upgradeLabel && <span className="chip !border-violet-400/30 !text-violet-300">{upgradeLabel}</span>}
        <span className="ml-auto text-xs text-zinc-600">
          {new Date(task.created_at).toLocaleString("zh-CN", { hour12: false })}
        </span>
      </div>

      {inputImages.length > 0 && (
        <div className="mb-3 flex gap-2 overflow-x-auto">
          {inputImages.map((img) => (
            <img
              key={img.url}
              src={fileUrl(img.url)}
              alt={img.label}
              title={img.label}
              className="h-14 w-20 shrink-0 rounded-lg border border-white/10 object-cover"
            />
          ))}
        </div>
      )}

      <p className="mb-3 line-clamp-2 text-sm text-zinc-300" title={task.prompt}>
        {task.prompt}
      </p>

      {task.status === "done" && task.video_url && (
        <div className="space-y-2">
          <video controls preload="metadata" className="max-h-[360px] w-full rounded-xl bg-black" src={fileUrl(task.video_url)} />
          <div className="flex items-center gap-2">
            <a href={fileUrl(task.video_url)} download={`h3_video_${task.id}.mp4`} className="btn-primary !py-1.5 text-xs">
              <Download size={13} /> 下载视频
            </a>
            {/* 多分辨率下载选项 */}
            {Object.keys(task.upscale_urls).length > 0 && (
              <div className="flex items-center gap-1">
                {Object.entries(task.upscale_urls).map(([res, url]) => (
                  <a
                    key={res}
                    href={fileUrl(url)}
                    download={`h3_video_${task.id}_${res}.mp4`}
                    className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] text-zinc-400 transition hover:border-white/25 hover:text-zinc-200"
                  >
                    <Download size={10} /> {RES_LABEL[res]}
                  </a>
                ))}
              </div>
            )}
            <span className="text-xs text-zinc-500">消耗 {task.cost} 积分</span>
            <button onClick={remove} className="ml-auto rounded-lg p-1.5 text-zinc-500 hover:bg-white/5 hover:text-rose-400" title="删除">
              <Trash2 size={14} />
            </button>
          </div>
          {/* 768p 已完成：显示高清升级按钮 */}
          {task.resolution === "768p" && !isUpgradeTask && (
            <div className="flex items-center gap-2 border-t border-white/[0.06] pt-2">
              <ArrowUpCircle size={13} className="text-violet-400" />
              <span className="text-xs text-zinc-500">高清升级：</span>
              {(["1k", "2k"] as const).map((res) => {
                const extraCost = res === "1k" ? (pricing?.cost_1k_extra ?? 8) : (pricing?.cost_2k_extra ?? 15);
                const hasUpgraded = Object.keys(task.upscale_urls).includes(res);
                return (
                  <button
                    key={res}
                    onClick={() => upgrade(res)}
                    disabled={upgrading !== null || hasUpgraded}
                    className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs transition ${
                      hasUpgraded
                        ? "cursor-not-allowed border border-white/5 bg-white/[0.02] text-zinc-600"
                        : "border border-violet-400/20 bg-violet-500/[0.07] text-violet-300 hover:border-violet-400/40 hover:bg-violet-500/[0.12]"
                    } disabled:cursor-not-allowed`}
                  >
                    {upgrading === res ? <Loader2 size={11} className="animate-spin" /> : null}
                    {RES_LABEL[res]}
                    <span className="text-[10px] opacity-60">{extraCost}积分</span>
                    {hasUpgraded && <Check size={10} className="text-emerald-400" />}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {task.status === "failed" && (
        <div className="flex items-center gap-3 rounded-xl bg-rose-500/[0.07] px-3 py-2.5">
          <AlertCircle size={15} className="shrink-0 text-rose-400" />
          <p className="flex-1 text-xs text-rose-300">{task.error || "生成失败，积分已退还"}</p>
          <button onClick={retry} disabled={retrying} className="btn-ghost !px-3 !py-1.5 text-xs">
            <RotateCcw size={13} /> {retrying ? "提交中…" : "重试"}
          </button>
        </div>
      )}

      {ACTIVE_STATUSES.includes(task.status) && (
        <div className="flex items-center gap-2 text-xs text-zinc-500">
          <div className="h-1 flex-1 overflow-hidden rounded-full bg-white/[0.06]">
            <div className="h-full w-1/3 animate-pulse rounded-full bg-gradient-to-r from-indigo-500 to-violet-500" />
          </div>
          {isUpgradeTask
            ? task.status === "upscaling"
              ? "高清升级排队中，请耐心等待…"
              : "处理中，请耐心等待"
            : "多卡并行处理中，请耐心等待"}
        </div>
      )}
    </div>
  );
}

export default function CreatePage() {
  const { user, refreshUser } = useAuth();
  const [pricing, setPricing] = useState<Pricing | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);

  // 表单状态：参考图上传方式 + 全能参考/首尾帧 选项共同决定生成模式
  const [refMode, setRefMode] = useState<RefMode>("r2v");
  const [refImages, setRefImages] = useState<UploadOut[]>([]);
  const [firstImage, setFirstImage] = useState<UploadOut | null>(null);
  const [lastImage, setLastImage] = useState<UploadOut | null>(null);
  const [uploading, setUploading] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [enhance, setEnhance] = useState(true);
  const [aspect, setAspect] = useState<(typeof ASPECTS)[number]>("16:9");
  const [duration, setDuration] = useState<(typeof DURATIONS)[number]>(5);
  const [resolution, setResolution] = useState<Resolution>("768p");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

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
    if (resolution === "1k") c += pricing.cost_1k_extra;
    if (resolution === "2k") c += pricing.cost_2k_extra;
    return c;
  }, [pricing, duration, resolution]);

  const cloudEnabled = pricing?.cloud_enabled ?? false;
  const upscaleEnabled = pricing?.upscale_enabled ?? true;

  // 是否上传参考图 + 模式选项 → 实际生成模式
  const effMode: Task["mode"] =
    refMode === "flf2v" ? (firstImage || lastImage ? "flf2v" : "t2v") : refImages.length > 0 ? "r2v" : "t2v";

  async function addRefs(files: File[]) {
    const room = MAX_REFS - refImages.length;
    if (room <= 0) return;
    if (files.length > room) alert(`最多上传 ${MAX_REFS} 张参考图`);
    setUploading(true);
    try {
      for (const f of files.slice(0, room)) {
        const up = await api.uploadImage(f, "reference");
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
      {/* ---- 创作输入卡 ---- */}
      <div className="panel p-4 sm:p-5">
        <div className="flex flex-col gap-4 sm:flex-row">
          {/* 左侧：参考图 / 首尾帧 上传区 */}
          <div className="flex shrink-0 gap-2">
            {refMode === "r2v" ? (
              <>
                {refImages.map((img, i) => (
                  <RefThumb
                    key={img.id}
                    index={i}
                    value={img}
                    onRemove={() => setRefImages((prev) => prev.filter((_, j) => j !== i))}
                  />
                ))}
                {refImages.length < MAX_REFS && <RefAddTile count={refImages.length} busy={uploading} onPick={addRefs} />}
              </>
            ) : (
              <>
                <FrameSlot label="首帧" value={firstImage} onChange={setFirstImage} />
                <FrameSlot label="尾帧" value={lastImage} onChange={setLastImage} />
              </>
            )}
          </div>

          {/* 右侧：提示词 */}
          <textarea
            className="min-h-[110px] flex-1 resize-none bg-transparent text-sm leading-relaxed text-zinc-100 placeholder-zinc-500 outline-none sm:min-h-[120px]"
            placeholder="描述您想要生成的视频内容，例如，“一个孩子在公园里放风筝，金色阳光，镜头上移。”"
            maxLength={2000}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
        </div>

        {/* 底部工具栏 */}
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-white/[0.06] pt-4">
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
            cloudEnabled={cloudEnabled}
          />
          <button
            type="button"
            onClick={() => setEnhance(!enhance)}
            disabled={!cloudEnabled}
            title={cloudEnabled ? "智能提示词增强" : "智能提示词增强（未配置云端 API）"}
            className={`rounded-full p-2 transition ${
              enhance && cloudEnabled ? "bg-violet-500/15 text-violet-300" : "text-zinc-500 hover:bg-white/5"
            } disabled:cursor-not-allowed disabled:opacity-40`}
          >
            <Sparkles size={15} />
          </button>

          <div className="ml-auto flex items-center gap-3">
            <span className="flex items-center gap-1.5 text-sm text-zinc-300" title={`余额 ${user?.credits ?? 0} 积分`}>
              <Coins size={15} className="text-amber-400" /> {cost}
            </span>
            {(user?.credits ?? 0) < cost && (
              <Link to="/recharge" className="text-xs text-indigo-400 hover:underline">
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

        {error && <p className="mt-3 text-sm text-rose-400">{error}</p>}
      </div>

      {/* ---- 我的作品 ---- */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-white">我的作品</h2>
          <span className="text-xs text-zinc-600">共 {tasks.length} 条</span>
        </div>

        {tasks.length === 0 ? (
          <div className="panel flex flex-col items-center justify-center gap-2 py-20 text-zinc-600">
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
    </div>
  );
}
