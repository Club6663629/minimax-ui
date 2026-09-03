/** 管理后台：概览统计 / 用户管理 / 兑换码 / 任务监控 / Worker 池（仅管理员）。 */
import { Copy, Cpu, RefreshCw, Ticket, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../state/auth";
import {
  MODE_LABEL,
  STATUS_LABEL,
  type AdminStats,
  type AdminUser,
  type RedeemCodeOut,
  type Task,
  type WorkerPoolOut,
} from "../types";

type Tab = "overview" | "users" | "codes" | "tasks" | "workers";

function fmtTime(s: string | null): string {
  if (!s) return "-";
  return new Date(s).toLocaleString("zh-CN", { hour12: false });
}

export default function AdminPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<Tab>("overview");
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [codes, setCodes] = useState<RedeemCodeOut[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [workerPool, setWorkerPool] = useState<WorkerPoolOut | null>(null);

  // 用户积分调整
  const [adjustTarget, setAdjustTarget] = useState<AdminUser | null>(null);
  const [adjustAmount, setAdjustAmount] = useState("");
  const [adjustNote, setAdjustNote] = useState("");
  // 兑换码生成
  const [codeValue, setCodeValue] = useState("100");
  const [codeCount, setCodeCount] = useState("5");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, u, c, t, w] = await Promise.all([
        api.adminStats(),
        api.adminUsers(),
        api.adminCodes(),
        api.adminTasks(),
        api.adminWorkers(),
      ]);
      setStats(s);
      setUsers(u);
      setCodes(c);
      setTasks(t);
      setWorkerPool(w);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Worker 池 tab 开启时自动轮询（心跳/忙闲实时变化）
  useEffect(() => {
    if (tab !== "workers") return;
    const timer = setInterval(() => api.adminWorkers().then(setWorkerPool).catch(() => {}), 5000);
    return () => clearInterval(timer);
  }, [tab]);

  if (user?.role !== "admin") {
    return <p className="py-20 text-center text-sm text-zinc-600">需要管理员权限</p>;
  }

  async function doAdjust() {
    if (!adjustTarget) return;
    const amount = parseInt(adjustAmount, 10);
    if (!amount) return;
    try {
      await api.adminAdjust(adjustTarget.id, amount, adjustNote);
      setAdjustTarget(null);
      setAdjustAmount("");
      setAdjustNote("");
      await load();
    } catch (err) {
      alert(err instanceof Error ? err.message : "调整失败");
    }
  }

  async function genCodes() {
    const value = parseInt(codeValue, 10);
    const count = parseInt(codeCount, 10);
    if (!value || !count) return;
    try {
      const created = await api.adminGenCodes(value, count);
      setMessage(`已生成 ${created.length} 个兑换码`);
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "生成失败");
    }
  }

  function copyCode(code: string) {
    navigator.clipboard.writeText(code).then(() => setMessage(`已复制 ${code}`));
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold text-white">管理后台</h1>
        <div className="ml-4 flex flex-wrap rounded-xl bg-ink-800 p-1 text-sm">
          {(
            [
              ["overview", "概览"],
              ["users", "用户管理"],
              ["codes", "兑换码"],
              ["tasks", "任务监控"],
              ["workers", "Worker 池"],
            ] as [Tab, string][]
          ).map(([t, label]) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-lg px-4 py-1.5 transition ${
                tab === t ? "bg-white/[0.08] text-white" : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <button onClick={load} className="btn-ghost ml-auto !px-3 !py-1.5 text-xs">
          <RefreshCw size={13} /> 刷新
        </button>
      </div>

      {message && <p className="text-sm text-emerald-400">{message}</p>}

      {/* ---- 概览 ---- */}
      {tab === "overview" && stats && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["注册用户", stats.user_count],
            ["今日任务", stats.today_tasks],
            ["任务总数", stats.task_count],
            ["累计消耗积分", stats.credits_consumed],
            ["排队中", stats.queued],
            ["执行中", stats.running],
            ["已完成", stats.done],
            ["已失败", stats.failed],
          ].map(([label, value]) => (
            <div key={label as string} className="panel p-5">
              <p className="text-xs text-zinc-500">{label}</p>
              <p className="mt-1.5 text-2xl font-semibold text-white">{value}</p>
            </div>
          ))}
        </div>
      )}

      {/* ---- 用户管理 ---- */}
      {tab === "users" && (
        <div className="panel overflow-x-auto p-5">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-white">
            <Users size={15} className="text-indigo-400" /> 用户列表
          </h3>
          <table className="w-full min-w-[700px] text-sm">
            <thead>
              <tr className="border-b border-white/[0.06] text-left text-xs text-zinc-600">
                <th className="pb-2 font-normal">ID</th>
                <th className="pb-2 font-normal">邮箱</th>
                <th className="pb-2 font-normal">昵称</th>
                <th className="pb-2 font-normal">角色</th>
                <th className="pb-2 font-normal">积分</th>
                <th className="pb-2 font-normal">任务数</th>
                <th className="pb-2 font-normal">注册时间</th>
                <th className="pb-2 font-normal">操作</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-b border-white/[0.04] last:border-0">
                  <td className="py-2.5 text-zinc-500">{u.id}</td>
                  <td className="py-2.5 text-zinc-300">{u.email}</td>
                  <td className="py-2.5 text-zinc-400">{u.username}</td>
                  <td className="py-2.5">
                    {u.role === "admin" ? (
                      <span className="text-violet-400">管理员</span>
                    ) : (
                      <span className="text-zinc-500">用户</span>
                    )}
                  </td>
                  <td className="py-2.5 text-indigo-300">{u.credits}</td>
                  <td className="py-2.5 text-zinc-400">{u.task_count}</td>
                  <td className="py-2.5 text-xs text-zinc-500">{fmtTime(u.created_at)}</td>
                  <td className="py-2.5">
                    {u.role !== "admin" && (
                      <button
                        className="btn-ghost !px-3 !py-1 text-xs"
                        onClick={() => setAdjustTarget(u)}
                      >
                        调整积分
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ---- 兑换码 ---- */}
      {tab === "codes" && (
        <div className="space-y-4">
          <div className="panel p-5">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-white">
              <Ticket size={15} className="text-violet-400" /> 生成兑换码
            </h3>
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <p className="mb-1.5 text-xs text-zinc-500">面值（积分）</p>
                <select className="input w-32" value={codeValue} onChange={(e) => setCodeValue(e.target.value)}>
                  {[50, 100, 500, 2000].map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <p className="mb-1.5 text-xs text-zinc-500">数量</p>
                <input
                  className="input w-24"
                  type="number"
                  min={1}
                  max={100}
                  value={codeCount}
                  onChange={(e) => setCodeCount(e.target.value)}
                />
              </div>
              <button className="btn-primary" onClick={genCodes}>
                生成
              </button>
            </div>
          </div>

          <div className="panel overflow-x-auto p-5">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-b border-white/[0.06] text-left text-xs text-zinc-600">
                  <th className="pb-2 font-normal">兑换码</th>
                  <th className="pb-2 font-normal">面值</th>
                  <th className="pb-2 font-normal">状态</th>
                  <th className="pb-2 font-normal">使用人</th>
                  <th className="pb-2 font-normal">生成时间</th>
                  <th className="pb-2 font-normal">操作</th>
                </tr>
              </thead>
              <tbody>
                {codes.map((c) => (
                  <tr key={c.id} className="border-b border-white/[0.04] last:border-0">
                    <td className="py-2.5 font-mono text-xs tracking-wider text-zinc-200">{c.code}</td>
                    <td className="py-2.5 text-indigo-300">{c.value}</td>
                    <td className="py-2.5">
                      {c.status === "unused" ? (
                        <span className="text-emerald-400">未使用</span>
                      ) : (
                        <span className="text-zinc-500">已使用</span>
                      )}
                    </td>
                    <td className="py-2.5 text-xs text-zinc-500">
                      {c.used_by_email ?? "-"}
                      {c.used_at && ` (${fmtTime(c.used_at)})`}
                    </td>
                    <td className="py-2.5 text-xs text-zinc-500">{fmtTime(c.created_at)}</td>
                    <td className="py-2.5">
                      {c.status === "unused" && (
                        <button
                          className="rounded-lg p-1.5 text-zinc-400 hover:bg-white/5 hover:text-white"
                          onClick={() => copyCode(c.code)}
                          title="复制"
                        >
                          <Copy size={14} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {codes.length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-8 text-center text-sm text-zinc-600">
                      暂无兑换码
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ---- 任务监控 ---- */}
      {tab === "tasks" && (
        <div className="panel overflow-x-auto p-5">
          <table className="w-full min-w-[800px] text-sm">
            <thead>
              <tr className="border-b border-white/[0.06] text-left text-xs text-zinc-600">
                <th className="pb-2 font-normal">ID</th>
                <th className="pb-2 font-normal">用户</th>
                <th className="pb-2 font-normal">模式</th>
                <th className="pb-2 font-normal">提示词</th>
                <th className="pb-2 font-normal">配置</th>
                <th className="pb-2 font-normal">状态</th>
                <th className="pb-2 font-normal">积分</th>
                <th className="pb-2 font-normal">创建时间</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((t) => (
                <tr key={t.id} className="border-b border-white/[0.04] align-top last:border-0">
                  <td className="py-2.5 text-zinc-500">#{t.id}</td>
                  <td className="py-2.5 text-xs text-zinc-400">{t.user_email ?? "-"}</td>
                  <td className="py-2.5 text-zinc-300">{MODE_LABEL[t.mode]}</td>
                  <td className="max-w-[220px] truncate py-2.5 text-xs text-zinc-500" title={t.prompt}>
                    {t.prompt}
                  </td>
                  <td className="whitespace-nowrap py-2.5 text-xs text-zinc-500">
                    {t.aspect_ratio} · {t.duration}s · {t.resolution}
                  </td>
                  <td className="py-2.5">
                    <span
                      className={
                        t.status === "done"
                          ? "text-emerald-400"
                          : t.status === "failed"
                            ? "text-rose-400"
                            : "text-indigo-300"
                      }
                      title={t.error}
                    >
                      {STATUS_LABEL[t.status]}
                    </span>
                  </td>
                  <td className="py-2.5 text-zinc-300">{t.cost || "-"}</td>
                  <td className="whitespace-nowrap py-2.5 text-xs text-zinc-500">
                    {fmtTime(t.created_at)}
                  </td>
                </tr>
              ))}
              {tasks.length === 0 && (
                <tr>
                  <td colSpan={8} className="py-8 text-center text-sm text-zinc-600">
                    暂无任务
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* ---- Worker 池监控 ---- */}
      {tab === "workers" && workerPool && (
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            {[
              ["排队/增强中", workerPool.queued],
              ["生成阶段", workerPool.generating],
              ["超分阶段", workerPool.upscaling],
            ].map(([label, value]) => (
              <div key={label as string} className="panel p-5">
                <p className="text-xs text-zinc-500">{label}</p>
                <p className="mt-1.5 text-2xl font-semibold text-white">{value}</p>
              </div>
            ))}
          </div>

          <div className="panel overflow-x-auto p-5">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-white">
              <Cpu size={15} className="text-indigo-400" /> ComfyUI 节点
              {workerPool.mock && <span className="text-xs font-normal text-amber-400">Mock 模式</span>}
              <span className="ml-auto text-xs font-normal text-zinc-600">每 5 秒自动刷新</span>
            </h3>
            <table className="w-full min-w-[700px] text-sm">
              <thead>
                <tr className="border-b border-white/[0.06] text-left text-xs text-zinc-600">
                  <th className="pb-2 font-normal">节点</th>
                  <th className="pb-2 font-normal">角色</th>
                  <th className="pb-2 font-normal">标签</th>
                  <th className="pb-2 font-normal">健康</th>
                  <th className="pb-2 font-normal">状态</th>
                  <th className="pb-2 font-normal">当前任务</th>
                </tr>
              </thead>
              <tbody>
                {workerPool.workers.map((w) => (
                  <tr key={w.url} className="border-b border-white/[0.04] last:border-0">
                    <td className="py-2.5 font-mono text-xs text-zinc-300">{w.url}</td>
                    <td className="py-2.5">
                      <span className={w.role === "generate" ? "text-indigo-300" : "text-violet-300"}>
                        {w.role === "generate" ? "生成" : "超分"}
                      </span>
                    </td>
                    <td className="py-2.5 text-xs text-zinc-500">{w.tags.join(", ") || "-"}</td>
                    <td className="py-2.5">
                      <span className={w.healthy ? "text-emerald-400" : "text-rose-400"}>
                        {w.healthy ? "在线" : `已摘除(${w.consecutive_fails})`}
                      </span>
                    </td>
                    <td className="py-2.5 text-zinc-400">{w.busy ? "忙碌" : "空闲"}</td>
                    <td className="py-2.5 text-xs text-zinc-500">{w.task_id ? `#${w.task_id}` : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ---- 调整积分弹窗 ---- */}
      {adjustTarget && (
        <div
          className="fixed inset-0 z-30 flex items-center justify-center bg-black/60 px-4"
          onClick={() => setAdjustTarget(null)}
        >
          <div className="panel w-full max-w-sm p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-1 text-base font-semibold text-white">调整积分</h3>
            <p className="mb-4 text-xs text-zinc-500">
              {adjustTarget.email} · 当前余额 {adjustTarget.credits}
            </p>
            <input
              className="input mb-3"
              type="number"
              placeholder="正数为充值，负数为扣减"
              value={adjustAmount}
              onChange={(e) => setAdjustAmount(e.target.value)}
            />
            <input
              className="input mb-4"
              placeholder="备注（可选）"
              value={adjustNote}
              onChange={(e) => setAdjustNote(e.target.value)}
            />
            <div className="flex justify-end gap-2">
              <button className="btn-ghost" onClick={() => setAdjustTarget(null)}>
                取消
              </button>
              <button className="btn-primary" onClick={doAdjust}>
                确认调整
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
