/** S2 角色与场景：定妆图上传/锁卡、角色编辑、场景锚图。 */
import { useMemo, useRef, useState } from "react";
import {
  ImagePlus,
  Lock,
  LockOpen,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  UserRound,
} from "lucide-react";
import { api, fileUrl } from "../../api/client";
import type { DramaCharacter, DramaEpisode, DramaScene } from "../../types";
import { errText } from "./labels";

const ROLE_LABEL: Record<string, string> = {
  main: "主要",
  support: "配角",
  extra: "群演",
};

function roleTone(role: string): string {
  if (role === "main") return "bg-primary-100 text-primary-700";
  if (role === "support") return "bg-sky-100 text-sky-700";
  return "bg-background-200 text-foreground-600";
}

function CharacterCard({
  character,
  refCount,
  disabled,
  notify,
  onChanged,
}: {
  character: DramaCharacter;
  refCount: number;
  disabled: boolean;
  notify: (message: string) => void;
  onChanged: () => void | Promise<void>;
}) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({
    name: character.name,
    appearance: character.appearance,
    costume: character.costume,
    personality: character.personality,
    voice: character.voice,
  });

  const canDelete = !character.locked && refCount === 0 && !disabled;

  const upload = async (file: File) => {
    if (refCount > 0) {
      const ok = window.confirm(
        `更换「${character.name}」的定妆图会让 ${refCount} 个镜头变成旧版，需要重新生成整集。确认更换？`,
      );
      if (!ok) return;
    }
    setBusy(true);
    try {
      await api.dramaUploadCharacter(character.id, file);
      notify(refCount > 0 ? `定妆图已更换，${refCount} 个镜头标记为旧版` : "定妆图已上传");
      await onChanged();
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  const toggleLock = async () => {
    if (!character.locked && !character.card_upload_id) {
      notify("请先上传定妆图再锁卡");
      return;
    }
    setBusy(true);
    try {
      await api.dramaLockCharacter(character.id, !character.locked);
      notify(character.locked ? `「${character.name}」已解锁` : `「${character.name}」已锁卡`);
      await onChanged();
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(`确认删除角色「${character.name}」？`)) return;
    setBusy(true);
    try {
      await api.dramaDeleteCharacter(character.id);
      notify("角色已删除");
      await onChanged();
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  const saveDraft = async () => {
    setBusy(true);
    try {
      await api.dramaPatchCharacter(character.id, draft);
      setEditing(false);
      notify("角色信息已保存");
      await onChanged();
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <article
      className={`panel space-y-3 p-3 ${
        character.role === "main" && !character.locked ? "border-rose-300" : ""
      }`}
    >
      <div className="flex items-start gap-3">
        <button
          type="button"
          className="group relative h-[92px] w-[72px] shrink-0 overflow-hidden rounded-xl border border-background-300 bg-background-200"
          onClick={() => fileRef.current?.click()}
          disabled={busy || disabled}
          title="上传定妆图"
        >
          {character.card_url ? (
            <img
              src={fileUrl(character.card_url)}
              alt={character.name}
              className="h-full w-full object-cover"
            />
          ) : (
            <span className="flex h-full flex-col items-center justify-center gap-1 text-[10px] text-foreground-500">
              <ImagePlus size={16} />
              上传定妆图
            </span>
          )}
          <span className="absolute inset-0 hidden items-center justify-center bg-foreground-950/40 text-background-50 group-hover:flex">
            <ImagePlus size={16} />
          </span>
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
            event.target.value = "";
          }}
        />

        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-1.5">
            <h3 className="truncate text-sm font-medium text-foreground-900">{character.name}</h3>
            <span className={`rounded px-1 py-0.5 text-[10px] ${roleTone(character.role)}`}>
              {ROLE_LABEL[character.role] || character.role}
            </span>
            {character.locked && (
              <span className="rounded bg-emerald-100 px-1 py-0.5 text-[10px] text-emerald-700">
                已锁卡
              </span>
            )}
          </div>
          <p className="line-clamp-2 text-[11px] leading-relaxed text-foreground-500">
            {character.appearance || "尚未填写外形"}
          </p>
          <p className="line-clamp-1 text-[11px] text-foreground-500">
            {character.costume || "尚未填写服装"}
          </p>
          {refCount > 0 && (
            <p className="text-[10px] text-foreground-400">被 {refCount} 个镜头引用</p>
          )}
        </div>
      </div>

      {editing ? (
        <div className="space-y-2 border-t border-background-200 pt-2">
          <input
            className="input text-xs"
            value={draft.name}
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            placeholder="姓名"
          />
          <textarea
            className="input min-h-[52px] resize-y text-xs"
            value={draft.appearance}
            onChange={(event) => setDraft({ ...draft, appearance: event.target.value })}
            placeholder="外形（年龄、发型、脸部特征）"
          />
          <input
            className="input text-xs"
            value={draft.costume}
            onChange={(event) => setDraft({ ...draft, costume: event.target.value })}
            placeholder="服装"
          />
          <input
            className="input text-xs"
            value={draft.personality}
            onChange={(event) => setDraft({ ...draft, personality: event.target.value })}
            placeholder="性格"
          />
          <input
            className="input text-xs"
            value={draft.voice}
            onChange={(event) => setDraft({ ...draft, voice: event.target.value })}
            placeholder="声音特征"
          />
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-ghost py-1 text-xs" onClick={() => setEditing(false)}>
              取消
            </button>
            <button type="button" className="btn-primary py-1 text-xs" onClick={saveDraft} disabled={busy}>
              保存
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-1.5 border-t border-background-200 pt-2">
          <button
            type="button"
            className="btn-ghost px-2 py-1 text-xs"
            onClick={toggleLock}
            disabled={busy || disabled}
            title={!character.card_upload_id ? "请先上传定妆图" : ""}
          >
            {character.locked ? <LockOpen size={12} /> : <Lock size={12} />}
            {character.locked ? "解锁" : "锁卡"}
          </button>
          <button
            type="button"
            className="btn-ghost px-2 py-1 text-xs"
            onClick={() => setEditing((value) => !value)}
            disabled={busy || disabled}
          >
            <Pencil size={12} /> 编辑
          </button>
          <button
            type="button"
            className="btn-ghost px-2 py-1 text-xs text-rose-600 disabled:text-foreground-400"
            onClick={remove}
            disabled={busy || !canDelete}
            title={character.locked ? "已锁卡，请先解锁" : refCount > 0 ? "被镜头引用，无法删除" : ""}
          >
            <Trash2 size={12} /> 删除
          </button>
        </div>
      )}
    </article>
  );
}

function SceneRow({
  scene,
  used,
  disabled,
  notify,
  onChanged,
}: {
  scene: DramaScene;
  used: number;
  disabled: boolean;
  notify: (message: string) => void;
  onChanged: () => void | Promise<void>;
}) {
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({
    name: scene.name,
    location: scene.location,
    time_of_day: scene.time_of_day,
    description: scene.description,
  });

  const upload = async (file: File) => {
    if (used > 0) {
      const ok = window.confirm(
        `更换场景「${scene.name}」的锚图会让 ${used} 个镜头变成旧版，需要重新生成整集。确认更换？`,
      );
      if (!ok) return;
    }
    setBusy(true);
    try {
      await api.dramaUploadScene(scene.id, file);
      notify("场景锚图已上传");
      await onChanged();
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(`确认删除场景「${scene.name}」？`)) return;
    setBusy(true);
    try {
      await api.dramaDeleteScene(scene.id);
      notify("场景已删除");
      await onChanged();
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  const saveDraft = async () => {
    setBusy(true);
    try {
      await api.dramaPatchScene(scene.id, draft);
      setEditing(false);
      notify("场景已保存");
      await onChanged();
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-xl border border-background-200 p-2.5">
      <div className="flex items-start gap-2.5">
        <button
          type="button"
          className="h-14 w-20 shrink-0 overflow-hidden rounded-lg border border-background-300 bg-background-200"
          onClick={() => fileRef.current?.click()}
          disabled={busy || disabled}
          title="上传场景锚图"
        >
          {scene.anchor_url ? (
            <img src={fileUrl(scene.anchor_url)} alt={scene.name} className="h-full w-full object-cover" />
          ) : (
            <span className="flex h-full flex-col items-center justify-center gap-0.5 text-[10px] text-foreground-500">
              <ImagePlus size={14} />
              锚图
            </span>
          )}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
            event.target.value = "";
          }}
        />

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="chip">#{scene.scene_no}</span>
            <span className="truncate text-sm text-foreground-900">{scene.name}</span>
            <span className="text-[11px] text-foreground-500">{scene.time_of_day}</span>
          </div>
          <p className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-foreground-500">
            {scene.description || scene.location || "尚未填写场景描述"}
          </p>
          {used > 0 && <p className="mt-0.5 text-[10px] text-foreground-400">被 {used} 个镜头引用</p>}
        </div>

        <div className="flex shrink-0 gap-1">
          <button
            type="button"
            className="rounded-lg p-1 text-foreground-400 hover:bg-background-200 hover:text-foreground-700"
            onClick={() => setEditing((value) => !value)}
            disabled={busy || disabled}
            title="编辑场景"
          >
            <Pencil size={13} />
          </button>
          <button
            type="button"
            className="rounded-lg p-1 text-foreground-400 hover:bg-rose-50 hover:text-rose-600 disabled:text-foreground-300 disabled:hover:bg-transparent"
            onClick={remove}
            disabled={busy || disabled || used > 0}
            title={used > 0 ? "被镜头引用，无法删除" : "删除场景"}
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      {editing && (
        <div className="mt-2 space-y-2 border-t border-background-200 pt-2">
          <div className="grid grid-cols-2 gap-2">
            <input
              className="input text-xs"
              value={draft.name}
              onChange={(event) => setDraft({ ...draft, name: event.target.value })}
              placeholder="场景名"
            />
            <input
              className="input text-xs"
              value={draft.time_of_day}
              onChange={(event) => setDraft({ ...draft, time_of_day: event.target.value })}
              placeholder="时间（日/夜/黄昏）"
            />
          </div>
          <input
            className="input text-xs"
            value={draft.location}
            onChange={(event) => setDraft({ ...draft, location: event.target.value })}
            placeholder="地点"
          />
          <textarea
            className="input min-h-[52px] resize-y text-xs"
            value={draft.description}
            onChange={(event) => setDraft({ ...draft, description: event.target.value })}
            placeholder="场景描述"
          />
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-ghost py-1 text-xs" onClick={() => setEditing(false)}>
              取消
            </button>
            <button type="button" className="btn-primary py-1 text-xs" onClick={saveDraft} disabled={busy}>
              保存
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function CastStage({
  projectId,
  episode,
  characters,
  scenes,
  notify,
  onChanged,
  onNext,
}: {
  projectId: number;
  episode: DramaEpisode;
  characters: DramaCharacter[];
  scenes: DramaScene[];
  notify: (message: string) => void;
  onChanged: () => void | Promise<void>;
  onNext: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState("");
  const [newRole, setNewRole] = useState("main");
  const [newScene, setNewScene] = useState("");

  const refCounts = useMemo(() => {
    const counts = new Map<number, number>();
    for (const shot of episode.shots) {
      for (const cid of shot.character_ids || []) {
        counts.set(cid, (counts.get(cid) || 0) + 1);
      }
    }
    return counts;
  }, [episode.shots]);

  const sceneUse = useMemo(() => {
    const counts = new Map<number, number>();
    for (const shot of episode.shots) {
      counts.set(shot.scene_no, (counts.get(shot.scene_no) || 0) + 1);
    }
    return counts;
  }, [episode.shots]);

  const mains = characters.filter((char) => char.role === "main");
  const unlocked = mains.filter((char) => !char.card_upload_id || !char.locked);
  const g2Ready = mains.length > 0 && unlocked.length === 0;

  const extract = async () => {
    if (characters.length && !window.confirm("重新抽取会覆盖未锁卡角色的资料，确认继续？")) return;
    setBusy(true);
    try {
      const result = await api.dramaExtractCharacters(episode.id, characters.length > 0);
      notify(result.source === "local" ? "已用本地模板抽取角色与场景" : "角色与场景已抽取");
      await onChanged();
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  const addCharacter = async () => {
    if (!newName.trim()) {
      notify("请填写角色姓名");
      return;
    }
    setBusy(true);
    try {
      await api.dramaCreateCharacter(projectId, { name: newName.trim(), role: newRole });
      setNewName("");
      notify("角色已新增");
      await onChanged();
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  const addScene = async () => {
    if (!newScene.trim()) {
      notify("请填写场景名");
      return;
    }
    setBusy(true);
    try {
      await api.dramaCreateScene(episode.id, { name: newScene.trim() });
      setNewScene("");
      notify("场景已新增");
      await onChanged();
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <section className="panel flex flex-wrap items-center gap-2 p-3">
        <div className="mr-auto text-xs text-foreground-600">
          {mains.length === 0 ? (
            <span className="text-rose-600">还没有主要角色，请先抽取或新增</span>
          ) : unlocked.length === 0 ? (
            <span className="text-emerald-600">主要角色已全部锁卡，可以进入分镜</span>
          ) : (
            <span className="text-rose-600">
              还有 {unlocked.length} 个主要角色未锁卡：{unlocked.map((c) => c.name).join("、")}
            </span>
          )}
        </div>
        <button type="button" className="btn-ghost" onClick={extract} disabled={busy}>
          <RefreshCw size={14} /> {characters.length ? "重新抽取" : "AI 抽取角色与场景"}
        </button>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-1.5 text-sm font-medium text-foreground-900">
            <UserRound size={15} /> 角色卡（{characters.length}）
          </h2>
          <div className="flex items-center gap-2">
            <input
              className="input w-36 py-1.5 text-xs"
              placeholder="新增角色姓名"
              value={newName}
              onChange={(event) => setNewName(event.target.value)}
            />
            <select
              className="input w-24 py-1.5 text-xs"
              value={newRole}
              onChange={(event) => setNewRole(event.target.value)}
            >
              <option value="main">主要</option>
              <option value="support">配角</option>
              <option value="extra">群演</option>
            </select>
            <button type="button" className="btn-ghost px-2 py-1.5 text-xs" onClick={addCharacter} disabled={busy}>
              <Plus size={13} /> 新增
            </button>
          </div>
        </div>

        {characters.length === 0 ? (
          <div className="panel px-5 py-8 text-center text-sm text-foreground-500">
            点「AI 抽取角色与场景」从剧本自动提取，或手动新增角色。
          </div>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {characters.map((character) => (
              <CharacterCard
                key={character.id}
                character={character}
                refCount={refCounts.get(character.id) || 0}
                disabled={busy}
                notify={notify}
                onChanged={onChanged}
              />
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium text-foreground-900">场景（{scenes.length}）</h2>
          <div className="flex items-center gap-2">
            <input
              className="input w-40 py-1.5 text-xs"
              placeholder="新增场景名"
              value={newScene}
              onChange={(event) => setNewScene(event.target.value)}
            />
            <button type="button" className="btn-ghost px-2 py-1.5 text-xs" onClick={addScene} disabled={busy}>
              <Plus size={13} /> 新增
            </button>
          </div>
        </div>

        {scenes.length === 0 ? (
          <div className="panel px-5 py-6 text-center text-sm text-foreground-500">
            尚未抽取场景。场景锚图用于整集生成时保持空间与光线一致。
          </div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2">
            {scenes.map((scene) => (
              <SceneRow
                key={scene.id}
                scene={scene}
                used={sceneUse.get(scene.scene_no) || 0}
                disabled={busy}
                notify={notify}
                onChanged={onChanged}
              />
            ))}
          </div>
        )}
      </section>

      <div className="flex items-center justify-end gap-2">
        {!g2Ready && (
          <span className="text-xs text-rose-600">
            {mains.length === 0 ? "需先新增主要角色" : "主要角色全部上传定妆图并锁卡后才能进入分镜"}
          </span>
        )}
        <button type="button" className="btn-primary" disabled={!g2Ready || busy} onClick={onNext}>
          {g2Ready ? "进入分镜" : "锁卡后进入分镜"}
        </button>
      </div>
    </div>
  );
}
