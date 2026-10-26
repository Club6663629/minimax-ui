/** 剧集工坊·分集页：单集真相（script）+ 切表（storyboard）+ 成片位。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  Clapperboard,
  Download,
  Film,
  ListTree,
  Wand2,
  XCircle,
} from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api, fileUrl } from "../api/client";
import { CAMERA_LABEL, GATE_LABEL, SHOT_SIZE_LABEL, errText } from "../components/studio/labels";
import type {
  StudioAsset,
  StudioCut,
  StudioEpisode,
  StudioGates,
  StudioKeyframe,
  StudioRender,
  StudioScript,
  StudioScriptEpisode,
  StudioSegment,
  StudioSegmentClip,
  StudioStoryboard,
  StudioSummary,
} from "../types";

/** 一场：标题行 + 节拍流水。台词与动作分开渲染，扫一眼就知道哪里缺东西。 */
function SceneCard({
  index,
  scene,
  nameOf,
}: {
  index: number;
  scene: { sceneId?: string; characters?: string[]; props?: string[]; flow?: Array<Record<string, string>> };
  nameOf: (ref: string) => string;
}) {
  const flow = scene.flow || [];
  const chars = (scene.characters || []).map(nameOf).join("、");
  return (
    <div className="rounded-xl border border-background-200 bg-background-50 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] text-foreground-500">
          #{index + 1} {scene.sceneId || "—"}
        </span>
        {chars && <span className="chip">{chars}</span>}
        <span className="ml-auto text-[11px] text-foreground-500">{flow.length} 个节拍</span>
      </div>
      <ol className="mt-2 space-y-1">
        {flow.map((beat, i) => (
          <li key={i} className="flex gap-2 text-xs leading-5">
            <span className="w-5 shrink-0 text-right font-mono text-[10px] text-foreground-400">
              {i + 1}
            </span>
            {beat.line ? (
              <span className="text-foreground-800">
                <span className="mr-1 text-foreground-500">[{beat.speaker || "VO"}]</span>
                {beat.line}
                {beat.delivery && (
                  <span className="ml-1 text-[11px] text-foreground-400">（{beat.delivery}）</span>
                )}
              </span>
            ) : (
              <span className="text-foreground-600">
                <span className="mr-1 text-[11px] text-foreground-400">动作</span>
                {beat.action}
              </span>
            )}
          </li>
        ))}
        {flow.length === 0 && <li className="text-xs text-foreground-400">（空场）</li>}
      </ol>
    </div>
  );
}

/** 一条节奏带：按切秒数占比排开，一眼看切长分布与是不是一路匀速。 */
function RhythmBar({ cuts }: { cuts: StudioCut[] }) {
  const total = cuts.reduce((n, c) => n + c.seconds, 0) || 1;
  return (
    <div className="flex h-1.5 overflow-hidden rounded-full bg-background-200">
      {cuts.map((cut, i) => (
        <div
          key={i}
          title={`#${i + 1} ${SHOT_SIZE_LABEL[cut.size] || cut.size} ${cut.seconds}s`}
          className={i % 2 ? "bg-primary-300" : "bg-primary-500"}
          style={{ width: `${(cut.seconds / total) * 100}%` }}
        />
      ))}
    </div>
  );
}

