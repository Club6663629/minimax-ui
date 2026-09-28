/** 使用台账：生成记录 + 积分流水。 */
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { MODE_LABEL, STATUS_LABEL, type CreditLog, type Task } from "../types";

const LOG_LABEL: Record<CreditLog["type"], string> = {
  signup: "注册赠送",
  consume: "生成消耗",
  refund: "失败退还",
  redeem: "兑换充值",
  adjust: "管理员调整",
};

function fmtTime(s: string | null): string {
  if (!s) return "-";
  return new Date(s).toLocaleString("zh-CN", { hour12: false });
}

function fmtDuration(task: Task): string {
  if (!task.started_at || !task.finished_at) return "-";
  const secs = Math.round(
    (new Date(task.finished_at).getTime() - new Date(task.started_at).getTime()) / 1000
  );
  if (secs < 60) return `${secs} 秒`;
  return `${Math.floor(secs / 60)} 分 ${secs % 60} 秒`;
}

export default function LedgerPage() {
  const [tab, setTab] = useState<"tasks" | "credits">("tasks");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [logs, setLogs] = useState<CreditLog[]>([]);

  useEffect(() => {
    api.listVideos().then(setTasks).catch(() => {});
    api.creditLogs().then(setLogs).catch(() => {});
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold text-foreground-950">使用台账</h1>
        <div className="ml-4 flex rounded-xl bg-background-200 p-1 text-sm">
          {(["tasks", "credits"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-lg px-4 py-1.5 transition ${
                tab === t ? "bg-background-50 text-foreground-900 shadow-sm" : "text-foreground-500 hover:text-foreground-800"
              }`}
            >
              {t === "tasks" ? "生成记录" : "积分流水"}
            </button>
          ))}
        </div>
      </div>

      {tab === "tasks" ? (
        <div className="panel overflow-x-auto p-5">
          {tasks.length === 0 ? (
            <p className="py-10 text-center text-sm text-foreground-500">暂无生成记录</p>
          ) : (
            <table className="w-full min-w-[760px] text-sm">
              <thead>
                <tr className="border-b border-background-200 text-left text-xs text-foreground-500">
                  <th className="pb-2 font-normal">时间</th>
                  <th className="pb-2 font-normal">模式</th>
                  <th className="pb-2 font-normal">提示词</th>
                  <th className="pb-2 font-normal">配置</th>
                  <th className="pb-2 font-normal">状态</th>
                  <th className="pb-2 font-normal">生成耗时</th>
                  <th className="pb-2 text-right font-normal">积分</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((t) => (
                  <tr key={t.id} className="border-b border-background-100 align-top last:border-0">
                    <td className="whitespace-nowrap py-2.5 text-xs text-foreground-500">
                      {fmtTime(t.created_at)}
                    </td>
                    <td className="py-2.5 text-foreground-800">{MODE_LABEL[t.mode]}</td>
                    <td className="max-w-[260px] truncate py-2.5 text-foreground-600" title={t.prompt}>
                      {t.prompt}
                    </td>
                    <td className="whitespace-nowrap py-2.5 text-xs text-foreground-500">
                      {t.aspect_ratio} · {t.duration}s · {t.resolution}
                    </td>
                    <td className="py-2.5">
                      <span
                        className={
                          t.status === "done"
                            ? "text-emerald-600"
                            : t.status === "failed"
                              ? "text-rose-600"
                              : "text-primary-600"
                        }
                      >
                        {STATUS_LABEL[t.status]}
                      </span>
                    </td>
                    <td className="whitespace-nowrap py-2.5 text-xs text-foreground-500">
                      {fmtDuration(t)}
                    </td>
                    <td className="py-2.5 text-right text-foreground-800">
                      {t.status === "done" || t.status === "failed"
                        ? t.status === "failed"
                          ? "已退还"
                          : `-${t.cost}`
                        : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : (
        <div className="panel overflow-x-auto p-5">
          {logs.length === 0 ? (
            <p className="py-10 text-center text-sm text-foreground-500">暂无积分流水</p>
          ) : (
            <table className="w-full min-w-[560px] text-sm">
              <thead>
                <tr className="border-b border-background-200 text-left text-xs text-foreground-500">
                  <th className="pb-2 font-normal">时间</th>
                  <th className="pb-2 font-normal">类型</th>
                  <th className="pb-2 font-normal">说明</th>
                  <th className="pb-2 font-normal">关联任务</th>
                  <th className="pb-2 text-right font-normal">积分变动</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((l) => (
                  <tr key={l.id} className="border-b border-background-100 last:border-0">
                    <td className="whitespace-nowrap py-2.5 text-xs text-foreground-500">
                      {fmtTime(l.created_at)}
                    </td>
                    <td className="py-2.5 text-foreground-800">{LOG_LABEL[l.type] ?? l.type}</td>
                    <td className="max-w-[300px] truncate py-2.5 text-foreground-500" title={l.note}>
                      {l.note}
                    </td>
                    <td className="py-2.5 text-xs text-foreground-500">
                      {l.task_id ? `#${l.task_id}` : "-"}
                    </td>
                    <td
                      className={`py-2.5 text-right font-medium ${
                        l.amount > 0 ? "text-emerald-600" : "text-rose-600"
                      }`}
                    >
                      {l.amount > 0 ? `+${l.amount}` : l.amount}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
