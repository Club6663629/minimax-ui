/** 电商广告片：商品图 + 场景描述 → N 张候选广告图 → 确认后用于生成广告片 → 下载成片。
 *
 * 业务闭环（一律以服务端任务为准，前端不靠内存状态续跑）：
 *   ① 表单：商品图(≤3) + 参考图(可选) + 场景描述 + 片长/比例/分辨率 → 生成候选广告图（不计费）
 *   ② 出图中：可「放弃本次」（删除任务，回表单）
 *   ③ 候选图已生成：确认后这 N 张候选图一起用于生成广告片（此时才计费）／「重新生成候选图」／「放弃本组」
 *   ④ 视频阶段：进度 → 播放/下载；可「新建广告片」（原任务保留在历史里）
 *
 * 提示词隔离：场景描述（图像）与视频提示词是两个独立输入框，互不回灌；视频提示词留空 = 沿用场景描述。
 * 计费口径：只在「确认并生成广告片」时扣一次（与单片视频一致），候选图阶段不消耗积分。
 */
import {
  AlertCircle,
  Check,
  Coins,
  Image as ImageIcon,
  Images,
  Loader2,
  Package,
  RefreshCw,
  Sparkles,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import TaskCard from "../components/TaskCard";
import { api, fileUrl } from "../api/client";
import { humanizeError } from "../lib/humanize";
import { useAuth } from "../state/auth";
import { RES_LABEL, type Pricing, type Task, type UploadOut } from "../types";

const ASPECTS = ["16:9", "9:16", "1:1"] as const;
const DURATIONS = [5, 8, 10] as const;
const RESOLUTIONS = ["768p", "2k", "4k"] as const;
const MAX_PRODUCT = 3; // 商品图上限（第 1 张为主商品）
const MAX_REFS = 6; // 可选参考图上限
const IMG_EXT_RE = /\.(jpe?g|png|webp)$/i;

type AdvanceMode = "llm" | "content_ir" | "local";

interface AdvideoStatus {
  enabled: boolean;
  worker: string;
  template_exists: boolean;
  available: boolean;
  /** 提示词增强默认是否开启（后端全局配置） */
  video_prompt_default_mode?: AdvanceMode;
  video_prompt_enhance_enabled?: boolean;
}

/** 提示词增强开关：开 = 开启（后端 llm 主方案：qwen-flash，失败自动回退 Content-IR），关 = 关闭（后端 local）。
 *  对用户只呈现「开 / 关」，不暴露后端实现细节。 */
const ENHANCE_ON: AdvanceMode = "llm";
const ENHANCE_OFF: AdvanceMode = "local";

/** 提示词增强开关（与视频生成页一致的星星开关）。 */
function EnhanceToggle({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={on}
      aria-label="提示词增强"
      title={on ? "提示词增强：已开启（自动扩写为专业镜头语言，商品一致性更稳）" : "提示词增强：已关闭（沿用您填写的内容）"}
      className={`rounded-full p-2 transition ${
        on ? "bg-accent-500/15 text-accent-700" : "text-foreground-500 hover:bg-background-200"
      }`}
    >
      <Sparkles size={15} />
    </button>
  );
}

const chipCls =
  "inline-flex items-center gap-1.5 rounded-full border border-background-300 bg-background-50 px-3 py-1.5 text-xs text-foreground-700";

/** 小缩略图（可移除）。 */
function Thumb({ value, label, onRemove }: { value: UploadOut; label: string; onRemove: () => void }) {
  return (
    <div className="group relative h-24 w-[76px] shrink-0 overflow-hidden rounded-xl border border-background-300">
      <img src={fileUrl(value.url)} alt={label} className="h-full w-full object-cover" />
      <span className="absolute left-1 top-1 rounded bg-foreground-950/60 px-1 text-[10px] text-background-50">{label}</span>
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

/** 上传瓦片（隐藏 input，点即选图）。 */
function UploadTile({ text, busy, onClick }: { text: string; busy: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className="flex h-24 w-[76px] shrink-0 flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-background-400 text-foreground-500 transition hover:border-primary-400 hover:text-foreground-800 disabled:opacity-50"
    >
      {busy ? <Loader2 size={16} className="animate-spin" /> : <UploadCloud size={16} />}
      <span className="text-[10px]">{text}</span>
    </button>
  );
}

export default function AdvideoPanel({
  pricing,
  tasks,
  onChanged,
  showToast,
  onRefreshUser,
}: {
  pricing: Pricing | null;
  tasks: Task[];
  onChanged: () => void;
  showToast: (m: string) => void;
  onRefreshUser?: () => void;
}) {
  const { user } = useAuth();
  const [product, setProduct] = useState<UploadOut[]>([]);
  const [refs, setRefs] = useState<UploadOut[]>([]);
  const [scenario, setScenario] = useState("");
  const [videoPrompt, setVideoPrompt] = useState("");
  // 提示词增强开关：默认跟随后端全局配置
  const [videoMode, setVideoMode] = useState<AdvanceMode>(ENHANCE_ON);
  const [imageCount, setImageCount] = useState(3);
  const [aspect, setAspect] = useState<(typeof ASPECTS)[number]>("16:9");
  const [duration, setDuration] = useState<(typeof DURATIONS)[number]>(8);
  const [resolution, setResolution] = useState<(typeof RESOLUTIONS)[number]>("768p");
  const [uploading, setUploading] = useState<"" | "product" | "ref">("");
  const [submitting, setSubmitting] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false); // 重新生成 / 放弃 等操作进行中
  const [error, setError] = useState("");
  const [activeId, setActiveId] = useState<number | null>(null);
  const [dismissed, setDismissed] = useState<number[]>([]); // 用户已主动离开的任务（不再自动弹回）
  const [node, setNode] = useState<AdvideoStatus | null>(null);

  const productInput = useRef<HTMLInputElement>(null);
  const refInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api
      .advideoStatus()
      .then((st) => {
        setNode(st);
        if (st.video_prompt_default_mode) setVideoMode(st.video_prompt_default_mode);
      })
      .catch(() => setNode(null));
  }, []);

  const advideoTasks = useMemo(
    () => tasks.filter((t) => t.mode === "advideo").sort((a, b) => b.id - a.id),
    [tasks],
  );

  const dismiss = (id: number | null | undefined) => {
    if (id == null) return;
    setDismissed((prev) => (prev.includes(id) ? prev : [...prev, id]));
    setActiveId((cur) => (cur === id ? null : cur));
  };

  /** 当前任务：显式选中的优先；否则取最近一条「还没结束、且用户没主动离开」的任务（刷新页面后可续上流程）。 */
  const activeTask = useMemo(() => {
    if (activeId != null) {
      const hit = advideoTasks.find((t) => t.id === activeId);
      if (hit) return hit;
    }
    return (
      advideoTasks.find(
        (t) => t.status !== "done" && t.status !== "failed" && !dismissed.includes(t.id),
      ) ?? null
    );
  }, [advideoTasks, activeId, dismissed]);

  const step: "form" | "images" | "review" | "video" = !activeTask
    ? "form"
    : activeTask.stage === "images_queued" || activeTask.stage === "images_running"
      ? "images"
      : activeTask.stage === "image_ready"
        ? "review"
        : "video";

  /** 最近一次任务就失败了：给一条可读的失败提示（可关闭），不劫持整页、不翻旧账。 */
  const lastFailed = useMemo(() => {
    const latest = advideoTasks[0];
    if (!latest || latest.status !== "failed" || dismissed.includes(latest.id)) return null;
    return latest;
  }, [advideoTasks, dismissed]);

  async function uploadFiles(files: FileList | null, which: "product" | "ref") {
    if (!files || !files.length) return;
    setError("");
    setUploading(which);
    try {
      let count = which === "product" ? product.length : refs.length;
      const max = which === "product" ? MAX_PRODUCT : MAX_REFS;
      for (const f of Array.from(files)) {
        if (!IMG_EXT_RE.test(f.name)) {
          setError("仅支持 JPG / PNG / WebP 图片");
          continue;
        }
        if (count >= max) {
          setError(which === "product" ? `商品图最多 ${MAX_PRODUCT} 张` : `参考图最多 ${MAX_REFS} 张`);
          break;
        }
        const up = await api.uploadMedia(f, "reference");
        count += 1;
        if (which === "product") setProduct((prev) => [...prev, up]);
        else setRefs((prev) => [...prev, up]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading("");
      if (which === "product" && productInput.current) productInput.current.value = "";
      if (which === "ref" && refInput.current) refInput.current.value = "";
    }
  }

  /** 计费口径：按实际生效的片长/清晰度算。表单用当前选择；任务阶段以服务端任务参数为准
   *  （confirm-image 不改片长/清晰度，生成用的就是任务上的值，页面必须显示同一套口径）。 */
  const costFor = (d: number, r: string) => {
    if (!pricing) return 0;
    const base: Record<number, number> = {
      5: pricing.cost_768p_5s,
      8: pricing.cost_768p_8s,
      10: pricing.cost_768p_10s,
      15: pricing.cost_768p_15s,
    };
    let c = base[d] ?? pricing.cost_768p_8s;
    if (r === "2k") c += pricing.cost_2k_extra;
    if (r === "4k") c += pricing.cost_4k_extra;
    return c;
  };
  const cost = costFor(duration, resolution);
  const taskDuration = activeTask?.duration ?? duration;
  const taskResolution = activeTask?.resolution ?? resolution;
  const taskCost = costFor(taskDuration, taskResolution);

  async function submit() {
    setError("");
    if (!product.length) {
      setError("请先上传商品图（第 1 张为主商品）");
      return;
    }
    if (!scenario.trim()) {
      setError("请填写场景描述（中文即可）");
      return;
    }
    setSubmitting(true);
    try {
      const t = await api.createAdvideo({
        prompt: scenario.trim(),
        product_image_ids: product.map((p) => p.id),
        ref_image_ids: refs.map((p) => p.id),
        aspect_ratio: aspect,
        duration,
        resolution,
        image_count: imageCount,
        enhance: true,
        video_prompt_mode: videoMode, // 提示词增强开/关
        scene: "ecommerce",
      });
      setActiveId(t.id);
      setVideoPrompt("");
      showToast("已提交，正在生成候选广告图…");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  }

  /** 重新生成候选图：以服务端任务参数为准重建一条任务，并放弃当前这组（候选图阶段不计费）。 */
  async function reroll() {
    if (!activeTask) return;
    setError("");
    setBusy(true);
    try {
      const t = await api.regenerateAdvideo(activeTask.id);
      dismiss(activeTask.id);
      setActiveId(t.id);
      showToast("正在重新生成候选广告图…");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "重新生成失败");
    } finally {
      setBusy(false);
    }
  }

  /** 放弃本组：删除当前任务（候选图阶段未计费，可直接删除），回到表单。 */
  async function abandon() {
    if (!activeTask) return;
    setError("");
    setBusy(true);
    try {
      await api.deleteVideo(activeTask.id);
      dismiss(activeTask.id);
      setVideoPrompt("");
      showToast("已放弃本组，可以重新开始");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "放弃失败");
    } finally {
      setBusy(false);
    }
  }

  /** 确认并生成广告片：候选图整组进入视频生成阶段（此时才计费）。 */
  async function confirm() {
    if (!activeTask) return;
    setError("");
    setConfirming(true);
    try {
      await api.confirmAdvideoImage(activeTask.id, {
        image_index: 0, // 第 1 张作为主参考图；整组候选图都会参与生成
        video_prompt: videoPrompt.trim(),
        enhance: true,
        video_prompt_mode: videoMode,
      });
      showToast("已提交，正在合成广告片");
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setConfirming(false);
    }
  }

  /** 新建广告片：离开当前任务回到表单（进行中的任务继续在后台生成，可在历史里找到）。 */
  function startNew() {
    dismiss(activeTask?.id);
    setVideoPrompt("");
    setError("");
  }

  function resetForm() {
    setActiveId(null);
    setProduct([]);
    setRefs([]);
    setScenario("");
    setError("");
  }

  const nodeReady = node ? node.enabled && node.available : true;
  const usedImages = activeTask?.ad_image_urls?.length ?? 0;

  return (
    <div className="space-y-6">
      {/* ---- 说明条 ---- */}
      <div className="panel flex flex-wrap items-center gap-3 p-4 sm:p-5">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary-100 text-primary-700">
          <Package size={18} />
        </span>
        <div className="min-w-[220px] flex-1">
          <h2 className="text-sm font-medium text-foreground-900">电商广告片</h2>
          <p className="mt-0.5 text-xs text-foreground-500">
            上传商品图、描述想要的场景 → 自动生成 {imageCount} 张候选广告图 → <b>确认后用于生成广告片</b> → 下载成片。
          </p>
        </div>
        {!nodeReady && (
          <span className={chipCls}>
            <X size={13} className="text-rose-500" /> 广告图生成暂时不可用，请稍后再试
          </span>
        )}
        {step === "video" && activeTask && (
          <button type="button" onClick={startNew} className="btn-ghost !px-3 !py-1.5 text-xs">
            <RefreshCw size={13} /> 新建广告片
          </button>
        )}
      </div>

      {/* ---- 失败提示（可关闭，不劫持整页） ---- */}
      {step === "form" && lastFailed && (
        <div className="panel flex flex-wrap items-start gap-3 border-l-4 border-l-rose-400 p-4">
          <AlertCircle size={16} className="mt-0.5 text-rose-500" />
          <div className="min-w-[200px] flex-1">
            <p className="text-sm text-foreground-900">上次生成未成功</p>
            <p className="mt-0.5 text-xs text-foreground-600">{humanizeError(lastFailed.error)}</p>
            <details className="mt-1">
              <summary className="cursor-pointer text-[11px] text-foreground-500">查看详情</summary>
              <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded-lg bg-background-100 p-2 text-[11px] text-foreground-600">
                {lastFailed.error || "（无更多信息）"}
              </pre>
            </details>
          </div>
          <button
            type="button"
            onClick={() => dismiss(lastFailed.id)}
            className="btn-ghost !px-3 !py-1.5 text-xs"
          >
            知道了
          </button>
        </div>
      )}

      {/* ---- ① 表单：商品图 + 场景 + 参数 ---- */}
      {step === "form" && (
        <div className="panel p-4 sm:p-5">
          <div className="flex flex-col gap-4 lg:flex-row">
            <div className="lg:w-[320px]">
              <p className="mb-2 text-xs font-medium text-foreground-700">
                商品图（{product.length}/{MAX_PRODUCT}，第 1 张为主商品）
              </p>
              <div className="flex flex-wrap gap-2">
                {product.map((p, i) => (
                  <Thumb
                    key={p.id}
                    value={p}
                    label={i === 0 ? "主商品" : `商品 ${i + 1}`}
                    onRemove={() => setProduct((prev) => prev.filter((_, j) => j !== i))}
                  />
                ))}
                {product.length < MAX_PRODUCT && (
                  <UploadTile text="加商品图" busy={uploading === "product"} onClick={() => productInput.current?.click()} />
                )}
              </div>
              <p className="mb-2 mt-4 text-xs font-medium text-foreground-700">
                参考图（可选 {refs.length}/{MAX_REFS}，如模特/背景）
              </p>
              <div className="flex flex-wrap gap-2">
                {refs.map((p, i) => (
                  <Thumb
                    key={p.id}
                    value={p}
                    label={`参考 ${i + 1}`}
                    onRemove={() => setRefs((prev) => prev.filter((_, j) => j !== i))}
                  />
                ))}
                {refs.length < MAX_REFS && (
                  <UploadTile text="加参考图" busy={uploading === "ref"} onClick={() => refInput.current?.click()} />
                )}
              </div>
              <input
                ref={productInput}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                multiple
                hidden
                onChange={(e) => uploadFiles(e.target.files, "product")}
              />
              <input
                ref={refInput}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                multiple
                hidden
                onChange={(e) => uploadFiles(e.target.files, "ref")}
              />
            </div>

            <div className="flex-1">
              <p className="mb-2 text-xs font-medium text-foreground-700">
                场景描述（自动扩写为专业提示词；中文即可）
              </p>
              <textarea
                className="min-h-[120px] w-full resize-none rounded-xl border border-background-300 bg-background-50 p-3 text-sm leading-relaxed text-foreground-900 placeholder-foreground-500 outline-none focus:border-primary-500"
                placeholder="例如：一杯手冲咖啡的电商海报，暖色调桌面静物，中文标语「慢火萃取」，留出右下角放价格标签。"
                maxLength={2000}
                value={scenario}
                onChange={(e) => setScenario(e.target.value)}
              />
              <p className="mt-1 text-[11px] text-foreground-500">
                提示：系统会按商品图自动补全商品细节（logo / 领口 / 纹理等）并保持与商品图一致，无需手工堆砌英文提示词。
              </p>
            </div>
          </div>

          {/* 参数栏 */}
          <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-background-200 pt-4">
            <span className={chipCls}>
              <Images size={13} /> 候选 {imageCount} 张
            </span>
            <select
              value={imageCount}
              onChange={(e) => setImageCount(Number(e.target.value))}
              className="rounded-full border border-background-300 bg-background-50 px-3 py-1.5 text-xs text-foreground-800 outline-none hover:border-primary-400"
              title="候选广告图张数"
            >
              {[2, 3, 4].map((n) => (
                <option key={n} value={n}>
                  {n} 张
                </option>
              ))}
            </select>
            <select
              value={aspect}
              onChange={(e) => setAspect(e.target.value as (typeof ASPECTS)[number])}
              className="rounded-full border border-background-300 bg-background-50 px-3 py-1.5 text-xs text-foreground-800 outline-none hover:border-primary-400"
            >
              {ASPECTS.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
            <select
              value={duration}
              onChange={(e) => setDuration(Number(e.target.value) as (typeof DURATIONS)[number])}
              className="rounded-full border border-background-300 bg-background-50 px-3 py-1.5 text-xs text-foreground-800 outline-none hover:border-primary-400"
            >
              {DURATIONS.map((d) => (
                <option key={d} value={d}>
                  {d} 秒
                </option>
              ))}
            </select>
            <select
              value={resolution}
              onChange={(e) => setResolution(e.target.value as (typeof RESOLUTIONS)[number])}
              className="rounded-full border border-background-300 bg-background-50 px-3 py-1.5 text-xs text-foreground-800 outline-none hover:border-primary-400"
            >
              {RESOLUTIONS.filter((r) => r === "768p" || (pricing?.upscale_enabled ?? true)).map((r) => (
                <option key={r} value={r}>
                  {RES_LABEL[r] ?? r}
                </option>
              ))}
            </select>
            <EnhanceToggle
              on={videoMode === ENHANCE_ON}
              onToggle={() => setVideoMode(videoMode === ENHANCE_ON ? ENHANCE_OFF : ENHANCE_ON)}
            />

            <div className="ml-auto flex items-center gap-3">
              <span className="flex items-center gap-1.5 text-xs text-foreground-500" title="生成广告片将消耗的积分">
                生成广告片 <Coins size={13} className="text-amber-500" /> {cost} 积分
              </span>
              {(user?.credits ?? 0) < cost && (
                <Link to="/recharge" className="text-xs text-primary-600 hover:underline">
                  去充值
                </Link>
              )}
              {(product.length > 0 || refs.length > 0 || scenario.trim().length > 0) && (
                <button type="button" onClick={resetForm} className="btn-ghost !px-3 !py-1.5 text-xs">
                  清空
                </button>
              )}
              <button onClick={submit} disabled={submitting || !nodeReady} className="btn-primary !rounded-full">
                {submitting ? (
                  <>
                    <Loader2 size={15} className="animate-spin" /> 提交中…
                  </>
                ) : (
                  <>
                    <Sparkles size={15} /> 生成候选广告图
                  </>
                )}
              </button>
            </div>
          </div>
          <p className="mt-2 text-[11px] text-foreground-500">生成候选广告图不消耗积分；确认合成为广告片时才按上面的片长/清晰度计费一次。</p>
          {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
        </div>
      )}

      {/* ---- ② 候选广告图生成中 ---- */}
      {step === "images" && activeTask && (
        <div className="panel p-6 text-center">
          <Loader2 size={22} className="mx-auto animate-spin text-primary-600" />
          <p className="mt-3 text-sm text-foreground-800">正在生成候选广告图…</p>
          <p className="mt-1 text-xs text-foreground-500">任务 #{activeTask.id} · 首次运行整组约需 3–8 分钟，请稍候</p>
          <div className="mt-4 flex flex-wrap items-center justify-center gap-3">
            <span className="text-xs text-foreground-500">生成完会自动展示，可先去做别的事</span>
            <button type="button" onClick={abandon} disabled={busy} className="btn-ghost !px-3 !py-1.5 text-xs">
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />} 放弃本次
            </button>
          </div>
          <p className="mx-auto mt-2 max-w-md text-[11px] text-foreground-500">
            放弃只丢弃本次候选广告图，不消耗积分。
          </p>
          {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
        </div>
      )}

      {/* ---- ③ 候选图已生成：整组合成广告片 ---- */}
      {step === "review" && activeTask && (
        <div className="panel p-4 sm:p-5">
          <div className="flex flex-wrap items-center gap-3">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-500/15 text-amber-600">
              <ImageIcon size={16} />
            </span>
            <div className="flex-1">
              <h3 className="text-sm font-medium text-foreground-900">候选广告图已生成</h3>
              <p className="mt-0.5 text-xs text-foreground-500">
                任务 #{activeTask.id} · 确认后这 {usedImages} 张候选图将一起用于生成广告片（多角度参考，成片更贴近商品）；不满意可重新生成。
              </p>
            </div>
            <span className={chipCls}>
              {taskDuration}s / {RES_LABEL[taskResolution] ?? taskResolution}
            </span>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
            {activeTask.ad_image_urls.map((u, i) => (
              <a
                key={i}
                href={fileUrl(u)}
                download={`advideo_${activeTask.id}_${i + 1}.png`}
                className="group relative block overflow-hidden rounded-xl border border-background-300 transition hover:border-primary-400"
                title="点击下载这张图"
              >
                <img
                  src={fileUrl(u)}
                  alt={`候选广告图 ${i + 1}`}
                  className="aspect-video w-full bg-background-200 object-contain"
                />
                <span className="absolute left-2 top-2 rounded bg-foreground-950/60 px-1.5 text-[10px] text-background-50">
                  候选 {i + 1}
                  {i === 0 ? " · 主参考" : ""}
                </span>
                <span className="absolute bottom-2 right-2 hidden rounded-md bg-foreground-950/60 px-1.5 py-0.5 text-[10px] text-background-50 group-hover:block">
                  下载
                </span>
              </a>
            ))}
          </div>

          <div className="mt-4 border-t border-background-200 pt-4">
            <p className="mb-2 text-xs font-medium text-foreground-700">视频提示词（选填，留空则沿用场景描述）</p>
            <textarea
              className="min-h-[90px] w-full resize-none rounded-xl border border-background-300 bg-background-50 p-3 text-sm leading-relaxed text-foreground-900 placeholder-foreground-500 outline-none focus:border-primary-500"
              placeholder="例如：镜头缓慢推进，商品居中，柔和顶光扫过瓶身，背景虚化；无口播，节奏感音乐。"
              maxLength={2000}
              value={videoPrompt}
              onChange={(e) => setVideoPrompt(e.target.value)}
            />
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <EnhanceToggle
                on={videoMode === ENHANCE_ON}
                onToggle={() => setVideoMode(videoMode === ENHANCE_ON ? ENHANCE_OFF : ENHANCE_ON)}
              />
              <span className="text-xs text-foreground-500">确认并生成广告片将消耗 {taskCost} 积分</span>
              {(user?.credits ?? 0) < taskCost && (
                <Link to="/recharge" className="text-xs text-primary-600 hover:underline">
                  去充值
                </Link>
              )}
              <div className="ml-auto flex items-center gap-2">
                <button type="button" onClick={abandon} disabled={busy || confirming} className="btn-ghost !px-3 !py-1.5 text-xs">
                  {busy ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />} 放弃本组
                </button>
                <button type="button" onClick={reroll} disabled={busy || confirming} className="btn-ghost !px-3 !py-1.5 text-xs">
                  {busy ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} 重新生成候选图
                </button>
                <button type="button" onClick={confirm} disabled={confirming || busy || usedImages === 0} className="btn-primary !py-2 text-xs">
                  {confirming ? (
                    <>
                      <Loader2 size={13} className="animate-spin" /> 提交中…
                    </>
                  ) : (
                    <>
                      <Check size={13} /> 确认并生成广告片
                    </>
                  )}
                </button>
              </div>
            </div>
            {error && <p className="mt-3 text-sm text-rose-600">{error}</p>}
          </div>
        </div>
      )}

      {/* ---- ④ 视频阶段：复用任务卡（进度/播放/下载/升级） ---- */}
      {step === "video" && activeTask && (
        <div className="space-y-2">
          <p className="text-xs text-foreground-500">
            已用 {usedImages || "-"} 张候选广告图合成广告片 · 任务 #{activeTask.id}
          </p>
          <TaskCard task={activeTask} onChanged={onChanged} pricing={pricing} onRefreshUser={onRefreshUser} />
        </div>
      )}

      {/* ---- 历史广告片任务 ---- */}
      {advideoTasks.filter((t) => t.id !== activeTask?.id).length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-medium text-foreground-800">历史广告片任务</h3>
          {advideoTasks
            .filter((t) => t.id !== activeTask?.id)
            .map((t) => (
              <TaskCard key={t.id} task={t} onChanged={onChanged} pricing={pricing} onRefreshUser={onRefreshUser} />
            ))}
        </div>
      )}
    </div>
  );
}
