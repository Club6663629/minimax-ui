/** S3 分镜：表格行内编辑、AI 规划、帧数吸附预览与保存。 */
import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Plus,
  Save,
  Sparkles,
  Trash2,
  Wand2,
} from "lucide-react";
import { api } from "../../api/client";
import type { DramaCharacter, DramaEpisode, DramaScene, DramaShotDraft } from "../../types";
import { errText, fmtSecs, shotDraftFromShot } from "./labels";

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
  notify,
  onSaved,
  onNext,
}: {
  episode: DramaEpisode;
  characters: DramaCharacter[];
  scenes: DramaScene[];
  maxShots: number;
  maxSeconds: number;
  minShot?: number;
  maxShot?: number;
  notify: (message: string) => void;
  onSaved: () => void | Promise<void>;
  onNext: () => void;
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
                return (
                  <FragmentRow
                    key={index}
                    draft={draft}
                    index={index}
                    open={open}
                    issues={issues}
                    frames={frames}
                    characters={characters}
                    scenes={scenes}
                    minShot={minShot}
                    maxShot={maxShot}
                    onToggleExpand={() => setExpanded(open ? null : index)}
                    onPatch={(next) => patch(index, next)}
                    onToggleCharacter={(cid) => toggleCharacter(index, cid)}
                    onRemove={() => removeRow(index)}
                  />
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
  minShot: number;
  maxShot: number;
  onToggleExpand: () => void;
  onPatch: (next: Partial<DramaShotDraft>) => void;
  onToggleCharacter: (cid: number) => void;
  onRemove: () => void;
}) {
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
          </td>
        </tr>
      )}
    </>
  );
}
