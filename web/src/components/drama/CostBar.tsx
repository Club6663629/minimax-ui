/** 右栏成本卡：时长 / 预估积分 / 余额，数据来自 checklist。 */
import { RefreshCw } from "lucide-react";
import type { DramaChecklist } from "../../types";
import { fmtSecs } from "./labels";

export default function CostBar({
  checklist,
  targetSeconds,
  loading,
  onRefresh,
}: {
  checklist: DramaChecklist | null;
  targetSeconds?: number;
  loading?: boolean;
  onRefresh?: () => void;
}) {
  const total = checklist?.total_seconds ?? 0;
  const est = checklist?.est_cost ?? 0;
  const balance = checklist?.balance ?? 0;
  const sufficient = checklist?.sufficient ?? true;
  return (
    <div className="panel space-y-3 p-4 text-sm">
      <div className="flex items-center justify-between">
        <h3 className="font-medium text-foreground-900">时长与成本</h3>
        {onRefresh && (
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            className="rounded-lg p-1 text-foreground-500 transition hover:bg-background-200 hover:text-foreground-800 disabled:opacity-50"
            title="重新计算"
          >
            <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
          </button>
        )}
      </div>

      <dl className="space-y-2 text-xs">
        <div className="flex items-center justify-between">
          <dt className="text-foreground-500">目标时长</dt>
          <dd className="text-foreground-800">{targetSeconds ? fmtSecs(targetSeconds) : "-"}</dd>
        </div>
        <div className="flex items-center justify-between">
          <dt className="text-foreground-500">实际时长</dt>
          <dd className="text-foreground-800">{fmtSecs(total)}</dd>
        </div>
        <div className="flex items-center justify-between">
          <dt className="text-foreground-500">帧数</dt>
          <dd className="text-foreground-800">{checklist?.plan?.total_frames ?? "-"}</dd>
        </div>
        <div className="my-2 border-t border-background-200" />
        <div className="flex items-center justify-between">
          <dt className="text-foreground-500">预估积分</dt>
          <dd className="font-medium text-foreground-900">{est > 0 ? `约 ${est}` : "-"}</dd>
        </div>
        <div className="flex items-center justify-between">
          <dt className="text-foreground-500">余额</dt>
          <dd className={sufficient ? "text-emerald-600" : "text-rose-600"}>
            {balance}
            {sufficient ? " ✓" : " 不足"}
          </dd>
        </div>
      </dl>

      {!sufficient && (
        <a href="/ledger" className="block rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 hover:bg-rose-100">
          余额不足，去台账充值
        </a>
      )}
      <p className="text-[11px] leading-relaxed text-foreground-500">
        计费按 5 秒一块向上取整；失败自动退还积分。
      </p>
    </div>
  );
}