/** 一段 = 一次生成。段头给时长与切数，展开看每切的景别、运镜与提示词。 */
function SegmentCard({
  seg,
  nameOf,
  keyframes,
  clip,
  onQueue,
  onRender,
  busy,
  renderBusy,
}: {
  seg: StudioSegment;
  nameOf: (ref: string) => string;
  keyframes: Record<string, StudioKeyframe>;
  clip?: StudioSegmentClip;
  onQueue: (segmentId: string) => void;
  onRender: (segmentId: string, force?: boolean) => void;
  busy: boolean;
  renderBusy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const seconds = seg.cuts.reduce((n, c) => n + c.seconds, 0);
  const readyCount = seg.cuts.filter(
    (_, i) => keyframes[`${seg.id}:${i + 1}`]?.status === "ready",
  ).length;
  // 排队中也要露出来：否则连点「出图」看不出任何变化，也分不清是没响应还是在出图
  const queuedCount = seg.cuts.filter(
    (_, i) => keyframes[`${seg.id}:${i + 1}`]?.status === "queued",
  ).length;
  // 一段一次 r2v：关键帧齐了才能出片（h3Prompt 的 Picture N 与切序强绑定）
  const kfReady = readyCount === seg.cuts.length;
  const rendering = clip?.status === "queued" || clip?.status === "running";
  const renderLabel = renderBusy
    ? "排单中"
    : clip?.status === "queued"
      ? "排队中"
      : clip?.status === "running"
        ? "生成中"
        : clip?.status === "ready"
          ? "重出"
          : "出片";
  return (
    <div className="min-w-0 rounded-xl border border-background-200 bg-background-50">
      <div className="flex items-center gap-1 px-2.5 pt-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          onClick={() => setOpen((v) => !v)}
        >
          <span className="font-mono text-[11px] text-foreground-600">{seg.id}</span>
          <span className="chip">场 {seg.sceneIndex}</span>
          <span className="ml-auto shrink-0 font-mono text-[11px] text-foreground-500">
            {seconds.toFixed(1)}s · {seg.cuts.length} 切
          </span>
          <ChevronDown
            size={13}
            className={`shrink-0 text-foreground-400 transition ${open ? "rotate-180" : ""}`}
          />
        </button>
        <button
          type="button"
          className="shrink-0 rounded-md border border-background-300 px-1.5 py-0.5 text-[10px] text-foreground-600 transition hover:bg-background-200 disabled:opacity-50"
          onClick={() => onQueue(seg.id)}
          disabled={busy || queuedCount > 0 || rendering}
        >
          {busy ? "排单中" : queuedCount > 0 ? "排队中" : "出图"}
        </button>
        <button
          type="button"
          title={kfReady ? "按关键帧出这一段" : "关键帧没出齐，先点「出图」"}
          className="shrink-0 rounded-md border border-background-300 px-1.5 py-0.5 text-[10px] text-foreground-600 transition hover:bg-background-200 disabled:opacity-50"
          onClick={() => onRender(seg.id, clip?.status === "ready")}
          disabled={renderBusy || rendering || !kfReady}
        >
          {renderLabel}
        </button>
      </div>
      <div className="flex items-center gap-2 px-2.5 pt-1.5">
        <div className="min-w-0 flex-1">
          <RhythmBar cuts={seg.cuts} />
        </div>
        <span className="shrink-0 whitespace-nowrap font-mono text-[10px] text-foreground-400">
          图 {readyCount}/{seg.cuts.length}
          {queuedCount > 0 && ` · 排队 ${queuedCount}`}
        </span>
      </div>
      <ol className="space-y-1 px-2.5 py-1.5">
        {seg.cuts.map((cut, i) => {
          const kf = keyframes[`${seg.id}:${i + 1}`];
          const thumb = kf?.status === "ready" && kf.url ? fileUrl(kf.url) : "";
          const hint =
            kf?.status === "queued" ? "排队中，稍后自动刷新" : "还没有关键帧";
          return (
            <li key={i} className="flex min-w-0 gap-1.5">
              <a
                href={thumb || undefined}
                target="_blank"
                rel="noreferrer"
                title={thumb ? `打开关键帧 #${i + 1}` : hint}
                className="mt-0.5 block h-11 w-6 shrink-0 overflow-hidden rounded border border-background-300 bg-background-200"
              >
                {thumb ? (
                  <img src={thumb} alt="" className="h-full w-full object-cover" />
                ) : (
                  <span
                    className={`flex h-full w-full items-center justify-center text-[9px] text-foreground-400 ${
                      kf?.status === "queued" ? "animate-pulse" : ""
                    }`}
                  >
                    {kf?.status === "queued" ? "…" : "—"}
                  </span>
                )}
              </a>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 text-[11px]">
                  <span className="w-4 shrink-0 text-right font-mono text-[10px] text-foreground-400">
                    {i + 1}
                  </span>
                  <span className="text-foreground-800">
                    {SHOT_SIZE_LABEL[cut.size] || cut.size}
                  </span>
                  <span className="text-foreground-500">
                    {CAMERA_LABEL[cut.camera] || cut.camera}
                  </span>
                  <span className="ml-auto font-mono text-[10px] text-foreground-500">
                    {cut.seconds.toFixed(1)}s
                  </span>
                </div>
                <p
                  className={`pl-5 text-[11px] leading-4 text-foreground-600 ${
                    open ? "whitespace-pre-wrap" : "truncate"
                  }`}
                >
                  {cut.frame}
                </p>
                {open && (
                  <div className="mt-1 space-y-0.5 pl-5 text-[11px] leading-4 text-foreground-500">
                    <div>角色：{cut.characters.map(nameOf).join("、") || "空场"}</div>
                    <div>
                      {cut.lens} · {cut.cameraPosition}
                    </div>
                    <div>{cut.composition}</div>
                    <div className="whitespace-pre-wrap text-foreground-700">{cut.shot}</div>
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {open && (
        <div className="space-y-1 border-t border-background-200 px-2.5 py-2 text-[11px] leading-4 text-foreground-500">
          <div>站位：{seg.blocking}</div>
          <div>环境声：{seg.soundscape}</div>
          {seg.cuts[0]?.look && <div>风格：{seg.cuts[0].look}</div>}
          {clip?.status === "ready" && clip.url && (
            <video
              src={fileUrl(clip.url)}
              controls
              preload="metadata"
              className="mt-1 w-full rounded-md border border-background-200"
            />
          )}
          {clip?.status === "failed" && (
            <div className="text-red-700">出片失败：{clip.error || "未知原因"}</div>
          )}
          <details>
            <summary className="cursor-pointer text-foreground-600">H3 提示词</summary>
            <pre className="mt-1 max-h-52 overflow-auto whitespace-pre-wrap rounded-md bg-background-200 p-2 text-[10px] leading-4 text-foreground-700">
              {seg.h3Prompt}
            </pre>
          </details>
        </div>
      )}
    </div>
  );
}

function GatePill({ kind, gates }: { kind: string; gates: StudioGates }) {
  const passed = gates.gates.filter((g) => g.ok).length;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs ${
        gates.passed ? "bg-accent-100 text-accent-800" : "bg-red-100 text-red-700"
      }`}
      title={gates.gates
        .filter((g) => !g.ok)
        .map((g) => `${g.label}${g.detail ? `：${g.detail}` : ""}`)
        .join("\n")}
    >
      {gates.passed ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
      {GATE_LABEL[kind] || kind} {passed}/{gates.gates.length}
    </span>
  );
}

export default function StudioEpisodePage() {
  const { pid, ep } = useParams();
  const projectId = Number(pid);
  const epNo = Number(ep);

  const [episode, setEpisode] = useState<StudioEpisode | null>(null);
  const [script, setScript] = useState<StudioScript | null>(null);
  const [docs, setDocs] = useState<Record<string, unknown>>({});
  const [gates, setGates] = useState<Record<string, StudioGates>>({});
  const [assets, setAssets] = useState<StudioAsset[]>([]);
  const [keyframes, setKeyframes] = useState<Record<string, StudioKeyframe>>({});
  const [clips, setClips] = useState<Record<string, StudioSegmentClip>>({});
  const [render, setRender] = useState<StudioRender | null>(null);
  const [summary, setSummary] = useState<StudioSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"" | "staging" | "storyboard">("");
  const [kfBusy, setKfBusy] = useState("");
  const [clipBusy, setClipBusy] = useState("");
  const [renderBusy, setRenderBusy] = useState(false);
  const [kfMsg, setKfMsg] = useState("");
  const [renderMsg, setRenderMsg] = useState("");
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [detail, assetList, kfList, clipList, renderOut] = await Promise.all([
        api.studioEpisode(projectId, epNo),
        api.studioAssets(projectId).catch(() => ({ items: [] as StudioAsset[] })),
        api
          .studioKeyframes(projectId, epNo)
          .catch(() => ({ items: [] as StudioKeyframe[] })),
        api
          .studioClips(projectId, epNo)
          .catch(() => ({ items: [] as StudioSegmentClip[] })),
        api.studioRender(projectId, epNo).catch(() => ({ render: null })),
      ]);
      setEpisode(detail.episode);
      setScript(detail.script);
      setDocs((detail.docs || {}) as Record<string, unknown>);
      setAssets(assetList.items);
      setSummary(detail.summary || null);
      setRender(renderOut.render);
      const kfMap: Record<string, StudioKeyframe> = {};
      for (const kf of kfList.items) kfMap[`${kf.segment_id}:${kf.cut_index}`] = kf;
      setKeyframes(kfMap);
      const clipMap: Record<string, StudioSegmentClip> = {};
      for (const clip of clipList.items) clipMap[clip.segment_id] = clip;
      setClips(clipMap);

      const kinds = ["script", "staging", "storyboard"] as const;
      const results = await Promise.all(
        kinds.map((kind) =>
          api.studioEpisodeGates(projectId, epNo, kind).catch(() => null),
        ),
      );
      const next: Record<string, StudioGates> = {};
      kinds.forEach((kind, i) => {
        const result = results[i];
        if (result) next[kind] = result;
      });
      setGates(next);
    } catch (exc) {
      setError(errText(exc));
    } finally {
      setLoading(false);
    }
  }, [projectId, epNo]);

  useEffect(() => {
    load();
  }, [load]);

  /** 只拉关键帧与段视频：轮询进度时不能把 3 组门检查一起重跑（21 道门很贵）。 */
  const loadProgress = useCallback(async () => {
    const [kfList, clipList] = await Promise.all([
      api.studioKeyframes(projectId, epNo).catch(() => null),
      api.studioClips(projectId, epNo).catch(() => null),
    ]);
    if (kfList) {
      const kfMap: Record<string, StudioKeyframe> = {};
      for (const kf of kfList.items) kfMap[`${kf.segment_id}:${kf.cut_index}`] = kf;
      setKeyframes(kfMap);
    }
    if (clipList) {
      const clipMap: Record<string, StudioSegmentClip> = {};
      for (const clip of clipList.items) clipMap[clip.segment_id] = clip;
      setClips(clipMap);
    }
  }, [projectId, epNo]);

  /** 生成分段 / 切表：后端算完直接回门结果，再整体刷新拉最新文档。 */
  const runGenerate = async (kind: "staging" | "storyboard") => {
    setBusy(kind);
    setActionError("");
    try {
      const out = await api.studioGenerate(projectId, epNo, kind);
      setGates((prev) => ({ ...prev, [kind]: out.gates }));
      await load();
    } catch (exc) {
      setActionError(errText(exc));
    } finally {
      setBusy("");
    }
  };

  const nameMap = useMemo(() => {
    const map: Record<string, string> = {};
    for (const asset of assets) {
      map[asset.ref_id] = asset.name;
      if (asset.data?.id) map[asset.data.id] = asset.name;
      if (asset.data?.name) map[asset.data.name] = asset.name;
    }
    return map;
  }, [assets]);
  const nameOf = (ref: string) => nameMap[ref] || ref;

  const current: StudioScriptEpisode | undefined =
    script?.episodes?.find((e) => e.ep === epNo) || script?.episodes?.[0];
  const totalBeats =
    current?.scenes?.reduce((sum, scene) => sum + (scene.flow?.length || 0), 0) || 0;
  const storyboard = docs.storyboard as StudioStoryboard | undefined;
  const segments = useMemo(() => {
    const eps = storyboard?.episodes || [];
    return eps.find((e) => e.ep === epNo)?.segments || eps[0]?.segments || [];
  }, [storyboard, epNo]);
  // 成片要等全部段出片：缺一段就是缺剧情的片子，后端也会再拦一次
  const readySegs = useMemo(
    () => segments.filter((s) => clips[s.id]?.status === "ready").length,
    [segments, clips],
  );
  const allClipsReady = segments.length > 0 && readySegs === segments.length;

  /** 给一段的每一切排关键帧；缺设定图时后端会把原因回在 blockers 里。 */
  const queueKeyframes = async (segmentId: string) => {
    setKfBusy(segmentId);
    setKfMsg("");
    try {
      const out = await api.studioQueueKeyframes(projectId, epNo, segmentId);
      setKfMsg(
        out.blockers.length
          ? out.blockers.join("；")
          : `已排 ${out.queued} 张，跳过 ${out.skipped} 张`,
      );
      await loadProgress();
    } catch (exc) {
      setKfMsg(errText(exc));
    } finally {
      setKfBusy("");
    }
  };

  /** 一段一次 r2v：关键帧不齐时后端会把原因回在 blockers 里。 */
  const queueClip = async (segmentId: string, force = false) => {
    setClipBusy(segmentId);
    setKfMsg("");
    try {
      const out = await api.studioQueueClip(projectId, epNo, segmentId, force);
      setKfMsg(
        out.blockers.length
          ? out.blockers.join("；")
          : out.queued
            ? "已排入生成队列，出片后自动刷新"
            : "这一段已在队列里或已出片，未重复排单",
      );
      await loadProgress();
    } catch (exc) {
      setKfMsg(errText(exc));
    } finally {
      setClipBusy("");
    }
  };

  /** 合成成片：同步拼接，失败把 ffmpeg 的错误原样回显；每次合成新增一条记录。 */
  const renderEpisode = async () => {
    setRenderBusy(true);
    setRenderMsg("");
    try {
      const out = await api.studioRenderEpisode(projectId, epNo);
      setRender(out.render);
      setRenderMsg(out.render.status === "done" ? `已合成 ${out.render.duration_sec} 秒` : "");
    } catch (exc) {
      setRenderMsg(errText(exc));
    } finally {
      setRenderBusy(false);
    }
  };

  // 关键帧或段视频在跑就自动刷新（同一条轻量链路，不重跑门）
  const hasQueued = useMemo(
    () =>
      Object.values(keyframes).some((kf) => kf.status === "queued") ||
      Object.values(clips).some((c) => c.status === "queued" || c.status === "running"),
    [keyframes, clips],
  );
  useEffect(() => {
    if (!hasQueued) return;
    const timer = setTimeout(() => {
      loadProgress();
    }, 5000);
    return () => clearTimeout(timer);
  }, [hasQueued, keyframes, clips, loadProgress]);
  const staging = docs.staging as
    | { locations?: Array<{ items?: unknown[] }>; scenes?: unknown[] }
    | undefined;
  const stagingInfo = staging
    ? `${staging.scenes?.length || 0} 场 · ${staging.locations?.length || 0} 地点 · ${(
        staging.locations || []
      ).reduce((n, loc) => n + (loc.items?.length || 0), 0)} 件`
    : "";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link
          to={`/studio/${projectId}`}
          className="inline-flex items-center gap-1 text-xs text-foreground-600 hover:text-foreground-900"
        >
          <ArrowLeft size={14} /> 项目
        </Link>
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-foreground-950">
            <Clapperboard size={18} /> EP{String(epNo).padStart(2, "0")}
            <span className="truncate text-sm font-normal text-foreground-600">
              {episode?.name || current?.title || ""}
            </span>
          </h1>
          <p className="mt-0.5 text-xs text-foreground-500">
            {current?.scenes?.length || 0} 场 · {totalBeats} 个节拍 · 目标{" "}
            {current?.targetSeconds || 0} 秒
            {summary?.segments ? (
              <>
                {" · 切表 "}
                {summary.segments} 段 / {summary.cuts} 切 / {summary.seconds} 秒
              </>
            ) : null}
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          {(["script", "staging", "storyboard"] as const).map((kind) =>
            gates[kind] ? (
              <GatePill key={kind} kind={kind} gates={gates[kind]} />
            ) : (
              <span key={kind} className="chip">
                {GATE_LABEL[kind]} 未跑
              </span>
            ),
          )}
          <button
            type="button"
            className="btn-ghost !px-3 !py-1.5 text-xs"
            onClick={() => runGenerate("staging")}
            disabled={!!busy || loading}
          >
            {busy === "staging" ? "分段中…" : "生成分段"}
          </button>
          <button
            type="button"
            className="btn-primary !px-3 !py-1.5 text-xs"
            onClick={() => runGenerate("storyboard")}
            disabled={!!busy || loading}
          >
            <Wand2 size={13} />
            {busy === "storyboard" ? "切表中…" : "生成切表"}
          </button>
          <button
            type="button"
            className="btn-ghost !px-3 !py-1.5 text-xs"
            onClick={load}
            disabled={loading}
          >
            {loading ? "刷新中…" : "重跑门"}
          </button>
        </div>
      </div>

      {error && (
        <div className="panel border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}
      {actionError && (
        <div className="panel border-red-300 bg-red-50 p-3 text-sm text-red-700">
          生成失败：{actionError}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
        {/* 真相 */}
        <div className="min-w-0 space-y-2">
          <div className="flex items-center gap-2 text-xs font-medium text-foreground-600">
            <ListTree size={14} /> 单集真相（script.json）
            {current?.hook && (
              <span className="truncate font-normal text-foreground-500">钩子：{current.hook}</span>
            )}
          </div>
          {(current?.scenes || []).map((scene, i) => (
            <SceneCard key={`${scene.sceneId}-${i}`} index={i} scene={scene} nameOf={nameOf} />
          ))}
          {!current?.scenes?.length && (
            <div className="panel p-6 text-sm text-foreground-500">
              {loading ? "加载中…" : "这一集还没有 script 文档。"}
            </div>
          )}
        </div>

        {/* 切表 + 成片位 */}
        <div className="min-w-0 space-y-3">
          <div className="panel p-4">
            <div className="flex items-center gap-2">
              <Film size={14} className="text-foreground-600" />
              <span className="text-sm font-semibold text-foreground-900">切表</span>
              <span className="chip ml-auto">
                {segments.length} 段 · {summary?.cuts || 0} 切
              </span>
            </div>
            <p className="mt-1 text-[11px] text-foreground-500">
              分段：{stagingInfo || "未生成"}
              {summary?.seconds ? ` · 成片 ${summary.seconds} 秒` : ""}
              {storyboard?.aspectRatio ? ` · ${storyboard.aspectRatio}` : ""}
            </p>
            {kfMsg && (
              <p className="mt-1 text-[11px] leading-4 text-amber-700">{kfMsg}</p>
            )}
            {segments.length ? (
              <div className="mt-2 space-y-1.5">
                {segments.map((seg) => (
                  <SegmentCard
                    key={seg.id}
                    seg={seg}
                    nameOf={nameOf}
                    keyframes={keyframes}
                    clip={clips[seg.id]}
                    onQueue={queueKeyframes}
                    onRender={queueClip}
                    busy={kfBusy === seg.id}
                    renderBusy={clipBusy === seg.id}
                  />
                ))}
              </div>
            ) : (
              <div className="mt-2 space-y-1">
                <p className="text-xs text-foreground-500">
                  还没有切表。先生成分段，再生成切表；门全过才允许出关键帧。
                </p>
                {gates.storyboard?.gates
                  .filter((g) => !g.ok)
                  .slice(0, 6)
                  .map((g) => (
                    <div key={g.id} className="text-[11px] text-red-700">
                      {g.label}
                      {g.detail ? `：${g.detail}` : ""}
                    </div>
                  ))}
              </div>
            )}
          </div>

          <div className="panel p-4">
            <div className="flex items-center gap-2">
              <Clapperboard size={14} className="text-foreground-600" />
              <span className="text-sm font-semibold text-foreground-900">成片位</span>
              <span className="chip ml-auto">
                {render?.status === "done"
                  ? `成片 ${render.duration_sec}s`
                  : `段 ${readySegs}/${segments.length}`}
              </span>
              <button
                type="button"
                title={
                  allClipsReady
                    ? "按切表顺序拼接全部段"
                    : "还有段没出片，拼出来就是缺剧情的片子"
                }
                className="shrink-0 rounded-md border border-background-300 px-2 py-1 text-[11px] text-foreground-700 transition hover:bg-background-200 disabled:opacity-50"
                onClick={renderEpisode}
                disabled={!allClipsReady || renderBusy || render?.status === "rendering"}
              >
                {renderBusy
                  ? "合成中…"
                  : render?.status === "done"
                    ? "重新合成"
                    : "合成成片"}
              </button>
            </div>
            {render?.status === "done" && render.url ? (
              <>
                <video
                  src={fileUrl(render.url)}
                  controls
                  preload="metadata"
                  className="mx-auto mt-2 w-full max-w-[200px] rounded-xl border border-background-200 bg-black"
                />
                <div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-foreground-500">
                  <span>
                    {render.duration_sec} 秒 · {render.cutlist.length} 段
                  </span>
                  <a
                    href={`${fileUrl(render.url)}&download=1`}
                    download
                    className="inline-flex items-center gap-1 text-foreground-700 hover:text-foreground-950"
                  >
                    <Download size={12} /> 下载 MP4
                  </a>
                </div>
                <details className="mt-2 text-[11px] text-foreground-500">
                  <summary className="cursor-pointer text-foreground-600">合成清单</summary>
                  <ol className="mt-1 space-y-0.5">
                    {render.cutlist.map((item) => (
                      <li key={item.segment_id} className="flex justify-between gap-2 font-mono">
                        <span>{item.segment_id}</span>
                        <span>
                          计划 {item.planned_sec.toFixed(1)}s · 实测 {item.actual_sec.toFixed(2)}s
                        </span>
                      </li>
                    ))}
                  </ol>
                </details>
              </>
            ) : (
              <div className="mx-auto mt-2 flex aspect-[9/16] w-full max-w-[200px] flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-background-400 bg-background-50 px-3 text-center text-xs text-foreground-400">
                <span>
                  {allClipsReady
                    ? "全部段已就绪，点「合成成片」"
                    : `还差 ${segments.length - readySegs} 段出片`}
                </span>
                {render?.status === "failed" && <span className="text-red-700">上次合成失败</span>}
              </div>
            )}
            {renderMsg && (
              <p className="mt-2 text-[11px] leading-4 text-amber-700">{renderMsg}</p>
            )}
            {render?.status === "failed" && (
              <p className="mt-2 text-[11px] leading-4 text-red-700">
                合成失败：{render.error || "未知原因"}
              </p>
            )}
            <p className="mt-2 text-[11px] leading-4 text-foreground-500">
              段 = 一次生成；切 = 关键帧。成片按切表顺序拼接，每次合成新增一条记录，旧成片保留。
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
