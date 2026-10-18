/** S1 剧本：生成/编辑节拍与剧本正文，保存后进入角色阶段。 */
import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Plus, Sparkles, Trash2, Wand2 } from "lucide-react";
import { api } from "../../api/client";
import type { DramaAiBeatCandidate, DramaBeat, DramaEpisode } from "../../types";
import { errText, fmtSecs } from "./labels";

function splitNames(value: string): string[] {
  return value
    .split(/[、,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function newBeat(index: number): DramaBeat {
  return {
    id: `b${Date.now()}${index}`,
    title: `节拍 ${index + 1}`,
    summary: "",
    mood: "",
    location: "",
    characters: [],
    target_seconds: 5,
    dialogue_hint: "",
  };
}

export default function ScriptStage({
  eid,
  episode,
  notify,
  onSaved,
  onNext,
  onDirtyChange,
}: {
  eid: number;
  episode: DramaEpisode;
  notify: (message: string) => void;
  onSaved: () => void | Promise<void>;
  onNext: () => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [beats, setBeats] = useState<DramaBeat[]>(episode.beats || []);
  const [script, setScript] = useState(episode.script || "");
  const [beatCount, setBeatCount] = useState(Math.max(3, episode.beats?.length || 3));
  const [targetSeconds, setTargetSeconds] = useState(
    Math.max(15, Math.min(180, episode.beats?.reduce((sum, b) => sum + (b.target_seconds || 0), 0) || 30)),
  );
  const [busy, setBusy] = useState(false);
  const [source, setSource] = useState("");
  const [showScript, setShowScript] = useState(false);
  /** AI 重写：展开的节拍 id、正在重写的节拍 id、用户补充要求、候选值 */
  const [aiOpen, setAiOpen] = useState<string | null>(null);
  const [aiBusy, setAiBusy] = useState<string | null>(null);
  const [aiHint, setAiHint] = useState("");
  const [aiCandidate, setAiCandidate] = useState<
    { beatId: string; beat: DramaAiBeatCandidate; label: string } | null
  >(null);
  const dirtyRef = useRef(false);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setBeats(episode.beats || []);
    setScript(episode.script || "");
    setAiOpen(null);
    setAiCandidate(null);
    setAiHint("");
    dirtyRef.current = false;
    setDirty(false);
  }, [episode.id, episode.updated_at]);

  const markDirty = (next: DramaBeat[] | string) => {
    if (Array.isArray(next)) setBeats(next);
    else setScript(next);
    dirtyRef.current = true;
    setDirty(true);
    onDirtyChange?.(true);
  };

  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (!dirtyRef.current) return;
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, []);

  const totalTarget = useMemo(
    () => beats.reduce((sum, beat) => sum + (Number(beat.target_seconds) || 0), 0),
    [beats],
  );

  const patchBeat = (index: number, patch: Partial<DramaBeat>) => {
    const next = beats.map((beat, i) => (i === index ? { ...beat, ...patch } : beat));
    markDirty(next);
  };

  /** 调 AI 重写这一拍；只拿候选值，采纳后才写入本地草稿。 */
  const rewriteBeat = async (index: number) => {
    const beat = beats[index];
    setAiBusy(beat.id);
    try {
      const result = await api.dramaAiRewriteBeat(eid, {
        beat_id: beat.id,
        instruction: aiHint.trim(),
      });
      const label = result.cached
        ? `${result.provider} 缓存`
        : result.provider || "AI";
      setAiCandidate({ beatId: beat.id, beat: result.beat, label });
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setAiBusy(null);
    }
  };

  /** 采纳候选值：只覆盖文本字段，时长与角色名单保持原样。 */
  const adoptBeat = (index: number) => {
    if (!aiCandidate) return;
    const { beat } = aiCandidate;
    patchBeat(index, {
      title: beat.title,
      summary: beat.summary,
      mood: beat.mood,
      location: beat.location,
      dialogue_hint: beat.dialogue_hint,
    });
    setAiCandidate(null);
    setAiHint("");
    notify("已采纳 AI 节拍，记得保存剧本");
  };

  const generate = async () => {
    if (beats.length && !window.confirm(`将覆盖当前 ${beats.length} 条节拍，确认重新生成？`)) {
      return;
    }
    setBusy(true);
    try {
      const result = await api.dramaGenerateScript(eid, {
        beat_count: beatCount,
        target_seconds: targetSeconds,
        regenerate: beats.length > 0,
      });
      setBeats(result.beats);
      setScript(result.script);
      setSource(result.source);
      dirtyRef.current = false;
      setDirty(false);
      onDirtyChange?.(false);
      notify(result.source === "local" ? "已用本地模板生成剧本，可手动修改" : "剧本已生成");
      await onSaved();
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  const save = async (): Promise<void> => {
    if (!beats.length) {
      notify("请先生成或新增至少一条节拍");
      return;
    }
    setBusy(true);
    try {
      await api.dramaSaveScript(eid, { beats, script });
      dirtyRef.current = false;
      setDirty(false);
      onDirtyChange?.(false);
      notify("剧本已保存");
      await onSaved();
    } catch (exc) {
      notify(errText(exc));
      throw exc;
    } finally {
      setBusy(false);
    }
  };

  const saveAndNext = async () => {
    if (dirty) {
      try {
        await save();
      } catch {
        return;
      }
    }
    onNext();
  };

  return (
    <div className="space-y-4">
      <section className="panel space-y-3 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-[180px] flex-1">
            <p className="text-xs text-foreground-500">本集梗概（来自立项）</p>
            <p className="mt-1 text-sm leading-relaxed text-foreground-800">
              {episode.brief || "尚未填写梗概"}
            </p>
          </div>
          <label className="w-32 text-xs text-foreground-600">
            <span className="mb-1 block">目标时长（秒）</span>
            <input
              type="number"
              min={15}
              max={180}
              className="input"
              value={targetSeconds}
              onChange={(event) => setTargetSeconds(Number(event.target.value) || 15)}
            />
          </label>
          <label className="w-28 text-xs text-foreground-600">
            <span className="mb-1 block">节拍数</span>
            <input
              type="number"
              min={3}
              max={12}
              className="input"
              value={beatCount}
              onChange={(event) => setBeatCount(Number(event.target.value) || 3)}
            />
          </label>
          <button type="button" className="btn-primary" onClick={generate} disabled={busy}>
            <Sparkles size={15} />
            {busy ? "生成中…" : beats.length ? "重新生成剧本" : "生成剧本"}
          </button>
        </div>
        <p className="text-[11px] text-foreground-500">
          当前节拍目标合计 {fmtSecs(totalTarget)}，导演台会按 24fps 吸附到合法帧数。
          {source === "local" && " 本次使用本地模板，可继续手动调整。"}
        </p>
      </section>

      {beats.length === 0 ? (
        <section className="panel flex flex-col items-center gap-3 px-6 py-12 text-center">
          <p className="text-sm text-foreground-700">还没有节拍</p>
          <p className="max-w-md text-xs leading-relaxed text-foreground-500">
            先点「生成剧本」让系统按梗概拆出 3–12 条节拍，或手动「新增节拍」。每条节拍会成为分镜规划的基本单位。
          </p>
        </section>
      ) : (
        <section className="grid gap-3 md:grid-cols-2">
          {beats.map((beat, index) => (
            <article key={beat.id || index} className="panel space-y-2.5 p-4">
              <div className="flex items-start gap-2">
                <span className="mt-1.5 rounded-md bg-background-200 px-1.5 py-0.5 text-[10px] text-foreground-600">
                  #{index + 1}
                </span>
                <input
                  className="input flex-1 font-medium"
                  value={beat.title}
                  onChange={(event) => patchBeat(index, { title: event.target.value })}
                  placeholder="节拍标题"
                />
                <button
                  type="button"
                  className="mt-1 rounded-lg p-1 text-foreground-400 hover:bg-primary-50 hover:text-primary-600"
                  title="AI 重写这一拍"
                  onClick={() => {
                    setAiHint("");
                    setAiCandidate(null);
                    setAiOpen((prev) => (prev === beat.id ? null : beat.id));
                  }}
                >
                  <Wand2 size={14} />
                </button>
                <button
                  type="button"
                  className="mt-1 rounded-lg p-1 text-foreground-400 hover:bg-rose-50 hover:text-rose-600"
                  title="删除节拍"
                  onClick={() => markDirty(beats.filter((_, i) => i !== index))}
                >
                  <Trash2 size={14} />
                </button>
              </div>

              <textarea
                className="input min-h-[64px] resize-y text-xs"
                value={beat.summary}
                onChange={(event) => patchBeat(index, { summary: event.target.value })}
                placeholder="这一拍发生了什么"
              />

              <div className="grid grid-cols-2 gap-2">
                <input
                  className="input text-xs"
                  value={beat.location}
                  onChange={(event) => patchBeat(index, { location: event.target.value })}
                  placeholder="场景 / 地点"
                />
                <input
                  className="input text-xs"
                  value={beat.mood}
                  onChange={(event) => patchBeat(index, { mood: event.target.value })}
                  placeholder="情绪"
                />
                <input
                  className="input text-xs"
                  value={beat.characters.join("、")}
                  onChange={(event) =>
                    patchBeat(index, { characters: splitNames(event.target.value) })
                  }
                  placeholder="出场角色（顿号分隔）"
                />
                <label className="flex items-center gap-2 text-xs text-foreground-500">
                  目标
                  <input
                    type="number"
                    min={2}
                    max={30}
                    step={0.5}
                    className="input text-xs"
                    value={beat.target_seconds}
                    onChange={(event) =>
                      patchBeat(index, { target_seconds: Number(event.target.value) || 5 })
                    }
                  />
                  秒
                </label>
              </div>

              <input
                className="input text-xs"
                value={beat.dialogue_hint || ""}
                onChange={(event) => patchBeat(index, { dialogue_hint: event.target.value })}
                placeholder="台词提示（可选）"
              />

              {aiOpen === beat.id && (
                <div className="space-y-2 rounded-xl border border-primary-200 bg-primary-50/40 p-2.5">
                  <textarea
                    className="input min-h-[46px] resize-y text-xs"
                    value={aiHint}
                    onChange={(event) => setAiHint(event.target.value)}
                    maxLength={500}
                    placeholder="想怎么改？可留空，AI 会参考前后拍重写（不改时长与角色名单）"
                  />
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      className="btn-primary !px-3 !py-1.5 text-xs"
                      onClick={() => rewriteBeat(index)}
                      disabled={aiBusy === beat.id}
                    >
                      <Sparkles size={13} />
                      {aiBusy === beat.id ? "重写中…" : "生成候选"}
                    </button>
                    <button
                      type="button"
                      className="btn-ghost !px-3 !py-1.5 text-xs"
                      onClick={() => {
                        setAiOpen(null);
                        setAiCandidate(null);
                      }}
                    >
                      收起
                    </button>
                  </div>
                  {aiCandidate?.beatId === beat.id && (
                    <div className="space-y-2 rounded-lg bg-background-100 p-2.5">
                      <p className="text-[11px] text-foreground-500">候选（{aiCandidate.label}）</p>
                      <p className="text-xs font-medium text-foreground-800">
                        {aiCandidate.beat.title}
                        <span className="ml-2 font-normal text-foreground-500">
                          {aiCandidate.beat.mood}
                          {aiCandidate.beat.location ? ` · ${aiCandidate.beat.location}` : ""}
                        </span>
                      </p>
                      <p className="whitespace-pre-wrap text-xs leading-relaxed text-foreground-700">
                        {aiCandidate.beat.summary}
                      </p>
                      {aiCandidate.beat.dialogue_hint && (
                        <p className="text-xs text-foreground-600">
                          台词：{aiCandidate.beat.dialogue_hint}
                        </p>
                      )}
                      <div className="flex gap-2">
                        <button
                          type="button"
                          className="btn-primary !px-3 !py-1.5 text-xs"
                          onClick={() => adoptBeat(index)}
                        >
                          采纳
                        </button>
                        <button
                          type="button"
                          className="btn-ghost !px-3 !py-1.5 text-xs"
                          onClick={() => setAiCandidate(null)}
                        >
                          放弃
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </article>
          ))}
        </section>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn-ghost"
          onClick={() => markDirty([...beats, newBeat(beats.length)])}
        >
          <Plus size={14} /> 新增节拍
        </button>
        <button type="button" className="btn-ghost" onClick={() => setShowScript((v) => !v)}>
          {showScript ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          剧本正文
        </button>
        <span className="text-xs text-foreground-500">{dirty ? "有未保存修改" : "已同步"}</span>
        <div className="ml-auto flex gap-2">
          <button type="button" className="btn-ghost" onClick={save} disabled={busy || !beats.length}>
            保存剧本
          </button>
          <button type="button" className="btn-primary" onClick={saveAndNext} disabled={busy || !beats.length}>
            保存并继续
          </button>
        </div>
      </div>

      {showScript && (
        <section className="panel space-y-2 p-4">
          <p className="text-xs text-foreground-500">剧本正文（可编辑，随「保存剧本」一起提交）</p>
          <textarea
            className="input min-h-[220px] resize-y font-mono text-xs leading-relaxed"
            value={script}
            onChange={(event) => markDirty(event.target.value)}
            placeholder="分场剧本正文"
          />
        </section>
      )}
    </div>
  );
}
