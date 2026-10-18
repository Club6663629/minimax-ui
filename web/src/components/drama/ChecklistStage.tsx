/** S4 就绪：清单分组、提交预览、成本确认与整集生成入口。 */
import { useMemo, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Rocket,
  RefreshCw,
} from "lucide-react";
import type { DramaChecklist, DramaEpisode, DramaIssue } from "../../types";
import { fmtSecs } from "./labels";

const GROUPS: { code: string; label: string; codes: string[] }[] = [
  {
    code: "content",
    label: "内容完整性",
    codes: ["SCRIPT_EMPTY", "SHOTS_EMPTY", "SHOT_NO_GAP", "SHOT_PROMPT_EMPTY", "SHOT_SCENE_MISSING", "PLAN_FAILED"],
  },
  {
    code: "cast",
    label: "角色一致性",
    codes: ["CAST_UNLOCKED", "SHOT_CHARACTER_MISSING"],
  },
  {
    code: "cost",
    label: "时长与成本",
    codes: ["SHOT_DURATION", "TOTAL_SECONDS", "INSUFFICIENT_CREDITS"],
  },
  { code: "tech", label: "技术限制", codes: [] },
];

function groupOf(issue: DramaIssue): string {
  return GROUPS.find((group) => group.codes.includes(issue.code))?.code || "tech";
}

