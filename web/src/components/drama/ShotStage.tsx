/** S3 分镜：表格行内编辑、AI 规划、帧数吸附预览、按段分组与保存。 */
import { Fragment, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Layers,
  Merge,
  PlayCircle,
  Plus,
  Save,
  Sparkles,
  Trash2,
  Wand2,
} from "lucide-react";
import { api, fileUrl } from "../../api/client";
import type {
  DramaAiShotRewrite,
  DramaCharacter,
  DramaEpisode,
  DramaScene,
  DramaSegment,
  DramaSegmentDraft,
  DramaShot,
  DramaShotDraft,
} from "../../types";
import {
  SEGMENT_ENTRY_LABEL,
  SEGMENT_STATUS_LABEL,
  SEGMENT_STATUS_TONE,
  errText,
  fmtSecs,
  shotDraftFromShot,
} from "./labels";
import SegmentCards from "./SegmentCards";

const FPS = 24;
const OVERLAP = 39;

const SHOT_SIZES = ["远景", "全景", "中景", "近景", "特写"];
const MOVEMENTS = ["固定", "推", "拉", "摇", "移", "跟", "升降", "环绕"];

function alignFrames(requested: number): number {
  const steps = Math.max(0, Math.round((Math.max(5, requested) - 5) / 17));
  return Math.min(5 + 17 * steps, 3592);
}

/** 与后端 plan_director_segments 一致：第 2 镜起先加 39 帧交叠再吸附。 */
export function segmentFrames(duration: number, first: boolean): number {
  return alignFrames(Math.round(duration * FPS) + (first ? 0 : OVERLAP));
}

/** 逐镜可见帧与整集总帧（end_frame 为开区间，末段终点即总帧数）。 */
export function planPreview(durations: number[]): { totalFrames: number; visible: number[] } {
  let cursor = 0;
  const visible: number[] = [];
  durations.forEach((duration, index) => {
    const frames = segmentFrames(duration, index === 0);
    const start = index === 0 ? 0 : cursor - OVERLAP;
    const end = start + frames;
    visible.push(end - start - (index === 0 ? 0 : OVERLAP));
    cursor = end;
  });
  return { totalFrames: cursor, visible };
}

function emptyDraft(index: number, scenes: DramaScene[]): DramaShotDraft {
  return {
    shot_no: index + 1,
    scene_no: scenes[0]?.scene_no || 1,
    beat_id: "",
    prompt: "",
    duration_s: 5,
    shot_size: "中景",
    camera: "",
    movement: "固定",
    character_ids: [],
    ref_upload_ids: [],
    continuity_note: "",
    dialogue: "",
    audio_note: "",
  };
}

function rowIssues(
  draft: DramaShotDraft,
  characters: DramaCharacter[],
  scenes: DramaScene[],
  minShot: number,
  maxShot: number,
): string[] {
  const issues: string[] = [];
  if (draft.duration_s < minShot || draft.duration_s > maxShot) {
    issues.push(`时长需在 ${minShot}–${maxShot} 秒`);
  }
  if (draft.prompt.trim().length < 6) issues.push("提示词过短");
  if (!scenes.some((scene) => scene.scene_no === draft.scene_no)) issues.push("场景不存在");
  for (const cid of draft.character_ids) {
    const character = characters.find((item) => item.id === cid);
    if (!character) issues.push("引用了不存在的角色");
    else if (!character.card_upload_id) issues.push(`「${character.name}」还没有定妆图`);
  }
  if (draft.ref_upload_ids.length > 9) issues.push("参考图超过 9 张");
  return issues;
}

