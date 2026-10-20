/** 短剧工作台：七阶段编排、活跃 run 轮询与阶段跳转。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import CastStage from "../components/drama/CastStage";
import ChecklistStage from "../components/drama/ChecklistStage";
import CostBar from "../components/drama/CostBar";
import DeliverStage from "../components/drama/DeliverStage";
import RenderStage from "../components/drama/RenderStage";
import ReviewStage from "../components/drama/ReviewStage";
import RunBadge from "../components/drama/RunBadge";
import ScriptStage from "../components/drama/ScriptStage";
import ShotStage from "../components/drama/ShotStage";
import StageNav, { type DramaStageKey, stageIndex } from "../components/drama/StageNav";
import Toast, { useToast } from "../components/drama/Toast";
import {
  EPISODE_STATUS_LABEL,
  EPISODE_STATUS_TONE,
  RUN_PHASE_LABEL,
  errText,
  fmtTime,
} from "../components/drama/labels";
import type {
  DramaChecklist,
  DramaEpisodeDetail,
  DramaRun,
  DramaSegmentDraft,
  DramaStatus,
} from "../types";

function stageFromStatus(status: string): DramaStageKey {
  if (status === "draft") return "script";
  if (status === "script_ready") return "cast";
  if (status === "cast_ready") return "shots";
  if (status === "shots_ready") return "checklist";
  if (status === "rendering") return "render";
  if (status === "review") return "review";
  return "deliver";
}

const TERMINAL = new Set(["done", "failed", "cancelled"]);

export default function DramaWorkspacePage() {
  const { pid, eid } = useParams();
  const projectId = Number(pid);
  const episodeId = Number(eid);
  const toast = useToast();
  const notify = toast.show;

  const [detail, setDetail] = useState<DramaEpisodeDetail | null>(null);
  const [status, setStatus] = useState<DramaStatus | null>(null);
  const [checklist, setChecklist] = useState<DramaChecklist | null>(null);
  const [checklistLoading, setChecklistLoading] = useState(false);
  const [stage, setStage] = useState<DramaStageKey>("script");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [run, setRun] = useState<DramaRun | null>(null);
  const [runId, setRunId] = useState<number | null>(null);
  const [runKind, setRunKind] = useState<"segment" | "full" | "patch" | null>(null);
  const [scriptDirty, setScriptDirty] = useState(false);

  const loadDetail = useCallback(async () => {
    const data = await api.dramaEpisode(episodeId);
    setDetail(data);
    return data;
  }, [episodeId]);

  const refreshChecklist = useCallback(
    async (quiet = false) => {
      setChecklistLoading(true);
      try {
        const data = await api.dramaChecklist(episodeId);
        setChecklist(data);
      } catch (exc) {
        if (!quiet) notify(errText(exc));
      } finally {
        setChecklistLoading(false);
      }
    },
    [episodeId, notify],
  );

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true);
      setError("");
      try {
        const [data, remoteStatus] = await Promise.all([
          api.dramaEpisode(episodeId),
          api.dramaStatus().catch(() => null),
        ]);
        if (!alive) return;
        setDetail(data);
        setStatus(remoteStatus);
        setStage(stageFromStatus(data.episode.status));
        void refreshChecklist(true);
        if (data.episode.active_run) {
          const activeRun = await api.dramaRun(data.episode.active_run.id);
          if (!alive) return;
          setRun(activeRun);
          setRunId(activeRun.id);
          setRunKind(activeRun.mode);
          setStage(activeRun.mode === "patch" ? "review" : "render");
        }
      } catch (exc) {
        if (alive) setError(errText(exc));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [episodeId, refreshChecklist]);

  const applyTerminal = useCallback(
    async (next: DramaRun) => {
      setRunId(null);
      let latest: DramaEpisodeDetail | null = null;
      try {
        latest = await loadDetail();
      } catch {
        /* 刷新失败时保留轮询结果，用户可手动刷新 */
      }
      void refreshChecklist(true);
      if (next.mode === "patch") {
        setRunKind(null);
        setStage("review");
        if (next.status === "done") {
          setRun(null);
          notify("返修完成，已重新拼接段成片与集成片（0 额外拼接成本）");
        } else {
          setRun(next);
          notify("返修失败，积分已退还，镜头状态已恢复");
        }
        return;
      }
      if (next.status === "done") {
        const chained = latest?.episode.active_run ?? null;
        if (chained) {
          try {
            const nextRun = await api.dramaRun(chained.id);
            setRun(nextRun);
            setRunId(nextRun.id);
            setRunKind(nextRun.mode);
            setStage("render");
          } catch {
            setRun(null);
          }
          notify(
            next.segment_no
              ? `第 ${next.segment_no} 段完成，已自动提交下一段`
              : "本段完成，已自动提交下一段",
          );
          return;
        }
        setRun(null);
        setRunKind(null);
        setStage(latest?.episode.status === "review" ? "review" : "render");
        notify(
          next.segment_no
            ? `第 ${next.segment_no} 段完成，已进入审片`
            : "整集生成完成，进入审片",
        );
        return;
      }
      setRun(next);
      setStage("render");
      notify(next.status === "cancelled" ? "生成已取消" : "生成失败，积分已退还");
    },
    [loadDetail, notify, refreshChecklist],
  );

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let timer = 0;

    const tick = async () => {
      if (document.visibilityState === "hidden") return;
      try {
        const next = await api.dramaRun(runId);
        if (cancelled) return;
        setRun(next);
        if (TERMINAL.has(next.phase)) {
          window.clearInterval(timer);
          await applyTerminal(next);
        }
      } catch (exc) {
        if (!cancelled) notify(errText(exc));
      }
    };

    void tick();
    timer = window.setInterval(tick, 2500);
    const onVisible = () => {
      if (document.visibilityState === "visible") void tick();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [runId, applyTerminal, notify]);

  const episode = detail?.episode;
  const characters = detail?.characters || [];
  const scenes = detail?.scenes || [];

  const mains = characters.filter((character) => character.role === "main");
  const g2Ready = mains.length > 0 && mains.every((c) => c.card_upload_id && c.locked);
  const targetSeconds = (episode?.beats || []).reduce(
    (sum, beat) => sum + (Number(beat.target_seconds) || 0),
    0,
  );

  const segmentsReady =
    Boolean(episode?.segments.length) &&
    (episode?.segments || []).every((segment) => !segment.stale);

  const canDeliver = useMemo(() => {
    if (!episode) return false;
    return (
      Boolean(episode.final_url) &&
      !episode.final_stale &&
      episode.stats.rejected === 0 &&
      episode.stats.stale === 0 &&
      segmentsReady &&
      episode.shots.every((shot) => Boolean(shot.clip_url))
    );
  }, [episode, segmentsReady]);

  const maxUnlocked = useMemo(() => {
    if (!episode) return 0;
    let max = 0;
    if (episode.beats.length > 0) max = 1;
    if (g2Ready) max = 2;
    if (episode.shots.length > 0) max = 3;
    if (checklist?.ready) max = 4;
    if (episode.status === "rendering") {
      // 段顺序生成：先完成的段可以直接审，返修也回审片
      max = runKind === "patch" || episode.stats.segments_ready > 0 ? 5 : 4;
    }
    if (episode.status === "review" || episode.status === "done") max = 5;
    if (episode.status === "done" || (episode.status === "review" && canDeliver)) max = 6;
    return max;
  }, [episode, g2Ready, checklist, runKind, canDeliver]);

  const reasons: Partial<Record<DramaStageKey, string>> = {
    cast: episode?.beats.length ? "" : "需先生成剧本",
    shots: g2Ready ? "" : "需先锁定主要角色",
    checklist: episode?.shots.length ? "" : "需先保存分镜",
    render: checklist?.ready ? "" : "需先通过就绪检查",
    review: episode?.final_url ? "" : "需先完成第一段生成",
    deliver: canDeliver ? "" : "需所有段就绪且集成片非旧版",
  };

  const jump = (target: DramaStageKey) => {
    if (stageIndex(target) > maxUnlocked) return;
    if (scriptDirty && target !== "script") {
      if (!window.confirm("剧本还有未保存修改，离开将丢失。确认离开？")) return;
      setScriptDirty(false);
    }
    setStage(target);
  };

  const reloadAll = async () => {
    try {
      await loadDetail();
      await refreshChecklist(true);
    } catch (exc) {
      notify(errText(exc));
    }
  };

  const openActiveRun = async () => {
    const active = detail?.episode.active_run;
    if (!active) {
      notify("当前没有进行中的生成");
      return;
    }
    try {
      const next = await api.dramaRun(active.id);
      setRun(next);
      setRunId(next.id);
      setRunKind(next.mode);
      setStage(next.mode === "patch" ? "review" : "render");
    } catch (exc) {
      notify(errText(exc));
    }
  };

  const render = async (force: boolean) => {
    try {
      const result = await api.dramaRender(episodeId, force);
      if (!result.run) {
        notify("所有段都已就绪，本次 0 积分");
        const data = await loadDetail();
        void refreshChecklist(true);
        setRun(null);
        setRunKind(null);
        setStage(
          data.episode.segments.length > 0 &&
            data.episode.segments.every((segment) => !segment.stale)
            ? "review"
            : "render",
        );
        return;
      }
      if (result.reused) {
        notify(
          result.segment_no
            ? `第 ${result.segment_no} 段命中复用，本次 0 积分`
            : "命中复用，本次 0 积分",
        );
        await loadDetail();
        void refreshChecklist(true);
        return;
      }
      setRun(result.run);
      setRunId(result.run.id);
      setRunKind(result.run.mode);
      setStage("render");
      notify(
        result.segment_no
          ? `第 ${result.segment_no} 段已提交，预估 ${result.cost} 积分${
              result.remaining > 0 ? `，后续还有 ${result.remaining} 段` : ""
            }`
          : `整集已提交，预估 ${result.cost} 积分`,
      );
      void loadDetail();
    } catch (exc) {
      notify(errText(exc));
    }
  };

  const renderSegment = async (segmentId: number, force: boolean) => {
    try {
      const result = await api.dramaRenderSegment(segmentId, { force });
      if (result.reused) {
        notify(`第 ${result.segment_no} 段命中复用，本次 0 积分`);
        await loadDetail();
        void refreshChecklist(true);
        return;
      }
      if (!result.run) {
        notify(`第 ${result.segment_no} 段已就绪`);
        await loadDetail();
        return;
      }
      setRun(result.run);
      setRunId(result.run.id);
      setRunKind("segment");
      setStage("render");
      notify(`第 ${result.segment_no} 段已提交，预估 ${result.cost} 积分`);
      void loadDetail();
    } catch (exc) {
      notify(errText(exc));
    }
  };

  /** 失败重试：单镜返修回审片重驳，段失败只重跑该段，集级失败按缺口段继续。 */
  const retryRun = async () => {
    if (run?.mode === "patch") {
      notify("返修失败：到审片页重新驳回该镜即可重试（积分已退还）");
      setStage("review");
      return;
    }
    if (run?.segment_id) {
      await renderSegment(run.segment_id, true);
      return;
    }
    await render(true);
  };

  const planSegments = async (force: boolean) => {
    try {
      const data = await api.dramaPlanSegments(episodeId, { force });
      setDetail((current) => (current ? { ...current, episode: data } : current));
      await loadDetail();
      void refreshChecklist(true);
      notify(`已按 ${data.segments.length} 段排版`);
    } catch (exc) {
      notify(errText(exc));
    }
  };

  const saveSegments = async (items: DramaSegmentDraft[]) => {
    try {
      const data = await api.dramaSaveSegments(episodeId, items);
      setDetail((current) => (current ? { ...current, episode: data } : current));
      await loadDetail();
      void refreshChecklist(true);
      notify(`已保存 ${data.segments.length} 段`);
    } catch (exc) {
      notify(errText(exc));
    }
  };

  const cancelRun = async () => {
    if (!runId) return;
    try {
      const result = await api.dramaCancelRun(runId);
      setRun(result.run);
      if (TERMINAL.has(result.run.phase)) await applyTerminal(result.run);
      else notify("已请求取消");
    } catch (exc) {
      notify(errText(exc));
    }
  };

  const repairRun = async () => {
    if (!runId) return;
    try {
      const result = await api.dramaRepairRun(runId);
      setRun(result.run);
      if (TERMINAL.has(result.run.phase)) await applyTerminal(result.run);
      else notify("正在重新后处理…");
    } catch (exc) {
      notify(errText(exc));
    }
  };

  const patchStarted = (next: DramaRun) => {
    setRun(next);
    setRunId(next.id);
    setRunKind("patch");
    setStage("review");
    void loadDetail();
  };

  /** 从审片直接重生成某段：留在审片页看进度徽标，不打断对比。 */
  const segmentStarted = (next: DramaRun) => {
    setRun(next);
    setRunId(next.id);
    setRunKind("segment");
    void loadDetail();
  };

  if (loading) {
    return <div className="panel px-6 py-16 text-center text-sm text-foreground-500">加载中…</div>;
  }

  if (!detail || !episode) {
    return (
      <div className="panel space-y-3 px-6 py-12 text-center">
        <p className="text-sm text-rose-600">{error || "剧集不存在"}</p>
        <Link to={`/drama/${projectId}`} className="btn-ghost">
          返回项目
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-xs text-foreground-500">
        <Link to="/drama" className="inline-flex items-center gap-1 hover:text-foreground-800">
          <ArrowLeft size={13} /> 短剧列表
        </Link>
        <span>/</span>
        <Link to={`/drama/${detail.project.id}`} className="hover:text-foreground-800">
          {detail.project.title}
        </Link>
        <span>/</span>
        <span className="text-foreground-700">第 {episode.episode_no} 集</span>
      </div>

      <section className="panel flex flex-wrap items-center gap-2 px-4 py-3">
        <div className="min-w-0">
          <h1 className="truncate text-base font-semibold text-foreground-950">
            {detail.project.title} · 第 {episode.episode_no} 集
          </h1>
          <p className="mt-0.5 text-[11px] text-foreground-500">
            {episode.title} · {detail.project.aspect_ratio} · {episode.stats.shots} 镜
            {episode.stats.segments > 0 ? ` · ${episode.stats.segments} 段` : ""} · 目标{" "}
            {episode.stats.total_seconds.toFixed(1)}s
          </p>
        </div>
        <span
          className={`rounded-md px-2 py-0.5 text-xs ${
            EPISODE_STATUS_TONE[episode.status] || "bg-background-200 text-foreground-600"
          }`}
        >
          {EPISODE_STATUS_LABEL[episode.status] || episode.status}
        </span>
        {run && (
          <span className="rounded-md bg-amber-100 px-2 py-0.5 text-xs text-amber-700">
            {RUN_PHASE_LABEL[run.phase] || run.phase} · run #{run.id}
          </span>
        )}
        <span className="ml-auto text-[11px] text-foreground-400">
          更新于 {fmtTime(episode.updated_at)}
        </span>
        <button
          type="button"
          className="rounded-lg p-1 text-foreground-500 transition hover:bg-background-200"
          onClick={() => void reloadAll()}
          title="刷新"
        >
          <RefreshCw size={14} />
        </button>
      </section>

      {status && !status.enabled && (
        <div className="panel border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800">
          短剧功能当前未开启（需要 DRAMA_ENABLED 与 DIRECTOR_ENABLED），本页只读。
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[180px_minmax(0,1fr)_228px]">
        <aside className="space-y-3">
          <StageNav
            current={stage}
            maxUnlocked={maxUnlocked}
            reasons={reasons}
            onJump={jump}
          />
        </aside>

        <main className="min-w-0 space-y-3">
          {stage === "script" && (
            <ScriptStage
              eid={episodeId}
              episode={episode}
              notify={notify}
              onSaved={async () => {
                await loadDetail();
              }}
              onNext={() => {
                setScriptDirty(false);
                setStage("cast");
              }}
              onDirtyChange={setScriptDirty}
            />
          )}

          {stage === "cast" && (
            <CastStage
              projectId={detail.project.id}
              episode={episode}
              characters={characters}
              scenes={scenes}
              notify={notify}
              onChanged={async () => {
                await loadDetail();
              }}
              onNext={() => setStage("shots")}
            />
          )}

          {stage === "shots" && (
            <ShotStage
              episode={episode}
              characters={characters}
              scenes={scenes}
              maxShots={status?.limits.max_shots ?? 20}
              maxSeconds={status?.limits.max_seconds ?? 180}
              minShot={status?.limits.min_shot ?? 2}
              maxShot={status?.limits.max_shot ?? 10}
              segmentMaxShots={status?.limits.segment_max_shots ?? 12}
              segmentMaxSeconds={status?.limits.segment_max_seconds ?? 120}
              notify={notify}
              onPlanSegments={planSegments}
              onSaveSegments={saveSegments}
              onRenderSegment={(segmentId) => renderSegment(segmentId, false)}
              onSaved={async () => {
                await loadDetail();
                await refreshChecklist(true);
              }}
              onNext={() => setStage("checklist")}
            />
          )}

          {stage === "checklist" && (
            <ChecklistStage
              episode={episode}
              checklist={checklist}
              loading={checklistLoading}
              targetSeconds={targetSeconds}
              onRefresh={() => void refreshChecklist()}
              onRender={render}
              onViewRun={() => void openActiveRun()}
              onJumpShot={() => setStage("shots")}
              onJumpCast={() => setStage("cast")}
              notify={notify}
            />
          )}

          {stage === "render" && (
            <>
              {!run && episode.last_error && (
                <div className="panel border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700">
                  上次失败：{episode.last_error}
                </div>
              )}
              <RenderStage
                episode={episode}
                run={run}
                onCancel={cancelRun}
                onRepair={repairRun}
                onRetry={retryRun}
                onReuse={() => render(false)}
                onRenderMissing={() => render(false)}
                onRenderSegment={renderSegment}
                onGoReady={() => setStage("checklist")}
                onGoShots={() => setStage("shots")}
                notify={notify}
              />
            </>
          )}

          {stage === "review" && (
            <ReviewStage
              eid={episodeId}
              episode={episode}
              costPer5s={status?.pricing.cost_768p_5s ?? 10}
              activePatchRun={run && !TERMINAL.has(run.phase) ? run : null}
              notify={notify}
              onPatchStarted={patchStarted}
              onSegmentStarted={segmentStarted}
              onChanged={async () => {
                await loadDetail();
              }}
              onGoShots={() => setStage("shots")}
              onGoDeliver={() => {
                if (!canDeliver) {
                  notify("还有未通过或旧片镜头，暂不能交付");
                  return;
                }
                setStage("deliver");
              }}
            />
          )}

          {stage === "deliver" && (
            <DeliverStage
              episode={episode}
              notify={notify}
              onFinish={async () => {
                await api.dramaFinish(episodeId);
                await loadDetail();
              }}
              onReopen={async () => {
                await api.dramaReopen(episodeId);
                await loadDetail();
                setStage("shots");
              }}
              onGoReview={() => setStage("review")}
            />
          )}
        </main>

        <aside className="space-y-3">
          <CostBar
            checklist={checklist}
            targetSeconds={targetSeconds}
            loading={checklistLoading}
            onRefresh={() => void refreshChecklist()}
          />
          <RunBadge
            run={run}
            label={
              run?.mode === "patch"
                ? "返修批次"
                : run?.mode === "segment" && run.segment_no
                  ? `第 ${run.segment_no} 段批次`
                  : "整集批次"
            }
          />
          <div className="panel space-y-1.5 p-3 text-[11px] text-foreground-500">
            <p className="text-foreground-700">阶段提示</p>
            <p>· 一集按段生成：每段 30–120s，完成后自动续下一段，可边生成边审已完成段。</p>
            <p>· 改剧本/角色/分镜会让受影响的段与旧成片标为「旧版」，需重生对应段。</p>
            <p>· 返修/回滚 0 成本重拼段成片与集成片；段失败只退该段积分。</p>
            <p>· 交付后仍可「重新编辑」，旧成片保留可看。</p>
          </div>
        </aside>
      </div>

      <Toast message={toast.message} visible={toast.visible} />
    </div>
  );
}