export default function ChecklistStage({
  episode,
  checklist,
  loading,
  targetSeconds,
  onRefresh,
  onRender,
  onViewRun,
  onJumpShot,
  onJumpCast,
  notify,
}: {
  episode: DramaEpisode;
  checklist: DramaChecklist | null;
  loading: boolean;
  targetSeconds: number;
  onRefresh: () => void;
  onRender: (force: boolean) => void | Promise<void>;
  onViewRun: () => void;
  onJumpShot?: (shotNo: number) => void;
  onJumpCast?: () => void;
  notify: (message: string) => void;
}) {
  const [checkedShots, setCheckedShots] = useState(false);
  const [checkedCost, setCheckedCost] = useState(false);
  const [showWarnings, setShowWarnings] = useState(false);
  const [showPlan, setShowPlan] = useState(false);
  const [busy, setBusy] = useState(false);

  const grouped = useMemo(() => {
    const map: Record<string, DramaIssue[]> = { content: [], cast: [], cost: [], tech: [] };
    for (const issue of checklist?.issues || []) map[groupOf(issue)].push(issue);
    const warningMap: Record<string, DramaIssue[]> = { content: [], cast: [], cost: [], tech: [] };
    for (const warning of checklist?.warnings || []) warningMap[groupOf(warning)].push(warning);
    return { issues: map, warnings: warningMap };
  }, [checklist]);

  const activeRun = episode.active_run;
  const reusableFinal = Boolean(episode.final_url) && !episode.final_stale;
  const canRender =
    Boolean(checklist?.ready) &&
    Boolean(checklist?.sufficient) &&
    checkedShots &&
    checkedCost &&
    !activeRun &&
    !busy;

  const submit = async (forceRender: boolean) => {
    if (!canRender) return;
    if (forceRender) {
      const ok = window.confirm(
        `强制重新生成会重新扣除约 ${checklist?.est_cost ?? 0} 积分，且不会复用现有成片。确认继续？`,
      );
      if (!ok) return;
    }
    setBusy(true);
    try {
      await onRender(forceRender);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <section className="panel flex flex-wrap items-center gap-3 px-4 py-3">
        {checklist?.ready ? (
          <span className="inline-flex items-center gap-1.5 text-sm text-emerald-700">
            <CheckCircle2 size={16} /> 就绪检查通过，可以生成
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5 text-sm text-rose-700">
            <AlertCircle size={16} /> 还有 {checklist?.issues.length ?? 0} 项需要处理
          </span>
        )}
        <span className="text-xs text-foreground-500">
          {checklist?.shots ?? 0} 镜 · {fmtSecs(checklist?.total_seconds ?? 0)} ·{" "}
          {checklist?.plan?.total_frames ?? 0} 帧
        </span>
        <button
          type="button"
          className="ml-auto btn-ghost px-2 py-1 text-xs"
          onClick={onRefresh}
          disabled={loading}
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} /> 重新检查
        </button>
      </section>

      <div className="grid gap-3 lg:grid-cols-2">
        {GROUPS.map((group) => {
          const issues = grouped.issues[group.code];
          const warnings = grouped.warnings[group.code];
          return (
            <section key={group.code} className="panel space-y-2 p-4">
              <h3 className="flex items-center gap-1.5 text-sm font-medium text-foreground-900">
                {group.label}
                {issues.length === 0 ? (
                  <span className="text-[10px] font-normal text-emerald-600">通过</span>
                ) : (
                  <span className="text-[10px] font-normal text-rose-600">{issues.length} 项</span>
                )}
              </h3>
              {issues.length === 0 && warnings.length === 0 ? (
                <p className="text-xs text-foreground-500">没有发现问题。</p>
              ) : (
                <ul className="space-y-1">
                  {issues.map((issue, index) => (
                    <li key={`${issue.code}-${index}`}>
                      <button
                        type="button"
                        className="flex w-full items-start gap-1.5 rounded-lg px-1.5 py-1 text-left text-xs text-rose-700 hover:bg-rose-50"
                        onClick={() => {
                          if (issue.shot_no && onJumpShot) onJumpShot(issue.shot_no);
                          else if (group.code === "cast") onJumpCast?.();
                        }}
                      >
                        <AlertCircle size={12} className="mt-0.5 shrink-0" />
                        <span>{issue.message}</span>
                      </button>
                    </li>
                  ))}
                  {warnings.map((warning, index) => (
                    <li
                      key={`w-${warning.code}-${index}`}
                      className="flex items-start gap-1.5 px-1.5 py-1 text-xs text-amber-700"
                    >
                      <AlertCircle size={12} className="mt-0.5 shrink-0" />
                      <span>{warning.message}</span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>

      <section className="panel p-4">
        <button
          type="button"
          className="flex w-full items-center gap-1.5 text-sm font-medium text-foreground-900"
          onClick={() => setShowPlan((value) => !value)}
        >
          {showPlan ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          提交预览（先确认后花钱）
          {checklist?.plan && (
            <span className="text-xs font-normal text-foreground-500">
              {checklist.plan.segments.length} 段 · {checklist.plan.fps}fps
            </span>
          )}
        </button>
        {showPlan && checklist?.plan && (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[720px] text-xs">
              <thead>
                <tr className="border-b border-background-200 text-left text-[11px] text-foreground-500">
                  <th className="py-1.5 pr-3 font-normal">镜号</th>
                  <th className="py-1.5 pr-3 font-normal">提交时长</th>
                  <th className="py-1.5 pr-3 font-normal">可见时长</th>
                  <th className="py-1.5 pr-3 font-normal">帧数</th>
                  <th className="py-1.5 pr-3 font-normal">参考图</th>
                  <th className="py-1.5 font-normal">提示词前 200 字</th>
                </tr>
              </thead>
              <tbody>
                {checklist.plan.segments.map((segment) => {
                  const window = checklist.plan?.windows.find((item) => item.shot_no === segment.shot_no);
                  return (
                    <tr key={segment.shot_no} className="border-b border-background-100 last:border-0">
                      <td className="py-1.5 pr-3">{segment.shot_no}</td>
                      <td className="py-1.5 pr-3">{fmtSecs(segment.frames / checklist.plan!.fps)}</td>
                      <td className="py-1.5 pr-3">
                        {fmtSecs(window?.duration ?? segment.visible_frames / checklist.plan!.fps)}
                      </td>
                      <td className="py-1.5 pr-3">
                        {segment.frames}
                        {segment.shot_no > 1 && (
                          <span className="ml-1 text-[10px] text-foreground-400">含 39 帧交叠</span>
                        )}
                      </td>
                      <td className="py-1.5 pr-3">{segment.ref_count}</td>
                      <td className="max-w-[340px] py-1.5 text-foreground-600">
                        <span className="line-clamp-2">{segment.prompt.slice(0, 200)}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {(checklist?.warnings.length ?? 0) > 0 && (
        <section className="panel p-4">
          <button
            type="button"
            className="flex w-full items-center gap-1.5 text-xs text-amber-700"
            onClick={() => setShowWarnings((value) => !value)}
          >
            {showWarnings ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            提醒 {checklist?.warnings.length} 条（不阻塞生成）
          </button>
          {showWarnings && (
            <ul className="mt-2 space-y-1 text-xs text-foreground-600">
              {checklist?.warnings.map((warning, index) => (
                <li key={`${warning.code}-${index}`}>· {warning.message}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      <section className="panel space-y-3 p-4">
        <div className="grid grid-cols-2 gap-3 text-xs md:grid-cols-5">
          <div>
            <p className="text-foreground-500">目标时长</p>
            <p className="mt-0.5 text-sm text-foreground-900">{fmtSecs(targetSeconds)}</p>
          </div>
          <div>
            <p className="text-foreground-500">实际时长</p>
            <p className="mt-0.5 text-sm text-foreground-900">{fmtSecs(checklist?.total_seconds ?? 0)}</p>
          </div>
          <div>
            <p className="text-foreground-500">帧数</p>
            <p className="mt-0.5 text-sm text-foreground-900">{checklist?.plan?.total_frames ?? 0}</p>
          </div>
          <div>
            <p className="text-foreground-500">预估积分</p>
            <p className="mt-0.5 text-sm font-medium text-foreground-900">
              约 {checklist?.est_cost ?? 0}
            </p>
          </div>
          <div>
            <p className="text-foreground-500">余额</p>
            <p
              className={`mt-0.5 text-sm ${
                checklist?.sufficient ? "text-emerald-600" : "text-rose-600"
              }`}
            >
              {checklist?.balance ?? 0}
              {checklist?.sufficient ? " ✓" : " 不足"}
            </p>
          </div>
        </div>

        <div className="space-y-2 border-t border-background-200 pt-3 text-xs text-foreground-700">
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={checkedShots}
              onChange={(event) => setCheckedShots(event.target.checked)}
            />
            我已核对分镜与角色卡
          </label>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={checkedCost}
              onChange={(event) => setCheckedCost(event.target.checked)}
            />
            我接受本次预估积分（约 {checklist?.est_cost ?? 0} 积分）
          </label>
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-background-200 pt-3">
          {activeRun ? (
            <button type="button" className="btn-primary" onClick={onViewRun}>
              查看进度（run #{activeRun.id}）
            </button>
          ) : (
            <>
              {!checklist?.ready && (
                <span className="mr-auto text-xs text-rose-600">请先处理左侧清单问题</span>
              )}
              {reusableFinal && (
                <button
                  type="button"
                  className="btn-ghost"
                  disabled={!canRender}
                  onClick={() => void submit(true)}
                  title="重新扣费，不复用现有成片"
                >
                  强制重新生成
                </button>
              )}
              <button
                type="button"
                className="btn-primary"
                disabled={!canRender}
                onClick={() => {
                  if (reusableFinal) {
                    notify("已有成片；直接生成会命中复用（0 积分），如需重跑请用「强制重新生成」");
                  }
                  void submit(false);
                }}
              >
                <Rocket size={15} /> {busy ? "提交中…" : "生成整集"}
              </button>
            </>
          )}
        </div>
        <p className="text-[11px] text-foreground-500">
          导演台独占整张显卡；提交后可在「生成」页离开再回来。生成失败会自动退还积分。
        </p>
      </section>
    </div>
  );
}
