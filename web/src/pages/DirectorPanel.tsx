/** 分镜模式（导演台）：整体风格 + 分镜卡片列表，一次提交跑完全部段。
 *
 * 对接后端 mode=director API：segments 每段带提示词/时长/参考图（≤9 张），
 * 提交后由后端吸附帧数并独占整卡生成。 */
import {
  Coins,
  Film,
  ImagePlus,
  Loader2,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { useRef, useState } from "react";
import { api, fileUrl } from "../api/client";
import type { Pricing, UploadOut } from "../types";

const MAX_PROMPT = 8000;
const MAX_REFS = 9;
const MIN_SECS = 1;
const MAX_SECS = 10;
const DEFAULT_SECS = 10;

const ASPECTS = ["16:9", "9:16", "1:1"] as const;

interface SceneDraft {
  id: number;
  prompt: string;
  duration: number;
  refs: UploadOut[];
}

interface DirectorPanelProps {
  pricing: Pricing | null;
  onSubmitted: () => void;
}

/** 单张分镜卡片：提示词 + 时长滑杆 + 参考图。 */
function SceneCard({
  scene,
  index,
  onUpdate,
  onDelete,
}: {
  scene: SceneDraft;
  index: number;
  onUpdate: (patch: Partial<SceneDraft>) => void;
  onDelete: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  async function addRefs(files: FileList | null) {
    if (!files) return;
    const room = MAX_REFS - scene.refs.length;
    if (room <= 0) {
      alert(`每段参考图最多 ${MAX_REFS} 张`);
      return;
    }
    const accepted = Array.from(files).slice(0, room);
    setUploading(true);
    try {
      const ups: UploadOut[] = [];
      for (const f of accepted) {
        ups.push(await api.uploadImage(f, "reference"));
      }
      onUpdate({ refs: [...scene.refs, ...ups] });
    } catch (err) {
      alert(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="rounded-xl border border-background-200 bg-background-100 p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="flex items-center gap-2 text-sm font-semibold text-foreground-950">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-primary-100 text-xs font-bold text-primary-700">
            {index + 1}
          </span>
          分镜 {index + 1}
        </span>
        <button
          type="button"
          onClick={onDelete}
          title="删除分镜"
          className="rounded-md p-1.5 text-foreground-500 transition hover:bg-rose-50 hover:text-rose-600"
        >
          <Trash2 size={14} />
        </button>
      </div>

      <textarea
        value={scene.prompt}
        onChange={(e) => onUpdate({ prompt: e.target.value.slice(0, MAX_PROMPT) })}
        rows={3}
        maxLength={MAX_PROMPT}
        placeholder="描述本段镜头内容，例如，“雨夜街头，主角撑伞回头，暖黄路灯，镜头缓慢推近。”"
        className="w-full resize-none rounded-lg border border-background-300 bg-background-50 px-3 py-2.5 text-sm text-foreground-900 placeholder-foreground-500 outline-none transition focus:border-primary-500"
      />

      {/* 参考图 */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {scene.refs.map((r) => (
          <div key={r.id} className="group relative h-16 w-[68px] shrink-0 overflow-hidden rounded-lg border border-background-300">
            <img src={fileUrl(r.url)} alt="参考" className="h-full w-full object-cover" />
            <button
              type="button"
              onClick={() => onUpdate({ refs: scene.refs.filter((x) => x.id !== r.id) })}
              className="absolute right-0.5 top-0.5 hidden rounded bg-foreground-950/60 p-0.5 text-background-50 group-hover:block"
              title="移除"
            >
              <X size={10} />
            </button>
          </div>
        ))}
        {scene.refs.length < MAX_REFS && (
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            className="flex h-16 w-[68px] shrink-0 flex-col items-center justify-center gap-0.5 rounded-lg border border-dashed border-background-300 text-foreground-500 transition hover:border-primary-400 hover:text-primary-600"
          >
            <input
              ref={fileRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              className="hidden"
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => {
                addRefs(e.target.files);
                e.target.value = "";
              }}
            />
            {uploading ? <Loader2 size={14} className="animate-spin" /> : <ImagePlus size={14} />}
            <span className="text-[10px]">{scene.refs.length}/{MAX_REFS}</span>
          </button>
        )}
      </div>

      {/* 时长 */}
      <div className="mt-3 flex items-center gap-3">
        <Film size={13} className="shrink-0 text-foreground-500" />
        <input
          type="range"
          min={MIN_SECS}
          max={MAX_SECS}
          step={1}
          value={scene.duration}
          onChange={(e) => onUpdate({ duration: Number(e.target.value) })}
          className="range-slider flex-1"
        />
        <span className="w-14 shrink-0 text-right text-xs text-foreground-600">
          {scene.duration} 秒
        </span>
      </div>
    </div>
  );
}

export default function DirectorPanel({ pricing, onSubmitted }: DirectorPanelProps) {
  const [overallStyle, setOverallStyle] = useState("");
  const [aspect, setAspect] = useState<(typeof ASPECTS)[number]>("16:9");
  const [scenes, setScenes] = useState<SceneDraft[]>([]);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const idRef = useRef(1);

  const totalSecs = scenes.reduce((sum, s) => sum + s.duration, 0);
  const estCost = pricing
    ? Math.max(1, Math.ceil(totalSecs / 5)) * pricing.cost_768p_5s
    : 0;

  const addScene = () => {
    const id = idRef.current;
    idRef.current += 1;
    setScenes((prev) => [...prev, { id, prompt: "", duration: DEFAULT_SECS, refs: [] }]);
  };

  const updateScene = (id: number, patch: Partial<SceneDraft>) => {
    setScenes((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  };

  const deleteScene = (id: number) => {
    setScenes((prev) => prev.filter((s) => s.id !== id));
  };

  const openConfirm = () => {
    setError("");
    if (scenes.length === 0) {
      setError("请先添加分镜");
      return;
    }
    const empty = scenes.find((s) => !s.prompt.trim());
    if (empty) {
      setError(`第 ${scenes.indexOf(empty) + 1} 段提示词为空`);
      return;
    }
    setConfirmOpen(true);
  };

  const submit = async () => {
    setSubmitting(true);
    setError("");
    try {
      await api.createVideo({
        mode: "director",
        prompt: overallStyle.trim() || "长视频导演台",
        aspect_ratio: aspect,
        duration: 5,
        resolution: "768p",
        enhance: false,
        scene: "general",
        segments: scenes.map((s) => ({
          prompt: s.prompt.trim(),
          duration: s.duration,
          ref_image_ids: s.refs.map((r) => r.id),
        })),
      });
      setConfirmOpen(false);
      setScenes([]);
      setOverallStyle("");
      onSubmitted();
    } catch (err) {
      setConfirmOpen(false);
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* 整体风格 + 画幅 */}
      <div className="panel p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-foreground-950">分镜编辑</h2>
          <div className="inline-flex rounded-full bg-background-200 p-1">
            {ASPECTS.map((a) => (
              <button
                key={a}
                type="button"
                onClick={() => setAspect(a)}
                className={`rounded-full px-3 py-1 text-xs transition ${
                  aspect === a
                    ? "bg-background-50 text-foreground-900 shadow-sm"
                    : "text-foreground-500 hover:text-foreground-800"
                }`}
              >
                {a}
              </button>
            ))}
          </div>
        </div>
        <textarea
          value={overallStyle}
          onChange={(e) => setOverallStyle(e.target.value.slice(0, 2000))}
          rows={3}
          maxLength={2000}
          placeholder="描述视频的整体风格、色调、氛围等…"
          className="w-full resize-none rounded-lg border border-background-300 bg-background-50 px-3 py-2.5 text-sm text-foreground-900 placeholder-foreground-500 outline-none transition focus:border-primary-500"
        />
        <div className="mt-1 text-right text-xs text-foreground-500">{overallStyle.length} / 2000</div>
      </div>

      {/* 分镜列表 */}
      <div className="space-y-3">
        {scenes.map((scene, i) => (
          <SceneCard
            key={scene.id}
            scene={scene}
            index={i}
            onUpdate={(patch) => updateScene(scene.id, patch)}
            onDelete={() => deleteScene(scene.id)}
          />
        ))}
        {scenes.length === 0 && (
          <div className="panel flex flex-col items-center justify-center gap-2 py-12 text-foreground-500">
            <Film size={24} />
            <p className="text-sm">还没有分镜，点击「添加分镜」开始创作</p>
          </div>
        )}
      </div>

      {/* 提交区 */}
      <div className="panel flex flex-col items-center gap-3 p-5">
        <div className="flex flex-wrap items-center justify-center gap-4 text-sm">
          <span className="text-foreground-600">
            共 {scenes.length} 段 · {totalSecs} 秒
          </span>
          <span className="flex items-center gap-1.5 text-foreground-800">
            <Coins size={15} className="text-amber-500" />
            预计消耗 {estCost} 积分
          </span>
        </div>
        <div className="flex items-center gap-3">
          <button type="button" onClick={addScene} className="btn-ghost">
            <Plus size={15} /> 添加分镜
          </button>
          <button
            type="button"
            onClick={openConfirm}
            disabled={scenes.length === 0 || submitting}
            className="btn-primary"
          >
            <Film size={15} /> 生成完整视频
          </button>
        </div>
        {error && <p className="text-sm text-rose-600">{error}</p>}
      </div>

      {/* 确认弹层 */}
      {confirmOpen && (
        <div
          className="fixed inset-0 z-30 flex items-center justify-center bg-foreground-950/40 px-4"
          onClick={() => setConfirmOpen(false)}
        >
          <div className="panel w-full max-w-sm p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-2 text-base font-semibold text-foreground-950">确认生成完整视频</h3>
            <p className="mb-4 text-xs leading-relaxed text-foreground-500">
              共 {scenes.length} 段、合计 {totalSecs} 秒，导演台将独占一张卡一次性生成全部片段
              （长视频耗时较长，请耐心等待）。
            </p>
            <div className="mb-4 flex items-center justify-between rounded-lg bg-background-200 px-3 py-2.5 text-sm">
              <span className="text-foreground-600">预计消耗</span>
              <span className="flex items-center gap-1 font-semibold text-foreground-950">
                <Coins size={14} className="text-amber-500" /> {estCost} 积分
              </span>
            </div>
            <div className="flex justify-end gap-2">
              <button className="btn-ghost" onClick={() => setConfirmOpen(false)} disabled={submitting}>
                取消
              </button>
              <button className="btn-primary" onClick={submit} disabled={submitting}>
                {submitting ? (
                  <>
                    <Loader2 size={15} className="animate-spin" /> 提交中…
                  </>
                ) : (
                  "确认生成"
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