export default function ShotStage({
  episode,
  characters,
  scenes,
  maxShots,
  maxSeconds,
  minShot = 2,
  maxShot = 10,
  segmentMaxShots = 12,
  segmentMaxSeconds = 120,
  notify,
  onSaved,
  onNext,
  onPlanSegments,
  onSaveSegments,
  onRenderSegment,
}: {
  episode: DramaEpisode;
  characters: DramaCharacter[];
  scenes: DramaScene[];
  maxShots: number;
  maxSeconds: number;
  minShot?: number;
  maxShot?: number;
  segmentMaxShots?: number;
  segmentMaxSeconds?: number;
  notify: (message: string) => void;
  onSaved: () => void | Promise<void>;
  onNext: () => void;
  onPlanSegments: (force: boolean) => void | Promise<void>;
  onSaveSegments: (segments: DramaSegmentDraft[]) => void | Promise<void>;
  onRenderSegment: (segmentId: number) => void | Promise<void>;
}) {
  const [drafts, setDrafts] = useState<DramaShotDraft[]>(
    episode.shots.map((shot) => shotDraftFromShot(shot)),
  );
  const [expanded, setExpanded] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [source, setSource] = useState("");

  useEffect(() => {
    setDrafts(episode.shots.map((shot) => shotDraftFromShot(shot)));
    setDirty(false);
  }, [episode.id, episode.updated_at]);

  const patch = (index: number, next: Partial<DramaShotDraft>) => {
    setDrafts((rows) => rows.map((row, i) => (i === index ? { ...row, ...next } : row)));
    setDirty(true);
  };

  /** 已保存镜头按镜号索引：AI 改写只能基于库内版本，未保存的新草稿不给入口。 */
  const savedByNo = useMemo(() => {
    const map = new Map<number, DramaShot>();
    episode.shots.forEach((shot) => map.set(shot.shot_no, shot));
    return map;
  }, [episode.shots]);

  const stats = useMemo(() => {
    const preview = planPreview(drafts.map((draft) => draft.duration_s));
    const rawSeconds = drafts.reduce((sum, draft) => sum + draft.duration_s, 0);
    return {
      count: drafts.length,
      rawSeconds,
      totalFrames: preview.totalFrames,
      visible: preview.visible,
      alignedSeconds: preview.totalFrames / FPS,
      ratio: maxSeconds > 0 ? preview.totalFrames / FPS / maxSeconds : 0,
    };
  }, [drafts, maxSeconds]);

  const plan = async (mode: "replace" | "append") => {
    if (mode === "replace" && drafts.length) {
      const ok = window.confirm(`AI 规划会替换当前 ${drafts.length} 个镜头，确认继续？`);
      if (!ok) return;
    }
    setBusy(true);
    try {
      const result = await api.dramaPlanShots(episode.id, mode);
      const planned = result.shots.map((shot, index) => ({ ...shot, shot_no: index + 1 }));
      setDrafts((rows) => {
        const merged = mode === "append" ? [...rows, ...planned] : planned;
        return merged.slice(0, maxShots).map((shot, index) => ({ ...shot, shot_no: index + 1 }));
      });
      setSource(result.source);
      setDirty(true);
      notify(
        result.source === "local"
          ? `已用本地模板规划 ${planned.length} 个镜头，请检查后保存`
          : `AI 已规划 ${planned.length} 个镜头，请检查后保存`,
      );
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  const addRow = () => {
    if (drafts.length >= maxShots) {
      notify(`镜头数上限为 ${maxShots}`);
      return;
    }
    setDrafts((rows) => [...rows, emptyDraft(rows.length, scenes)]);
    setDirty(true);
  };

  const removeRow = (index: number) => {
    setDrafts((rows) =>
      rows.filter((_, i) => i !== index).map((row, i) => ({ ...row, shot_no: i + 1 })),
    );
    setDirty(true);
    if (expanded === index) setExpanded(null);
  };

  const toggleCharacter = (index: number, cid: number) => {
    const draft = drafts[index];
    const ids = draft.character_ids.includes(cid)
      ? draft.character_ids.filter((id) => id !== cid)
      : [...draft.character_ids, cid];
    patch(index, { character_ids: ids });
  };

  const save = async () => {
    if (!drafts.length) {
      notify("请先 AI 规划或新增镜头");
      return;
    }
    const invalid = drafts.findIndex(
      (draft) => rowIssues(draft, characters, scenes, minShot, maxShot).length > 0,
    );
    if (invalid >= 0) {
      setExpanded(invalid);
      notify(`第 ${drafts[invalid].shot_no} 镜还有问题：${rowIssues(drafts[invalid], characters, scenes, minShot, maxShot).join("、")}`);
      return;
    }
    setBusy(true);
    try {
      await api.dramaSaveShots(episode.id, drafts);
      setDirty(false);
      notify("分镜已保存");
      await onSaved();
      onNext();
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  /** 已保存镜头按段分组：仅当草稿与库内镜号一致时展示段头，避免未保存改动错位。 */
  const segmentGroups = useMemo(() => {
    const byNo = new Map<number, DramaSegment>();
    episode.segments.forEach((segment) => byNo.set(segment.segment_no, segment));
    return drafts.map((draft, index) => {
      const saved = episode.shots[index];
      if (!saved || saved.shot_no !== draft.shot_no || saved.segment_no === null) return null;
      return byNo.get(saved.segment_no) || null;
    });
  }, [drafts, episode.segments, episode.shots]);

  const planSegments = async () => {
    if (dirty) {
      notify("分镜有未保存修改，请先保存再重新分段");
      return;
    }
    const force = episode.segments.length > 0;
    if (force && !window.confirm("重新分段会重建段结构，已有段成片会被标为旧版（不删文件）。确认继续？")) {
      return;
    }
    setBusy(true);
    try {
      await onPlanSegments(force);
    } finally {
      setBusy(false);
    }
  };

  /** 合并到上一段：段头操作，保存后已有段成片会因镜头归属变化而标旧。 */
  const mergePrev = async (index: number) => {
    const current = episode.segments[index];
    const prev = episode.segments[index - 1];
    if (!current || !prev) return;
    if (dirty) {
      notify("分镜有未保存修改，请先保存再调整分段");
      return;
    }
    if (
      !window.confirm(
        `把第 ${current.segment_no} 段（${current.shot_count} 镜）并入第 ${prev.segment_no} 段？`,
      )
    ) {
      return;
    }
    const items: DramaSegmentDraft[] = [];
    episode.segments.forEach((segment, i) => {
      if (i === index) return;
      if (i === index - 1) {
        items.push({
          segment_no: items.length + 1,
          title: segment.title,
          shot_ids: [...segment.shot_ids, ...current.shot_ids],
        });
        return;
      }
      items.push({
        segment_no: items.length + 1,
        title: segment.title,
        shot_ids: [...segment.shot_ids],
      });
    });
    setBusy(true);
    try {
      await onSaveSegments(items);
      notify("已合并到上一段");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <section className="panel flex flex-wrap items-center gap-4 px-4 py-3 text-xs">
        <div>
          <span className="text-foreground-500">镜数</span>
          <span className={`ml-1.5 font-medium ${stats.count > maxShots ? "text-rose-600" : "text-foreground-900"}`}>
            {stats.count} / {maxShots}
          </span>
        </div>
        <div>
          <span className="text-foreground-500">原始时长</span>
          <span className="ml-1.5 text-foreground-900">{fmtSecs(stats.rawSeconds)}</span>
        </div>
        <div>
          <span className="text-foreground-500">吸附后</span>
          <span className={`ml-1.5 ${stats.alignedSeconds > maxSeconds ? "text-rose-600" : "text-foreground-900"}`}>
            {fmtSecs(stats.alignedSeconds)} · {stats.totalFrames} 帧
          </span>
        </div>
        <div className="min-w-[140px] flex-1">
          <div className="h-1.5 overflow-hidden rounded-full bg-background-200">
            <div
              className={`h-full rounded-full ${stats.ratio > 1 ? "bg-rose-500" : "bg-primary-500"}`}
              style={{ width: `${Math.min(100, Math.round(stats.ratio * 100))}%` }}
            />
          </div>
          <p className="mt-1 text-[10px] text-foreground-500">
            占本集上限 {maxSeconds}s 的 {Math.round(stats.ratio * 100)}%
          </p>
        </div>
        <span className="text-[11px] text-foreground-500">
          {dirty ? "有未保存修改" : "已同步"}
          {source === "local" && " · 本地模板规划"}
        </span>
      </section>

      <section className="panel space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="flex items-center gap-1.5 text-sm font-medium text-foreground-900">
            <Layers size={15} /> 分段
          </h2>
          <span className="text-xs text-foreground-500">
            {episode.segments.length} 段 · 每段 ≤{segmentMaxShots} 镜 / ≤{segmentMaxSeconds}s；跨场硬切，同场承接
          </span>
          <button
            type="button"
            className="btn-ghost ml-auto px-2 py-1 text-xs"
            disabled={busy || !episode.shots.length}
            onClick={() => void planSegments()}
          >
            {episode.segments.length ? "重新分段" : "自动分段"}
          </button>
        </div>
        {dirty && (
          <p className="rounded-lg bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-700">
            分镜有未保存修改：保存后会按场自动重新分段。
          </p>
        )}
        <SegmentCards
          segments={episode.segments}
          renderActions={(item) => {
            const index = episode.segments.findIndex((row) => row.id === item.id);
            return (
              <>
                <button
                  type="button"
                  className="btn-ghost px-2 py-1 text-[11px]"
                  disabled={busy || Boolean(item.active_run) || Boolean(episode.active_run)}
                  onClick={() => void onRenderSegment(item.id)}
                  title={item.stale ? "生成本段" : "已就绪，重新生成会再扣费"}
                >
                  <PlayCircle size={11} /> {item.stale ? "生成本段" : "重生成"}
                </button>
                {index > 0 && (
                  <button
                    type="button"
                    className="btn-ghost px-2 py-1 text-[11px]"
                    disabled={busy || Boolean(item.active_run) || Boolean(episode.active_run)}
                    onClick={() => void mergePrev(index)}
                  >
                    <Merge size={11} /> 并入上一段
                  </button>
                )}
              </>
            );
          }}
        />
      </section>

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn-ghost" onClick={() => plan("replace")} disabled={busy}>
          <Sparkles size={14} /> AI 规划（替换）
        </button>
        <button type="button" className="btn-ghost" onClick={() => plan("append")} disabled={busy}>
          <Wand2 size={14} /> AI 追加规划
        </button>
        <button
          type="button"
          className="btn-ghost"
          onClick={() => {
            setDrafts((rows) => rows.map((row) => ({ ...row, duration_s: 5 })));
            setDirty(true);
          }}
          disabled={busy || !drafts.length}
        >
          全部 5s
        </button>
        <button type="button" className="btn-ghost" onClick={addRow} disabled={busy || drafts.length >= maxShots}>
          <Plus size={14} /> 新增镜头
        </button>
        <span className="ml-auto text-[11px] text-foreground-500">
          时长按 24fps 吸附到 5+17n 帧；第 2 镜起额外带 39 帧交叠，表格显示的是该镜可见帧。
        </span>
      </div>

      {drafts.length === 0 ? (
        <section className="panel flex flex-col items-center gap-3 px-6 py-12 text-center">
          <p className="text-sm text-foreground-700">还没有分镜</p>
          <p className="max-w-md text-xs leading-relaxed text-foreground-500">
            点「AI 规划」按节拍自动拆镜，或「新增镜头」手工填写。每个镜头建议 2–10 秒。
          </p>
        </section>
      ) : (
        <section className="panel overflow-x-auto">
          <table className="w-full min-w-[1080px] text-xs">
            <thead>
              <tr className="border-b border-background-200 text-left text-[11px] text-foreground-500">
                <th className="w-10 px-2 py-2 font-normal">#</th>
                <th className="w-28 px-2 py-2 font-normal">时长</th>
                <th className="w-28 px-2 py-2 font-normal">场景</th>
                <th className="w-20 px-2 py-2 font-normal">景别</th>
                <th className="w-20 px-2 py-2 font-normal">运镜</th>
                <th className="w-44 px-2 py-2 font-normal">角色</th>
                <th className="w-36 px-2 py-2 font-normal">台词</th>
                <th className="px-2 py-2 font-normal">提示词</th>
                <th className="w-24 px-2 py-2 font-normal">校验</th>
                <th className="w-16 px-2 py-2 font-normal" />
              </tr>
            </thead>
            <tbody>
              {drafts.map((draft, index) => {
                const issues = rowIssues(draft, characters, scenes, minShot, maxShot);
                const frames = stats.visible[index] ?? 0;
                const open = expanded === index;
                const group = segmentGroups[index];
                const prevGroup = index > 0 ? segmentGroups[index - 1] : null;
                const showGroup = Boolean(group) && group?.id !== prevGroup?.id;
                return (
                  <Fragment key={index}>
                    {showGroup && group && (
                      <tr className="border-y border-background-200 bg-background-100/80">
                        <td colSpan={10} className="px-2 py-1.5">
                          <span className="flex flex-wrap items-center gap-2 text-[11px]">
                            <span className="font-medium text-foreground-800">
                              第 {group.segment_no} 段
                            </span>
                            <span className={`rounded px-1.5 py-0.5 text-[10px] ${SEGMENT_STATUS_TONE[group.status]}`}>
                              {SEGMENT_STATUS_LABEL[group.status]}
                            </span>
                            {group.stale && (
                              <span className="rounded bg-background-200 px-1.5 py-0.5 text-[10px] text-foreground-500">
                                旧版
                              </span>
                            )}
                            <span className="text-foreground-500">
                              {group.shot_count} 镜 · {fmtSecs(group.duration || group.target_seconds)}
                            </span>
                            <span className="text-foreground-400">
                              {SEGMENT_ENTRY_LABEL[group.entry_mode] || group.entry_mode}
                            </span>
                            {group.cut_url && (
                              <a
                                className="text-primary-600 hover:underline"
                                href={fileUrl(group.cut_url)}
                                target="_blank"
                                rel="noreferrer"
                              >
                                看段成片
                              </a>
                            )}
                          </span>
                        </td>
                      </tr>
                    )}
                    <FragmentRow
                      draft={draft}
                      index={index}
                      open={open}
                      issues={issues}
                      frames={frames}
                      characters={characters}
                      scenes={scenes}
                      saved={savedByNo.get(draft.shot_no)}
                      notify={notify}
                      minShot={minShot}
                      maxShot={maxShot}
                      onToggleExpand={() => setExpanded(open ? null : index)}
                      onPatch={(next) => patch(index, next)}
                      onToggleCharacter={(cid) => toggleCharacter(index, cid)}
                      onRemove={() => removeRow(index)}
                    />
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      <div className="flex items-center justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={save} disabled={busy || !drafts.length}>
          <Save size={14} /> 保存分镜
        </button>
        <button type="button" className="btn-primary" onClick={save} disabled={busy || !drafts.length}>
          保存并进入就绪
        </button>
      </div>
    </div>
  );
}

function FragmentRow({
  draft,
  open,
  issues,
  frames,
  characters,
  scenes,
  saved,
  notify,
  minShot,
  maxShot,
  onToggleExpand,
  onPatch,
  onToggleCharacter,
  onRemove,
}: {
  draft: DramaShotDraft;
  index: number;
  open: boolean;
  issues: string[];
  frames: number;
  characters: DramaCharacter[];
  scenes: DramaScene[];
  saved?: DramaShot;
  notify: (message: string) => void;
  minShot: number;
  maxShot: number;
  onToggleExpand: () => void;
  onPatch: (next: Partial<DramaShotDraft>) => void;
  onToggleCharacter: (cid: number) => void;
  onRemove: () => void;
}) {
  /** 只有草稿与已保存版本一致时才允许 AI 改写，避免拿旧内容生成候选。 */
  const synced = Boolean(
    saved && JSON.stringify(shotDraftFromShot(saved)) === JSON.stringify(draft),
  );

  return (
    <>
      <tr className={`border-b border-background-100 align-top last:border-0 ${issues.length ? "bg-rose-50/30" : ""}`}>
        <td className="px-2 py-2 text-foreground-500">{draft.shot_no}</td>
        <td className="px-2 py-2">
          <div className="flex items-center gap-1">
            <input
              type="number"
              min={minShot}
              max={maxShot}
              step={0.5}
              className="input w-16 px-2 py-1 text-xs"
              value={draft.duration_s}
              onChange={(event) => onPatch({ duration_s: Number(event.target.value) || 5 })}
            />
            <span className="whitespace-nowrap text-[10px] text-foreground-400">{frames}f</span>
          </div>
        </td>
        <td className="px-2 py-2">
          <select
            className="input px-1.5 py-1 text-xs"
            value={draft.scene_no}
            onChange={(event) => onPatch({ scene_no: Number(event.target.value) })}
          >
            {scenes.map((scene) => (
              <option key={scene.id} value={scene.scene_no}>
                {scene.scene_no}. {scene.name}
              </option>
            ))}
          </select>
        </td>
        <td className="px-2 py-2">
          <select
            className="input px-1.5 py-1 text-xs"
            value={draft.shot_size}
            onChange={(event) => onPatch({ shot_size: event.target.value })}
          >
            {SHOT_SIZES.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </td>
        <td className="px-2 py-2">
          <select
            className="input px-1.5 py-1 text-xs"
            value={draft.movement}
            onChange={(event) => onPatch({ movement: event.target.value })}
          >
            {MOVEMENTS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </td>
        <td className="px-2 py-2">
          <div className="flex flex-wrap gap-1">
            {characters.map((character) => {
              const active = draft.character_ids.includes(character.id);
              const noCard = !character.card_upload_id;
              return (
                <button
                  key={character.id}
                  type="button"
                  onClick={() => onToggleCharacter(character.id)}
                  className={`rounded-md px-1.5 py-0.5 text-[10px] transition ${
                    active
                      ? noCard
                        ? "bg-rose-100 text-rose-700"
                        : "bg-primary-100 text-primary-700"
                      : "bg-background-200 text-foreground-500 hover:bg-background-300"
                  }`}
                  title={noCard ? "该角色还没有定妆图" : character.name}
                >
                  {character.name}
                  {noCard && " !"}
                </button>
              );
            })}
          </div>
        </td>
        <td className="px-2 py-2">
          <input
            className="input px-2 py-1 text-xs"
            value={draft.dialogue}
            onChange={(event) => onPatch({ dialogue: event.target.value })}
            placeholder="台词"
          />
        </td>
        <td className="px-2 py-2">
          <button
            type="button"
            className="flex w-full items-start gap-1 text-left text-xs text-foreground-700"
            onClick={onToggleExpand}
            title="展开编辑提示词"
          >
            <span className="mt-0.5 shrink-0 text-foreground-400">
              {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            </span>
            <span className="line-clamp-2">{draft.prompt || "点击填写提示词"}</span>
          </button>
        </td>
        <td className="px-2 py-2">
          {issues.length === 0 ? (
            <span className="text-[10px] text-emerald-600">OK</span>
          ) : (
            <span
              className="inline-flex items-center gap-1 text-[10px] text-rose-600"
              title={issues.join("；")}
            >
              <AlertTriangle size={11} /> {issues.length}
            </span>
          )}
        </td>
        <td className="px-2 py-2">
          <button
            type="button"
            className="rounded-lg p-1 text-foreground-400 hover:bg-rose-50 hover:text-rose-600"
            onClick={onRemove}
            title="删除该镜头"
          >
            <Trash2 size={13} />
          </button>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-background-100 bg-background-50/60">
          <td />
          <td colSpan={9} className="px-2 py-3">
            <div className="space-y-2">
              {saved ? (
                synced ? (
                  <ShotAiPanel
                    shotId={saved.id}
                    notify={notify}
                    onApply={(value) =>
                      onPatch({
                        prompt: value.prompt,
                        continuity_note: value.continuity_note,
                        audio_note: value.audio_note,
                        dialogue: value.dialogue,
                      })
                    }
                  />
                ) : (
                  <p className="rounded-lg bg-background-200/60 px-2.5 py-1.5 text-[11px] text-foreground-500">
                    AI 改写基于已保存的镜头；本行有未保存修改，请先保存分镜。
                  </p>
                )
              ) : (
                <p className="rounded-lg bg-background-200/60 px-2.5 py-1.5 text-[11px] text-foreground-500">
                  新增镜头先保存分镜，之后可用 AI 改写提示词。
                </p>
              )}
              <div className="grid gap-2 md:grid-cols-2">
                <label className="space-y-1 text-[11px] text-foreground-500">
                  <span>提示词（画面、动作、光线；建议 20 字以上）</span>
                  <textarea
                    className="input min-h-[92px] resize-y text-xs"
                    value={draft.prompt}
                    onChange={(event) => onPatch({ prompt: event.target.value })}
                    placeholder="例如：雨夜便利店门口，女主撑伞回头，霓虹在水面反光，中景缓慢推近。"
                  />
                </label>
                <div className="space-y-2">
                  <label className="block space-y-1 text-[11px] text-foreground-500">
                    <span>衔接说明（与上一镜的动作/视线关系）</span>
                    <input
                      className="input text-xs"
                      value={draft.continuity_note}
                      onChange={(event) => onPatch({ continuity_note: event.target.value })}
                      placeholder="承接上一镜的回头动作"
                    />
                  </label>
                  <label className="block space-y-1 text-[11px] text-foreground-500">
                    <span>声音提示</span>
                    <input
                      className="input text-xs"
                      value={draft.audio_note}
                      onChange={(event) => onPatch({ audio_note: event.target.value })}
                      placeholder="雨声、远处电话铃"
                    />
                  </label>
                  <label className="block space-y-1 text-[11px] text-foreground-500">
                    <span>台词</span>
                    <input
                      className="input text-xs"
                      value={draft.dialogue}
                      onChange={(event) => onPatch({ dialogue: event.target.value })}
                      placeholder="角色台词或旁白"
                    />
                  </label>
                </div>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

/** 单镜 AI 改写：只产出候选值，采纳后写回当前草稿，仍需用户保存。 */
function ShotAiPanel({
  shotId,
  notify,
  onApply,
}: {
  shotId: number;
  notify: (message: string) => void;
  onApply: (value: DramaAiShotRewrite) => void;
}) {
  const [mode, setMode] = useState<"polish" | "h3">("polish");
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [candidate, setCandidate] = useState<DramaAiShotRewrite | null>(null);
  const [label, setLabel] = useState("");

  const run = async () => {
    setBusy(true);
    try {
      const result = await api.dramaAiRewriteShot(shotId, {
        mode,
        instruction: instruction.trim(),
      });
      setCandidate(result.shot);
      setLabel(result.cached ? `${result.provider} 缓存` : result.provider || "AI");
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 rounded-xl border border-primary-200 bg-primary-50/40 p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-foreground-600">AI 改写提示词</span>
        <select
          className="input w-32 px-2 py-1 text-xs"
          value={mode}
          onChange={(event) => setMode(event.target.value === "h3" ? "h3" : "polish")}
        >
          <option value="polish">润色（保留原意）</option>
          <option value="h3">H3 结构化重写</option>
        </select>
        <input
          className="input min-w-[180px] flex-1 text-xs"
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
          maxLength={500}
          placeholder="补充要求（可留空），如：雨更大一些，女主要低头"
        />
        <button
          type="button"
          className="btn-primary !px-3 !py-1.5 text-xs"
          onClick={run}
          disabled={busy}
        >
          <Sparkles size={13} />
          {busy ? "改写中…" : "生成候选"}
        </button>
      </div>
      {candidate && (
        <div className="space-y-2 rounded-lg bg-background-100 p-2.5">
          <p className="text-[11px] text-foreground-500">候选（{label}）</p>
          <p className="whitespace-pre-wrap text-xs leading-relaxed text-foreground-800">
            {candidate.prompt}
          </p>
          {(candidate.continuity_note || candidate.audio_note || candidate.dialogue) && (
            <div className="space-y-1 text-[11px] text-foreground-600">
              {candidate.continuity_note && <p>衔接：{candidate.continuity_note}</p>}
              {candidate.audio_note && <p>声音：{candidate.audio_note}</p>}
              {candidate.dialogue && <p>台词：{candidate.dialogue}</p>}
            </div>
          )}
          {candidate.changes.length > 0 && (
            <ul className="list-inside list-disc text-[11px] text-foreground-500">
              {candidate.changes.map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              className="btn-primary !px-3 !py-1.5 text-xs"
              onClick={() => {
                onApply(candidate);
                setCandidate(null);
                notify("已采纳 AI 提示词，记得保存分镜");
              }}
            >
              采纳
            </button>
            <button
              type="button"
              className="btn-ghost !px-3 !py-1.5 text-xs"
              onClick={() => setCandidate(null)}
            >
              放弃
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
