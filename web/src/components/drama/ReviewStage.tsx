/** S6 审片：段标签页 + 段播放器、逐镜通过/返修、接缝指标与版本回滚。 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Clock3,
  Download,
  History,
  Layers,
  Loader2,
  RefreshCw,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  X,
} from "lucide-react";
import { api, fileUrl } from "../../api/client";
import type {
  DramaAiPrecheckShot,
  DramaArtifactVersion,
  DramaEpisode,
  DramaReview,
  DramaRun,
  DramaSegment,
  DramaShot,
} from "../../types";
import {
  SEGMENT_ENTRY_LABEL,
  SEGMENT_STATUS_LABEL,
  SEGMENT_STATUS_TONE,
  errText,
  fmtSecs,
  fmtTime,
  SHOT_STATUS_LABEL,
  SHOT_STATUS_TONE,
} from "./labels";
import SeamBadge from "./SeamBadge";

export default function ReviewStage({
  eid,
  episode,
  costPer5s,
  activePatchRun,
  notify,
  onPatchStarted,
  onSegmentStarted,
  onChanged,
  onGoShots,
  onGoDeliver,
}: {
  eid: number;
  episode: DramaEpisode;
  costPer5s: number;
  activePatchRun: DramaRun | null;
  notify: (message: string) => void;
  onPatchStarted: (run: DramaRun) => void;
  onSegmentStarted: (run: DramaRun) => void;
  onChanged: () => void | Promise<void>;
  onGoShots: () => void;
  onGoDeliver: () => void;
}) {
  const [review, setReview] = useState<DramaReview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showSheet, setShowSheet] = useState(false);
  const [rejectTarget, setRejectTarget] = useState<DramaShot | null>(null);
  const [reason, setReason] = useState("");
  const [rerun, setRerun] = useState(true);
  const [rejectBusy, setRejectBusy] = useState(false);
  const [versionsFor, setVersionsFor] = useState<DramaShot | null>(null);
  const [segmentVersionsFor, setSegmentVersionsFor] = useState<DramaSegment | null>(null);
  const [selectedSegmentId, setSelectedSegmentId] = useState<number | null>(null);
  const [segmentBusy, setSegmentBusy] = useState<number | null>(null);
  const [approvingId, setApprovingId] = useState<number | null>(null);
  /** AI 预检结果（整集）与返修意见扩写候选值 */
  const [precheckBusy, setPrecheckBusy] = useState(false);
  const [precheck, setPrecheck] = useState<
    { summary: string; results: DramaAiPrecheckShot[]; imageUsed: boolean; label: string } | null
  >(null);
  const [briefBusy, setBriefBusy] = useState(false);
  const [briefCandidate, setBriefCandidate] = useState<{ brief: string; label: string } | null>(null);
  const rowRefs = useRef(new Map<number, HTMLDivElement>());

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await api.dramaReview(eid);
      setReview(data);
    } catch (exc) {
      setError(errText(exc));
    } finally {
      setLoading(false);
    }
  }, [eid]);

  useEffect(() => {
    void load();
  }, [load, episode.updated_at, episode.final_url, episode.status]);

  useEffect(() => {
    if (!rejectTarget) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setRejectTarget(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rejectTarget]);

  const shots = review?.shots || [];
  const segments = review?.segments || episode.segments || [];
  const selectedSegment = selectedSegmentId
    ? segments.find((segment) => segment.id === selectedSegmentId) || null
    : null;
  const visibleShots = selectedSegment
    ? shots.filter((shot) => shot.segment_id === selectedSegment.id)
    : shots;

  const seams = useMemo(() => {
    const map = new Map<number, DramaReview["seams"][number]>();
    const visible = new Set(visibleShots.map((shot) => shot.shot_no));
    for (const seam of review?.seams || []) {
      if (visible.has(seam.between[0]) && visible.has(seam.between[1])) {
        map.set(seam.between[0], seam);
      }
    }
    return map;
    // visibleShots 随所选段变化；用 id 作为稳定依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [review, selectedSegment?.id]);

  const staleCount = shots.filter((shot) => shot.stale).length;
  const rejected = shots.filter((shot) => shot.status === "rejected").length;
  const rendered = shots.filter((shot) => shot.status === "rendered").length;
  const missingClip = shots.filter((shot) => !shot.clip_url).length;
  const finalStale = Boolean(review?.episode.final_stale ?? episode.final_stale);
  const canDeliver =
    shots.length > 0 &&
    segments.length > 0 &&
    segments.every((segment) => !segment.stale) &&
    rejected === 0 &&
    staleCount === 0 &&
    missingClip === 0 &&
    !finalStale &&
    Boolean(review?.episode.final_url);
  const generationCost = review?.stats.generation_cost ?? 0;
  const repairRatio =
    generationCost > 0 ? (review?.stats.repair_cost_total ?? 0) / generationCost : 0;

  const focusShot = (shotId: number | undefined) => {
    if (!shotId) return;
    window.setTimeout(() => {
      rowRefs.current.get(shotId)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 120);
  };

  /** 整集 AI 预检：一次联系表调用，结果同时由后端写入 shot.qa.ai。 */
  const runPrecheck = async () => {
    setPrecheckBusy(true);
    try {
      const result = await api.dramaAiPrecheck(eid, { scope: "episode" });
      setPrecheck({
        summary: result.summary,
        results: result.results,
        imageUsed: result.image_used,
        label: result.cached ? `${result.provider} 缓存` : result.provider || "AI",
      });
      const hits = result.results.filter((row) => row.issues.length > 0).length;
      notify(hits ? `AI 预检完成，${hits} 镜有问题` : "AI 预检完成，未发现明显问题");
      await load();
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setPrecheckBusy(false);
    }
  };

  /** 把一句话返修意见扩写成可执行的重采样要求（不提交）。 */
  const expandBrief = async () => {
    if (!rejectTarget) return;
    setBriefBusy(true);
    try {
      const result = await api.dramaAiRepairBrief(rejectTarget.id, reason.trim());
      setBriefCandidate({
        brief: result.brief,
        label: result.cached ? `${result.provider} 缓存` : result.provider || "AI",
      });
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBriefBusy(false);
    }
  };

  const approve = async (shot: DramaShot) => {
    setApprovingId(shot.id);
    try {
      await api.dramaApproveShot(shot.id);
      const next = shots.find((item, index) => {
        const current = shots.findIndex((entry) => entry.id === shot.id);
        return index > current && item.status === "rendered";
      });
      await load();
      await onChanged();
      notify(`第 ${shot.shot_no} 镜已通过`);
      if (next) focusShot(next.id);
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setApprovingId(null);
    }
  };

  const renderSegment = async (segment: DramaSegment, force: boolean) => {
    setSegmentBusy(segment.id);
    try {
      const result = await api.dramaRenderSegment(segment.id, { force });
      if (result.reused) {
        notify(`第 ${segment.segment_no} 段命中复用，本次 0 积分`);
        await load();
        await onChanged();
      } else if (result.run) {
        notify(`第 ${segment.segment_no} 段已提交，预估 ${result.cost} 积分`);
        onSegmentStarted(result.run);
      }
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setSegmentBusy(null);
    }
  };

  const approveSegment = async (segment: DramaSegment) => {
    setSegmentBusy(segment.id);
    try {
      await api.dramaApproveSegment(segment.id);
      await load();
      await onChanged();
      notify(`第 ${segment.segment_no} 段已通过`);
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setSegmentBusy(null);
    }
  };

  const rejectSegment = async (segment: DramaSegment) => {
    if (!window.confirm(`驳回第 ${segment.segment_no} 段？段内已通过的镜头会退回待审（不扣费）。`)) {
      return;
    }
    setSegmentBusy(segment.id);
    try {
      await api.dramaRejectSegment(segment.id);
      await load();
      await onChanged();
      notify(`第 ${segment.segment_no} 段已驳回`);
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setSegmentBusy(null);
    }
  };

  const submitReject = async () => {
    if (!rejectTarget) return;
    if (!reason.trim()) {
      notify("请填写返修原因");
      return;
    }
    setRejectBusy(true);
    try {
      const result = await api.dramaRejectShot(rejectTarget.id, {
        reason: reason.trim(),
        rerun,
        anchor: true,
      });
      setRejectTarget(null);
      setReason("");
      setBriefCandidate(null);
      if (result.run) {
        notify(
          result.cost
            ? `返修已提交，预计 ${result.cost} 积分`
            : "返修已提交",
        );
        onPatchStarted(result.run);
      } else {
        notify(`第 ${rejectTarget.shot_no} 镜已标记待返修`);
        await load();
        await onChanged();
      }
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setRejectBusy(false);
    }
  };

  if (!review && loading) {
    return (
      <div className="panel flex items-center justify-center gap-2 px-6 py-16 text-sm text-foreground-500">
        <Loader2 size={16} className="animate-spin" /> 正在汇总审片数据…
      </div>
    );
  }

  if (!review) {
    return (
      <div className="panel space-y-3 px-6 py-12 text-center">
        <p className="text-sm text-rose-600">{error || "审片数据加载失败"}</p>
        <button type="button" className="btn-ghost" onClick={() => void load()}>
          重试
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {activePatchRun && (
        <section className="panel flex items-center gap-2 border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800">
          <Loader2 size={14} className="animate-spin" />
          {activePatchRun.mode === "patch"
            ? "返修生成中"
            : activePatchRun.segment_no
              ? `第 ${activePatchRun.segment_no} 段生成中`
              : "生成中"}
          （run #{activePatchRun.id} · {activePatchRun.phase}），完成后自动刷新。
        </section>
      )}

      <section className="panel space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="flex items-center gap-1.5 text-sm font-medium text-foreground-900">
            <Layers size={15} /> 分段审片
          </h2>
          <span className="text-xs text-foreground-500">
            {segments.length} 段 · {segments.filter((segment) => !segment.stale).length} 段成片就绪
          </span>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {!selectedSegment && review.episode.final_url && (
              <a
                className="btn-ghost px-2 py-1 text-xs"
                href={fileUrl(review.episode.final_url)}
                target="_blank"
                rel="noreferrer"
              >
                在新窗口播放
              </a>
            )}
            {!selectedSegment && review.sheet_url && (
              <button
                type="button"
                className="btn-ghost px-2 py-1 text-xs"
                onClick={() => setShowSheet((value) => !value)}
              >
                {showSheet ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                联系表
              </button>
            )}
            {!selectedSegment && (
              <button
                type="button"
                className="btn-ghost px-2 py-1 text-xs"
                onClick={() => void runPrecheck()}
                disabled={precheckBusy || shots.length === 0 || missingClip === shots.length}
                title="用联系表看一遍整集，逐镜给出问题与返修建议"
              >
                <Sparkles size={12} />
                {precheckBusy ? "预检中…" : "AI 预检"}
              </button>
            )}
          </div>
        </div>

        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={() => setSelectedSegmentId(null)}
            className={`rounded-lg px-2 py-1 text-[11px] transition ${
              selectedSegmentId === null
                ? "bg-primary-100 text-primary-700"
                : "bg-background-100 text-foreground-600 hover:bg-background-200"
            }`}
          >
            整集（{shots.length} 镜）
          </button>
          {segments.map((segment) => (
            <button
              key={segment.id}
              type="button"
              onClick={() => setSelectedSegmentId(segment.id)}
              className={`rounded-lg px-2 py-1 text-[11px] transition ${
                selectedSegmentId === segment.id
                  ? "bg-primary-100 text-primary-700"
                  : "bg-background-100 text-foreground-600 hover:bg-background-200"
              }`}
            >
              段{segment.segment_no} · {SEGMENT_STATUS_LABEL[segment.status]}
              {segment.stale ? " · 旧版" : ""}
            </button>
          ))}
        </div>

        {selectedSegment ? (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-medium text-foreground-900">
                {selectedSegment.title || `第 ${selectedSegment.segment_no} 段`}
              </span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] ${SEGMENT_STATUS_TONE[selectedSegment.status]}`}>
                {SEGMENT_STATUS_LABEL[selectedSegment.status]}
              </span>
              <span className="text-foreground-500">
                {selectedSegment.shot_count} 镜 · {fmtSecs(selectedSegment.duration)} ·{" "}
                {SEGMENT_ENTRY_LABEL[selectedSegment.entry_mode] || selectedSegment.entry_mode} · 预估{" "}
                {selectedSegment.estimate_cost} 积分
              </span>
              <div className="ml-auto flex flex-wrap items-center gap-1.5">
                <button
                  type="button"
                  className="btn-ghost px-2 py-1 text-xs"
                  disabled={
                    segmentBusy === selectedSegment.id ||
                    selectedSegment.stale ||
                    !selectedSegment.cut_url ||
                    selectedSegment.status === "approved" ||
                    Boolean(selectedSegment.active_run) ||
                    Boolean(activePatchRun)
                  }
                  onClick={() => void approveSegment(selectedSegment)}
                  title={
                    selectedSegment.stale ? "段成片缺失或已过旧，请先重新生成本段" : "通过本段并批量通过段内待审镜头"
                  }
                >
                  <ThumbsUp size={12} />
                  {segmentBusy === selectedSegment.id
                    ? "…"
                    : selectedSegment.status === "approved"
                      ? "已通过"
                      : "通过本段"}
                </button>
                {selectedSegment.status === "approved" && (
                  <button
                    type="button"
                    className="btn-ghost px-2 py-1 text-xs text-rose-600"
                    disabled={segmentBusy === selectedSegment.id || Boolean(activePatchRun)}
                    onClick={() => void rejectSegment(selectedSegment)}
                  >
                    <ThumbsDown size={12} /> 驳回本段
                  </button>
                )}
                <button
                  type="button"
                  className="btn-ghost px-2 py-1 text-xs"
                  disabled={segmentBusy === selectedSegment.id || Boolean(activePatchRun)}
                  onClick={() => {
                    if (
                      !window.confirm(
                        `重新生成第 ${selectedSegment.segment_no} 段会重新扣费，其余段不受影响。确认继续？`,
                      )
                    ) {
                      return;
                    }
                    void renderSegment(selectedSegment, true);
                  }}
                >
                  <RefreshCw size={12} /> 重新生成本段
                </button>
                {selectedSegment.cut_url && (
                  <>
                    <button
                      type="button"
                      className="btn-ghost px-2 py-1 text-xs"
                      onClick={() => setSegmentVersionsFor(selectedSegment)}
                    >
                      <History size={12} /> 段版本
                    </button>
                    <a
                      className="btn-ghost px-2 py-1 text-xs"
                      href={fileUrl(`${selectedSegment.cut_url}?download=1`)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <Download size={12} /> 下载段成片
                    </a>
                  </>
                )}
              </div>
            </div>
            {selectedSegment.stale && (
              <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
                该段成片缺失或已过旧（分镜已改动），请重新生成本段后再审。
              </p>
            )}
            {selectedSegment.entry_mode === "continue" && (
              <p className="text-[11px] text-foreground-500">
                承接段：
                {selectedSegment.bridge_url
                  ? "桥接帧已建立（上一段末帧已注入首镜参考图）"
                  : "生成时自动从上一段末帧建立桥接"}
                {selectedSegment.bridge_note ? ` · ${selectedSegment.bridge_note}` : ""}
              </p>
            )}
            {selectedSegment.cut_url ? (
              <video
                key={selectedSegment.cut_url}
                src={fileUrl(selectedSegment.cut_url)}
                controls
                className="max-h-[320px] w-full rounded-xl bg-black"
              />
            ) : (
              <div className="rounded-xl bg-background-200 px-4 py-8 text-center text-xs text-foreground-500">
                该段还没有段成片，请先「生成本段」。
              </div>
            )}
          </div>
        ) : (
          <>
            {review.episode.final_url ? (
              <video
                key={review.episode.final_url}
                src={fileUrl(review.episode.final_url)}
                controls
                className="max-h-[360px] w-full rounded-xl bg-black"
              />
            ) : (
              <div className="rounded-xl bg-background-200 px-4 py-10 text-center text-xs text-foreground-500">
                还没有集成片；每段生成后可以先逐段审，全部段就绪会自动拼成整集。
              </div>
            )}

            {finalStale && (
              <p className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
                集成片已过旧（分镜或角色卡已改动），交付前需要重新生成受影响段。
              </p>
            )}

            {showSheet && review.sheet_url && (
              <img
                src={fileUrl(review.sheet_url)}
                alt="联系表"
                className="w-full rounded-xl border border-background-200"
              />
            )}
          </>
        )}
      </section>

      <section className="panel grid grid-cols-2 gap-3 px-4 py-3 text-xs md:grid-cols-4 xl:grid-cols-7">
        <div>
          <p className="text-foreground-500">待审</p>
          <p className="mt-0.5 text-sm text-sky-700">{review.stats.rendered}</p>
        </div>
        <div>
          <p className="text-foreground-500">已通过</p>
          <p className="mt-0.5 text-sm text-emerald-700">{review.stats.approved}</p>
        </div>
        <div>
          <p className="text-foreground-500">待返修</p>
          <p className="mt-0.5 text-sm text-rose-700">{review.stats.rejected}</p>
        </div>
        <div>
          <p className="text-foreground-500" title="当前成片与最新分镜/角色卡不一致的镜头数">
            旧片镜数
          </p>
          <p className="mt-0.5 text-sm text-foreground-700">{review.stats.stale}</p>
        </div>
        <div>
          <p className="text-foreground-500">段成片</p>
          <p className="mt-0.5 text-sm text-foreground-800">
            {segments.filter((segment) => !segment.stale).length} / {segments.length}
          </p>
        </div>
        <div>
          <p className="text-foreground-500">返修成本</p>
          <p className={`mt-0.5 text-sm ${repairRatio > 0.4 ? "text-rose-700" : "text-foreground-800"}`}>
            {review.stats.repair_cost_total} / {generationCost} 积分
          </p>
        </div>
        <div>
          <p className="text-foreground-500">接缝</p>
          <p className="mt-0.5 text-sm text-foreground-800">{review.seams.length} 处已测</p>
        </div>
      </section>

      {repairRatio > 0.4 && (
        <p className="rounded-xl bg-rose-50 px-3 py-2 text-xs text-rose-700">
          已返修成本占本集生成成本 {Math.round(repairRatio * 100)}%，建议重生成对应段（可能更省）。
        </p>
      )}

      {precheck && (
        <section className="panel space-y-2 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="flex items-center gap-1.5 text-sm font-medium text-foreground-900">
              <Sparkles size={14} /> AI 预检结果
            </h3>
            <span className="text-[10px] text-foreground-400">（{precheck.label}）</span>
            {!precheck.imageUsed && (
              <span className="text-[11px] text-amber-600">未取到画面，本次仅按文字预检</span>
            )}
            <button
              type="button"
              className="ml-auto rounded-lg p-1 text-foreground-400 hover:bg-background-200"
              onClick={() => setPrecheck(null)}
              title="关闭"
            >
              <X size={13} />
            </button>
          </div>
          {precheck.summary && <p className="text-xs text-foreground-700">{precheck.summary}</p>}
          {precheck.results.filter((row) => row.issues.length > 0).length === 0 ? (
            <p className="text-xs text-emerald-600">未发现明显问题，可继续逐镜审核。</p>
          ) : (
            <div className="space-y-1.5">
              {precheck.results
                .filter((row) => row.issues.length > 0)
                .map((row) => (
                  <div key={row.shot_no} className="rounded-lg bg-background-100 px-2.5 py-2">
                    <button
                      type="button"
                      className={`text-xs font-medium ${row.score >= 85 ? "text-emerald-700" : "text-amber-700"}`}
                      onClick={() =>
                        focusShot(shots.find((shot) => shot.shot_no === row.shot_no)?.id)
                      }
                    >
                      第 {row.shot_no} 镜 · {row.score} 分
                    </button>
                    {row.issues.map((issue, i) => (
                      <p key={i} className="mt-0.5 text-[11px] leading-relaxed text-foreground-600">
                        <span className="text-foreground-500">[{issue.severity}]</span>{" "}
                        {issue.evidence}
                        {issue.suggestion && (
                          <span className="text-foreground-500">　→ {issue.suggestion}</span>
                        )}
                      </p>
                    ))}
                  </div>
                ))}
            </div>
          )}
          <p className="text-[10px] text-foreground-400">
            预检只给建议，不改变镜头状态；可直接用「返修」提交具体意见。
          </p>
        </section>
      )}

      <section className="space-y-2">
        {selectedSegment && (
          <p className="text-xs text-foreground-500">
            当前只显示第 {selectedSegment.segment_no} 段的 {visibleShots.length} 个镜头；切回「整集」看全部。
          </p>
        )}
        {visibleShots.map((shot) => {
          const seam = seams.get(shot.shot_no);
          const shotSegment = segments.find((item) => item.id === shot.segment_id);
          const segmentOpen =
            !shotSegment || shotSegment.status === "review" || shotSegment.status === "approved";
          const disabled = shot.stale || !segmentOpen;
          return (
            <div key={shot.id} ref={(node) => {
              if (node) rowRefs.current.set(shot.id, node);
              else rowRefs.current.delete(shot.id);
            }}>
              <div className="panel grid grid-cols-[112px_minmax(0,1fr)] items-start gap-3 p-3 sm:grid-cols-[112px_1fr_auto]">
                <div className="relative h-[63px] w-[112px] overflow-hidden rounded-lg bg-black">
                  {shot.clip_url ? (
                    <video
                      src={fileUrl(shot.clip_url)}
                      muted
                      preload="metadata"
                      playsInline
                      className="h-full w-full object-cover"
                      onMouseEnter={(event) => void event.currentTarget.play().catch(() => {})}
                      onMouseLeave={(event) => {
                        event.currentTarget.pause();
                        event.currentTarget.currentTime = 0;
                      }}
                    />
                  ) : (
                    <span className="flex h-full items-center justify-center text-[10px] text-background-50/70">
                      无成片
                    </span>
                  )}
                  <span className="absolute left-1 top-1 rounded bg-black/60 px-1 text-[10px] text-background-50">
                    #{shot.shot_no}
                  </span>
                </div>

                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] ${SHOT_STATUS_TONE[shot.status]}`}>
                      {SHOT_STATUS_LABEL[shot.status]}
                    </span>
                    {shot.segment_no !== null && (
                      <span className="rounded bg-background-200 px-1.5 py-0.5 text-[10px] text-foreground-500">
                        段{shot.segment_no}
                      </span>
                    )}
                    {shot.stale && (
                      <span
                        className="rounded bg-background-200 px-1.5 py-0.5 text-[10px] text-foreground-500"
                        title="分镜或角色卡已改动，当前成片是旧片，需重新生成"
                      >
                        旧片
                      </span>
                    )}
                    <span className="text-[10px] text-foreground-400">
                      v{shot.version_no || 0} · {fmtSecs(shot.duration_s)}
                      {shot.repair_count > 0 && ` · 返修 ${shot.repair_count} 次`}
                    </span>
                    {shot.qa?.ai && (
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] ${
                          shot.qa.ai.stale
                            ? "bg-background-200 text-foreground-500"
                            : (shot.qa.ai.score ?? 0) >= 85
                              ? "bg-emerald-100 text-emerald-700"
                              : "bg-amber-100 text-amber-700"
                        }`}
                        title={
                          shot.qa.ai.issues?.length
                            ? shot.qa.ai.issues.map((item) => item.evidence).join("\n")
                            : "AI 预检未发现问题"
                        }
                      >
                        AI {shot.qa.ai.score ?? "—"}
                        {shot.qa.ai.stale ? " · 过期" : ""}
                      </span>
                    )}
                    <button
                      type="button"
                      className="ml-auto inline-flex items-center gap-0.5 text-[10px] text-foreground-500 hover:text-foreground-800"
                      onClick={() => setVersionsFor(shot)}
                    >
                      <History size={11} /> 版本
                    </button>
                  </div>
                  <p className="line-clamp-2 text-xs leading-relaxed text-foreground-700">
                    {shot.prompt}
                  </p>
                  {shot.dialogue && (
                    <p className="text-[11px] text-foreground-500">台词：{shot.dialogue}</p>
                  )}
                  {shot.status === "rejected" && (
                    <p className="text-[11px] text-rose-600">
                      已标记待返修（未生成）；可点「重新返修」提交。
                    </p>
                  )}
                </div>

                <div className="col-span-2 flex shrink-0 flex-wrap gap-1.5 sm:col-span-1 sm:flex-col">
                  <button
                    type="button"
                    className="btn-ghost px-2 py-1 text-xs"
                    disabled={disabled || approvingId === shot.id || shot.status === "approved" || !shot.clip_url}
                    onClick={() => void approve(shot)}
                    title={
                      disabled
                        ? segmentOpen
                          ? "旧版镜头需重新生成后再审"
                          : "该镜所属段还没生成完成"
                        : ""
                    }
                  >
                    <ThumbsUp size={12} />
                    {approvingId === shot.id ? "…" : shot.status === "approved" ? "已通过" : "通过"}
                  </button>
                  <button
                    type="button"
                    className="btn-ghost px-2 py-1 text-xs text-rose-600"
                    disabled={disabled || !shot.clip_url || Boolean(activePatchRun)}
                    onClick={() => {
                      setRejectTarget(shot);
                      setReason("");
                      setBriefCandidate(null);
                      setRerun(true);
                    }}
                    title={
                      disabled
                        ? segmentOpen
                          ? "旧版镜头需重新生成后再审"
                          : "该镜所属段不在审片阶段"
                        : ""
                    }
                  >
                    <ThumbsDown size={12} />
                    {shot.status === "rejected" ? "重新返修" : "返修"}
                  </button>
                </div>
              </div>

              {seam && (
                <div className="flex items-center justify-center gap-2 py-0.5 text-[10px] text-foreground-400">
                  <span className="h-px w-8 bg-background-300" />
                  <SeamBadge seam={seam} label={`${seam.between[0]}→${seam.between[1]}`} />
                  <span>接缝 {seam.between[0]}→{seam.between[1]}</span>
                  <span className="h-px w-8 bg-background-300" />
                </div>
              )}
            </div>
          );
        })}
      </section>

      <section className="panel flex flex-wrap items-center gap-3 px-4 py-3">
        {rendered > 0 && (
          <span className="text-xs text-foreground-500">
            还有 {rendered} 镜未审，交付时自动视为通过。
          </span>
        )}
        {staleCount > 0 && (
          <span className="text-xs text-rose-600">
            有 {staleCount} 个旧版镜头 / {segments.filter((segment) => segment.stale).length} 个旧版段，需重新生成对应段。
          </span>
        )}
        {rejected > 0 && (
          <span className="text-xs text-rose-600">还有 {rejected} 镜待返修，不能交付。</span>
        )}
        {finalStale && <span className="text-xs text-rose-600">集成片过旧，不能交付。</span>}
        {segments.length > 0 && segments.some((segment) => segment.stale) && (
          <span className="text-xs text-rose-600">还有段未生成或已过旧，交付前需全部就绪。</span>
        )}
        <div className="ml-auto flex gap-2">
          <button type="button" className="btn-ghost" onClick={onGoShots}>
            回分镜
          </button>
          <button type="button" className="btn-primary" disabled={!canDeliver} onClick={onGoDeliver}>
            去交付
          </button>
        </div>
      </section>

      {rejectTarget && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 px-4">
          <div className="panel w-full max-w-md space-y-3 p-5">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium text-foreground-900">
                返修第 {rejectTarget.shot_no} 镜
              </h3>
              <button
                type="button"
                className="rounded-lg p-1 text-foreground-500 hover:bg-background-200"
                onClick={() => setRejectTarget(null)}
              >
                <X size={15} />
              </button>
            </div>

            <textarea
              className="input min-h-[96px] resize-y text-xs"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="必填：说明要改什么（会写入重采样提示词）"
            />

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="btn-ghost !px-2 !py-1 text-xs"
                onClick={() => void expandBrief()}
                disabled={briefBusy || !reason.trim()}
                title="把一句话意见扩写成可执行的画面要求"
              >
                <Sparkles size={12} />
                {briefBusy ? "扩写中…" : "AI 扩写意见"}
              </button>
              <span className="text-[10px] text-foreground-400">
                先写个大概，AI 会补上保留与避免的点
              </span>
            </div>

            {briefCandidate && (
              <div className="space-y-2 rounded-lg bg-background-100 p-2.5">
                <p className="text-[11px] text-foreground-500">候选（{briefCandidate.label}）</p>
                <p className="whitespace-pre-wrap text-xs leading-relaxed text-foreground-800">
                  {briefCandidate.brief}
                </p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    className="btn-primary !px-2 !py-1 text-xs"
                    onClick={() => {
                      setReason(briefCandidate.brief);
                      setBriefCandidate(null);
                    }}
                  >
                    采用
                  </button>
                  <button
                    type="button"
                    className="btn-ghost !px-2 !py-1 text-xs"
                    onClick={() => setBriefCandidate(null)}
                  >
                    放弃
                  </button>
                </div>
              </div>
            )}

            <label className="flex items-start gap-2 text-xs text-foreground-600">
              <input type="checkbox" checked disabled className="mt-0.5" />
              图锚保持首尾连贯（L1）— 自动使用相邻镜头首尾帧作参考
            </label>
            <label className="flex items-start gap-2 text-xs text-foreground-600">
              <input
                type="checkbox"
                checked={rerun}
                onChange={(event) => setRerun(event.target.checked)}
                className="mt-0.5"
              />
              立即重新生成（<Clock3 size={11} className="inline" /> 约{" "}
              {Math.max(1, Math.ceil(rejectTarget.duration_s / 5)) * costPer5s} 积分）
            </label>
            {!rerun && (
              <p className="rounded-lg bg-background-200 px-3 py-2 text-[11px] text-foreground-600">
                仅标记待返修、不生成；稍后可在行内点「重新返修」再提交。
              </p>
            )}

            <div className="flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={() => setRejectTarget(null)}>
                取消
              </button>
              <button
                type="button"
                className="btn-primary"
                disabled={rejectBusy}
                onClick={() => void submitReject()}
              >
                {rejectBusy ? "提交中…" : rerun ? "提交返修" : "标记待返修"}
              </button>
            </div>
          </div>
        </div>
      )}

      {segmentVersionsFor && (
        <VersionDrawer
          episodeId={eid}
          segment={segmentVersionsFor}
          onClose={() => setSegmentVersionsFor(null)}
          onRolledBack={async () => {
            setSegmentVersionsFor(null);
            await load();
            await onChanged();
            notify("段成片已回滚，并重新拼接集成片（0 成本）");
          }}
          notify={notify}
        />
      )}

      {versionsFor && (
        <VersionDrawer
          episodeId={eid}
          shot={versionsFor}
          onClose={() => setVersionsFor(null)}
          onRolledBack={async () => {
            setVersionsFor(null);
            await load();
            await onChanged();
            notify("已回滚到所选版本，并重新拼接成片（0 成本）");
          }}
          notify={notify}
        />
      )}
    </div>
  );
}

function VersionDrawer({
  episodeId,
  shot,
  segment,
  notify,
  onClose,
  onRolledBack,
}: {
  episodeId: number;
  shot?: DramaShot;
  segment?: DramaSegment;
  notify: (message: string) => void;
  onClose: () => void;
  onRolledBack: () => void | Promise<void>;
}) {
  const [versions, setVersions] = useState<DramaArtifactVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const load = segment
      ? api.dramaSegmentArtifacts(segment.id).then((result) => result.artifacts)
      : shot
        ? api.dramaShotArtifacts(shot.id).then((result) => result.artifacts)
        : Promise.resolve([] as DramaArtifactVersion[]);
    load
      .then((rows) => {
        if (alive) setVersions(rows);
      })
      .catch((exc) => notify(errText(exc)))
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [shot?.id, segment?.id, notify]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const rollback = async (artifact: DramaArtifactVersion) => {
    const label = segment ? `第 ${segment.segment_no} 段` : `第 ${shot?.shot_no ?? "-"} 镜`;
    if (!window.confirm(`把${label}回滚到 v${artifact.version_no}？将重新拼接，0 成本。`)) return;
    setBusyId(artifact.id);
    try {
      await api.dramaRollback(episodeId, artifact.id);
      await onRolledBack();
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/40" onClick={onClose}>
      <div
        className="h-full w-full max-w-sm overflow-y-auto bg-background-50 p-4 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-medium text-foreground-900">
            {segment
              ? `第 ${segment.segment_no} 段 · 段成片历史`
              : `第 ${shot?.shot_no ?? "-"} 镜 · 历史版本`}
          </h3>
          <button
            type="button"
            className="rounded-lg p-1 text-foreground-500 hover:bg-background-200"
            onClick={onClose}
          >
            <X size={15} />
          </button>
        </div>

        {loading ? (
          <p className="flex items-center gap-2 py-6 text-xs text-foreground-500">
            <Loader2 size={14} className="animate-spin" /> 加载版本…
          </p>
        ) : versions.length === 0 ? (
          <p className="py-6 text-xs text-foreground-500">还没有历史版本。</p>
        ) : (
          <ul className="space-y-2">
            {versions.map((version) => (
              <li
                key={version.id}
                className={`rounded-xl border p-2.5 text-xs ${
                  version.is_current
                    ? "border-primary-300 bg-primary-50"
                    : "border-background-200 bg-background-100"
                }`}
              >
                <div className="flex items-center gap-1.5">
                  <span className="font-medium text-foreground-900">v{version.version_no}</span>
                  {version.is_current && (
                    <span className="rounded bg-primary-100 px-1 text-[10px] text-primary-700">
                      当前
                    </span>
                  )}
                  {version.stale && (
                    <span className="rounded bg-background-200 px-1 text-[10px] text-foreground-500">
                      旧版
                    </span>
                  )}
                  <span className="ml-auto text-[10px] text-foreground-400">
                    {fmtSecs(version.duration)} · {version.frames} 帧
                  </span>
                </div>
                <p className="mt-1 text-[10px] text-foreground-400">{fmtTime(version.created_at)}</p>
                <div className="mt-1.5 flex items-center gap-1.5">
                  {version.url && (
                    <a
                      className="btn-ghost px-2 py-0.5 text-[11px]"
                      href={fileUrl(version.url)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <Download size={11} /> 查看
                    </a>
                  )}
                  {!version.is_current && (
                    <button
                      type="button"
                      className="btn-ghost px-2 py-0.5 text-[11px]"
                      disabled={busyId === version.id}
                      onClick={() => void rollback(version)}
                    >
                      {busyId === version.id ? "回滚中…" : "回滚到此版本"}
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-[10px] leading-relaxed text-foreground-500">
          回滚只切换当前指针并重新拼接成片，不重新生成、不扣积分。
        </p>
      </div>
    </div>
  );
}
