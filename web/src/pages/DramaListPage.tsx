/** 短剧项目列表：项目卡片 + 新建。 */
import { useEffect, useState } from "react";
import { Clapperboard, Film, Plus } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import ProjectCreateDialog from "../components/drama/ProjectCreateDialog";
import Toast, { useToast } from "../components/drama/Toast";
import { EPISODE_STATUS_LABEL, EPISODE_STATUS_TONE, errText, fmtTime } from "../components/drama/labels";
import type { DramaProject, DramaStatus } from "../types";

export default function DramaListPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [projects, setProjects] = useState<DramaProject[]>([]);
  const [status, setStatus] = useState<DramaStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.dramaProjects();
      setProjects(result.projects);
    } catch (exc) {
      setError(errText(exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    let alive = true;
    (async () => {
      const remote = await api.dramaStatus().catch(() => null);
      if (!alive) return;
      setStatus(remote);
      // 测试期非管理员不拉列表（避免拉出 403 反而像报错），直接展示拦截提示
      if (remote && !remote.available) {
        setLoading(false);
        return;
      }
      await load();
    })();
    return () => {
      alive = false;
    };
  }, []);

  const unavailable = status !== null && !status.available;
  const blockedTitle = status?.enabled ? "短剧功能测试中，暂仅管理员可用" : "短剧功能未开启";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-foreground-950">
            <Clapperboard size={18} /> 短剧流水线
          </h1>
          <p className="mt-0.5 text-xs text-foreground-500">
            剧本 → 角色 → 分镜 → 就绪 → 生成 → 审片 → 交付，一集一集往下做。
          </p>
        </div>
        <button
          type="button"
          className="btn-primary ml-auto"
          onClick={() => setCreating(true)}
          disabled={unavailable}
          title={unavailable ? blockedTitle : ""}
        >
          <Plus size={15} /> 新建短剧
        </button>
      </div>

      {status && !status.enabled && (
        <div className="panel border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800">
          短剧功能当前未开启（需要 DRAMA_ENABLED 与 DIRECTOR_ENABLED）。
        </div>
      )}

      {status && status.enabled && !status.available && (
        <div className="panel border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800">
          短剧功能测试中，暂仅管理员可用；测试完成后对外开放。
        </div>
      )}

      {status && status.available && !status.director_node_available && (
        <div className="panel border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800">
          当前没有带 director 标签的生成节点，可以编辑剧本与分镜，但暂时不能提交整集。
        </div>
      )}

      {error && (
        <div className="panel border-rose-200 bg-rose-50 px-4 py-2.5 text-xs text-rose-700">
          {error}
          <button type="button" className="ml-2 underline" onClick={() => void load()}>
            重试
          </button>
        </div>
      )}

      {loading ? (
        <div className="panel px-6 py-16 text-center text-sm text-foreground-500">加载中…</div>
      ) : unavailable ? (
        <div className="panel px-6 py-16 text-center text-sm text-foreground-500">
          {blockedTitle}；测试完成后对外开放。
        </div>
      ) : projects.length === 0 ? (
        <div className="panel flex flex-col items-center gap-3 px-6 py-16 text-center">
          <Film size={24} className="text-foreground-400" />
          <p className="text-sm text-foreground-700">还没有短剧项目</p>
          <p className="max-w-md text-xs leading-relaxed text-foreground-500">
            新建项目会同时创建第 1 集；先写梗概，系统会按节拍拆剧本、抽角色、规划分镜并整集出片。
          </p>
          <button
            type="button"
            className="btn-primary"
            onClick={() => setCreating(true)}
            disabled={unavailable}
          >
            <Plus size={15} /> 新建第一部短剧
          </button>
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {projects.map((project) => (
            <Link
              key={project.id}
              to={`/drama/${project.id}`}
              className="panel group space-y-3 p-4 transition hover:border-primary-300 hover:shadow-sm"
            >
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <h2 className="truncate text-base font-medium text-foreground-950 group-hover:text-primary-700">
                    {project.title}
                  </h2>
                  <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-foreground-500">
                    {project.logline || "未填写梗概"}
                  </p>
                </div>
                <span className="chip shrink-0">{project.aspect_ratio}</span>
              </div>

              <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-foreground-500">
                {project.genre && <span className="chip">{project.genre}</span>}
                <span>{project.episode_count ?? project.episodes?.length ?? 0} 集</span>
                <span>·</span>
                <span>目标 {project.target_episodes} 集</span>
                <span className="ml-auto">{fmtTime(project.updated_at)}</span>
              </div>

              {project.episodes && project.episodes.length > 0 && (
                <div className="flex flex-wrap gap-1.5 border-t border-background-200 pt-2.5">
                  {project.episodes.slice(-4).map((episode) => (
                    <span
                      key={episode.id}
                      className={`rounded-md px-1.5 py-0.5 text-[10px] ${
                        EPISODE_STATUS_TONE[episode.status] || "bg-background-200 text-foreground-600"
                      }`}
                    >
                      第 {episode.episode_no} 集 · {EPISODE_STATUS_LABEL[episode.status] || episode.status}
                    </span>
                  ))}
                  {(project.episodes.length || 0) > 4 && (
                    <span className="text-[10px] text-foreground-400">…</span>
                  )}
                </div>
              )}
            </Link>
          ))}
        </div>
      )}

      <ProjectCreateDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(project, episode) => {
          setCreating(false);
          toast.show("项目已创建，开始写第 1 集");
          navigate(`/drama/${project.id}/ep/${episode.id}`);
        }}
      />
      <Toast message={toast.message} visible={toast.visible} />
    </div>
  );
}
