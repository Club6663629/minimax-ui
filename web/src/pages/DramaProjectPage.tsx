/** 短剧项目页：项目信息 + 集列表 + 新建下一集。 */
import { useEffect, useState } from "react";
import { ArrowLeft, Clapperboard, Film, Pencil, Plus, X } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, fileUrl } from "../api/client";
import Toast, { useToast } from "../components/drama/Toast";
import {
  EPISODE_STATUS_LABEL,
  EPISODE_STATUS_TONE,
  errText,
  fmtSecs,
  fmtTime,
} from "../components/drama/labels";
import type { DramaProjectDetail } from "../types";

export default function DramaProjectPage() {
  const { pid } = useParams();
  const projectId = Number(pid);
  const navigate = useNavigate();
  const toast = useToast();
  const [detail, setDetail] = useState<DramaProjectDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({
    title: "",
    logline: "",
    genre: "",
    style_bible: "",
    target_episodes: 1,
  });

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.dramaProject(projectId);
      setDetail(result);
      setDraft({
        title: result.project.title,
        logline: result.project.logline,
        genre: result.project.genre,
        style_bible: result.project.style_bible,
        target_episodes: result.project.target_episodes,
      });
    } catch (exc) {
      setError(errText(exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (Number.isFinite(projectId)) void load();
  }, [projectId]);

  if (loading) {
    return <div className="panel px-6 py-16 text-center text-sm text-foreground-500">加载中…</div>;
  }

  if (!detail) {
    return (
      <div className="panel space-y-3 px-6 py-12 text-center">
        <p className="text-sm text-rose-600">{error || "项目不存在"}</p>
        <Link to="/drama" className="btn-ghost">
          返回短剧列表
        </Link>
      </div>
    );
  }

  const { project, episodes } = detail;
  const nextNo = episodes.length + 1;
  const canAdd = nextNo <= project.target_episodes;

  const createEpisode = async () => {
    setBusy(true);
    try {
      const result = await api.dramaCreateEpisode(project.id, {
        title: `第 ${nextNo} 集`,
        brief: project.logline,
      });
      toast.show(`已创建第 ${result.episode.episode_no} 集`);
      navigate(`/drama/${project.id}/ep/${result.episode.id}`);
    } catch (exc) {
      toast.show(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  const saveProject = async () => {
    setBusy(true);
    try {
      await api.dramaPatchProject(project.id, draft);
      setEditing(false);
      toast.show("项目信息已保存");
      await load();
    } catch (exc) {
      toast.show(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-xs text-foreground-500">
        <Link to="/drama" className="inline-flex items-center gap-1 hover:text-foreground-800">
          <ArrowLeft size={13} /> 短剧列表
        </Link>
        <span>/</span>
        <span className="text-foreground-700">{project.title}</span>
      </div>

      <section className="panel space-y-3 p-4">
        {editing ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h1 className="text-base font-semibold text-foreground-950">编辑项目</h1>
              <button
                type="button"
                className="rounded-lg p-1 text-foreground-500 hover:bg-background-200"
                onClick={() => setEditing(false)}
              >
                <X size={15} />
              </button>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="space-y-1 text-xs text-foreground-600">
                <span>剧名</span>
                <input
                  className="input"
                  value={draft.title}
                  onChange={(event) => setDraft({ ...draft, title: event.target.value })}
                />
              </label>
              <label className="space-y-1 text-xs text-foreground-600">
                <span>题材</span>
                <input
                  className="input"
                  value={draft.genre}
                  onChange={(event) => setDraft({ ...draft, genre: event.target.value })}
                />
              </label>
            </div>
            <label className="block space-y-1 text-xs text-foreground-600">
              <span>一句话梗概</span>
              <textarea
                className="input min-h-[64px] resize-y"
                value={draft.logline}
                onChange={(event) => setDraft({ ...draft, logline: event.target.value })}
              />
            </label>
            <label className="block space-y-1 text-xs text-foreground-600">
              <span>风格圣经</span>
              <textarea
                className="input min-h-[72px] resize-y"
                value={draft.style_bible}
                onChange={(event) => setDraft({ ...draft, style_bible: event.target.value })}
              />
            </label>
            <label className="block w-32 space-y-1 text-xs text-foreground-600">
              <span>目标集数</span>
              <input
                type="number"
                min={episodes.length}
                max={60}
                className="input"
                value={draft.target_episodes}
                onChange={(event) =>
                  setDraft({ ...draft, target_episodes: Number(event.target.value) || 1 })
                }
              />
            </label>
            <div className="flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={() => setEditing(false)}>
                取消
              </button>
              <button type="button" className="btn-primary" onClick={saveProject} disabled={busy}>
                保存
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-start gap-2">
              <div className="min-w-0 flex-1">
                <h1 className="flex items-center gap-2 text-lg font-semibold text-foreground-950">
                  <Clapperboard size={18} /> {project.title}
                </h1>
                <p className="mt-1 max-w-3xl text-xs leading-relaxed text-foreground-500">
                  {project.logline || "未填写梗概"}
                </p>
              </div>
              <span className="chip">{project.aspect_ratio}</span>
              {project.genre && <span className="chip">{project.genre}</span>}
              <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => setEditing(true)}>
                <Pencil size={12} /> 编辑项目
              </button>
            </div>
            <div className="flex flex-wrap gap-3 text-[11px] text-foreground-500">
              <span>
                已建 {episodes.length} / 目标 {project.target_episodes} 集
              </span>
              <span>更新于 {fmtTime(project.updated_at)}</span>
            </div>
            {project.style_bible && (
              <details className="rounded-xl bg-background-200/60 px-3 py-2 text-xs text-foreground-600">
                <summary className="cursor-pointer text-foreground-700">风格圣经</summary>
                <p className="mt-1.5 whitespace-pre-wrap leading-relaxed">{project.style_bible}</p>
              </details>
            )}
          </>
        )}
      </section>

      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-1.5 text-sm font-medium text-foreground-900">
          <Film size={15} /> 剧集（{episodes.length}）
        </h2>
        <button
          type="button"
          className="btn-ghost px-2 py-1.5 text-xs"
          onClick={createEpisode}
          disabled={busy || !canAdd}
          title={!canAdd ? "已达到目标集数，请先调整目标集数" : ""}
        >
          <Plus size={13} /> 新建第 {nextNo} 集
        </button>
      </div>

      {episodes.length === 0 ? (
        <div className="panel px-6 py-12 text-center text-sm text-foreground-500">
          还没有剧集，点「新建第 1 集」开始。
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {episodes.map((episode) => (
            <Link
              key={episode.id}
              to={`/drama/${project.id}/ep/${episode.id}`}
              className="panel group space-y-2.5 p-4 transition hover:border-primary-300 hover:shadow-sm"
            >
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-[11px] text-foreground-400">第 {episode.episode_no} 集</p>
                  <h3 className="truncate text-sm font-medium text-foreground-950 group-hover:text-primary-700">
                    {episode.title}
                  </h3>
                </div>
                <span
                  className={`shrink-0 rounded-md px-1.5 py-0.5 text-[10px] ${
                    EPISODE_STATUS_TONE[episode.status] || "bg-background-200 text-foreground-600"
                  }`}
                >
                  {EPISODE_STATUS_LABEL[episode.status] || episode.status}
                </span>
              </div>

              {episode.final_url && (
                <video
                  src={fileUrl(episode.final_url)}
                  muted
                  preload="metadata"
                  playsInline
                  className="h-28 w-full rounded-lg bg-black object-cover"
                  onMouseEnter={(event) => void event.currentTarget.play().catch(() => {})}
                  onMouseLeave={(event) => {
                    event.currentTarget.pause();
                    event.currentTarget.currentTime = 0;
                  }}
                />
              )}

              <div className="flex flex-wrap items-center gap-2 text-[11px] text-foreground-500">
                <span>{episode.stats.shots} 镜</span>
                <span>{fmtSecs(episode.stats.total_seconds)}</span>
                {episode.final_duration > 0 && <span>成片 {fmtSecs(episode.final_duration)}</span>}
                {episode.final_stale && <span className="text-amber-600">成片旧版</span>}
                <span className="ml-auto">{fmtTime(episode.updated_at)}</span>
              </div>
            </Link>
          ))}
        </div>
      )}

      <Toast message={toast.message} visible={toast.visible} />
    </div>
  );
}
