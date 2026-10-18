/** S7 交付：成片播放与下载、消耗汇总、确认交付 / 重新编辑。 */
import { useState } from "react";
import { CheckCircle2, Copy, Download, Film, RotateCcw } from "lucide-react";
import { fileUrl } from "../../api/client";
import type { DramaEpisode } from "../../types";
import { errText, fmtSecs } from "./labels";

export default function DeliverStage({
  episode,
  notify,
  onFinish,
  onReopen,
  onGoReview,
}: {
  episode: DramaEpisode;
  notify: (message: string) => void;
  onFinish: () => void | Promise<void>;
  onReopen: () => void | Promise<void>;
  onGoReview: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const done = episode.status === "done";
  const finalStale = episode.final_stale;
  const canFinish =
    !done && !finalStale && Boolean(episode.final_url) && !episode.active_run;

  const copyLink = async () => {
    const path = `/api/drama/episodes/${episode.id}/download?kind=final`;
    const absolute = `${window.location.origin}${fileUrl(path)}`;
    try {
      await navigator.clipboard.writeText(absolute);
      notify("下载链接已复制（含访问凭证，请勿公开分享）");
    } catch {
      notify(absolute);
    }
  };

  const finish = async () => {
    if (!window.confirm("确认交付本集？交付后编辑会被锁定，可随时「重新编辑」。")) return;
    setBusy(true);
    try {
      await onFinish();
      notify("本集已交付");
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  const reopen = async () => {
    if (!window.confirm("重新编辑会回到分镜阶段，旧成片保留但标记为旧版。确认继续？")) return;
    setBusy(true);
    try {
      await onReopen();
      notify("已回到可编辑状态");
    } catch (exc) {
      notify(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      {done && (
        <section className="panel flex items-center gap-2 border-emerald-200 bg-emerald-50 px-4 py-2.5 text-sm text-emerald-800">
          <CheckCircle2 size={16} /> 本集已交付
        </section>
      )}

      {finalStale && (
        <section className="panel border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-800">
          成片已过旧（分镜或角色卡改动过），不能确认交付。请回「就绪」重新生成整集，或「重新编辑」继续调整。
        </section>
      )}

      <section className="panel space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="flex items-center gap-1.5 text-sm font-medium text-foreground-900">
            <Film size={15} /> 成片
          </h2>
          {episode.final_url ? (
            <div className="ml-auto flex items-center gap-2">
              <a
                className="btn-ghost px-2 py-1 text-xs"
                href={fileUrl(`/api/drama/episodes/${episode.id}/download?kind=final&download=1`)}
                target="_blank"
                rel="noreferrer"
              >
                <Download size={12} /> 下载成片
              </a>
              <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => void copyLink()}>
                <Copy size={12} /> 复制链接
              </button>
            </div>
          ) : (
            <span className="ml-auto text-xs text-rose-600">还没有成片</span>
          )}
        </div>

        {episode.final_url ? (
          <video
            key={episode.final_url}
            src={fileUrl(episode.final_url)}
            controls
            className="max-h-[420px] w-full rounded-xl bg-black"
          />
        ) : (
          <div className="rounded-xl bg-background-200 px-4 py-12 text-center text-xs text-foreground-500">
            回到「就绪」提交整集后，这里会出现成片与逐镜下载。
          </div>
        )}
      </section>

      <section className="panel grid grid-cols-2 gap-3 px-4 py-3 text-xs md:grid-cols-5">
        <div>
          <p className="text-foreground-500">成片时长</p>
          <p className="mt-0.5 text-sm text-foreground-900">{fmtSecs(episode.final_duration)}</p>
        </div>
        <div>
          <p className="text-foreground-500">镜头数</p>
          <p className="mt-0.5 text-sm text-foreground-900">{episode.stats.shots}</p>
        </div>
        <div>
          <p className="text-foreground-500">总消耗</p>
          <p className="mt-0.5 text-sm text-foreground-900">{episode.spent} 积分</p>
        </div>
        <div>
          <p className="text-foreground-500">生成批次</p>
          <p className="mt-0.5 text-sm text-foreground-900">{episode.run_count}</p>
        </div>
        <div>
          <p className="text-foreground-500" title="当前各镜成片的版本号去重数量（回滚不会新增版本）">
            镜头版本
          </p>
          <p className="mt-0.5 text-sm text-foreground-900">{episode.version_count}</p>
        </div>
      </section>

      <section className="panel p-4">
        <h3 className="text-sm font-medium text-foreground-900">逐镜下载</h3>
        <div className="mt-2 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {episode.shots.map((shot) => (
            <div
              key={shot.id}
              className="flex items-center gap-2 rounded-xl border border-background-200 px-2.5 py-2 text-xs"
            >
              <span className="text-foreground-500">#{shot.shot_no}</span>
              <span className="text-foreground-700">{fmtSecs(shot.duration_s)}</span>
              <span className="text-[10px] text-foreground-400">
                v{shot.version_no || 0}
                {shot.stale && " · 旧片"}
              </span>
              {shot.clip_url ? (
                <a
                  className="ml-auto inline-flex items-center gap-1 text-primary-600 hover:underline"
                  href={fileUrl(`${shot.clip_url}?download=1`)}
                  target="_blank"
                  rel="noreferrer"
                >
                  <Download size={11} /> 下载
                </a>
              ) : (
                <span className="ml-auto text-[10px] text-rose-600">缺成片</span>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="panel flex flex-wrap items-center justify-end gap-2 p-4">
        {!done && (
          <button type="button" className="btn-ghost" onClick={onGoReview}>
            回审片
          </button>
        )}
        {!done ? (
          <button
            type="button"
            className="btn-primary"
            disabled={!canFinish || busy}
            onClick={() => void finish()}
            title={finalStale ? "成片过旧，需重新生成" : ""}
          >
            <CheckCircle2 size={15} /> {busy ? "提交中…" : "确认交付"}
          </button>
        ) : (
          <button type="button" className="btn-ghost" disabled={busy} onClick={() => void reopen()}>
            <RotateCcw size={14} /> 重新编辑
          </button>
        )}
      </section>
    </div>
  );
}
