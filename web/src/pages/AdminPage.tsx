/** 管理后台：概览统计 / 用户管理 / 兑换码 / 任务监控 / Worker 池（仅管理员）。 */
import { Copy, Cpu, Power, PowerOff, RefreshCw, Ticket, Users } from "lucide-react";
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
  type WorkerInfo,
  type WorkerPoolOut,
} from "../types";

type Tab = "overview" | "users" | "codes" | "tasks" | "workers";

function fmtTime(s: string | null): string {
  if (!s) return "-";
  return new Date(s).toLocaleString("zh-CN", { hour12: false });
}

/** 标签列：长模型名折叠为「模型×N」，短标签（1k/2k/4k/engine:rtx 等）原样显示；完整值见 hover。 */
function compactTags(tags: string[]): string {
  if (!tags.length) return "-";
  const short = tags.filter((t) => t.length <= 24);
  const longCount = tags.length - short.length;
  return [...short, ...(longCount ? [`模型×${longCount}`] : [])].join(" · ");
}

/** 云开关机失败短因：命中错误码给中文短词，未命中则截取平台原文（12 字）。 */
function shortReason(code: string | null | undefined, msg: string): string {
  const MAP: Record<string, string> = {
    NO_STOCK: "无库存",
    AUTH_FAILED: "token失效",
    NOT_FOUND: "实例不存在",
    UPSTREAM_TIMEOUT: "平台超时",
    UPSTREAM_ERROR: "网络异常",
    BAD_REQUEST: "配置无效",
    NO_CREDENTIAL: "缺凭据",
    NOT_CLOUD: "非云节点",
    PLATFORM_UNSUPPORTED: "平台不支持",
  };
  if (code && MAP[code]) return MAP[code];
  const t = (msg || "").replace(/\s+/g, " ").trim();
  if (!t) return code ? "未知原因" : "";
  return t.length > 12 ? `${t.slice(0, 12)}…` : t;
}

/** 失败记录（本次会话内存 / 后端持久化 二选一）。 */
type PowerNote = { code?: string | null; msg: string; at: number };

// 失败原因展示 TTL：1 小时（过期不再显示；下次操作即覆盖）
const NOTE_TTL_MS = 3600_000;

/** 取当前应展示的失败记录：本地新失败优先，其次后端持久化（仅手动开关机、1h 内）。 */
function liveFail(w: WorkerInfo, note?: PowerNote): PowerNote | null {
  if (note) return note;
  if (w.last_op_ok !== false) return null;
  if (w.last_op_action !== "on" && w.last_op_action !== "off") return null;
  const at = (w.last_op_at ?? 0) * 1000;
  if (!at || Date.now() - at > NOTE_TTL_MS) return null;
  return { code: w.last_op_code, msg: w.last_op_msg || "", at };
}

/** 电源列状态 pill：紧凑中文状态；后端长句只作 hover tooltip，不再占表格空间。 */
function powerPill(
  w: WorkerInfo,
  busy: boolean,
  act: "" | "on" | "off",
  fail: PowerNote | null,
): { text: string; cls: string; title: string } {
  const AMBER = "bg-amber-500/15 text-amber-700 ring-amber-500/30";
  const GREEN = "bg-emerald-500/15 text-emerald-700 ring-emerald-500/30";
  const GREY = "bg-background-300/70 text-foreground-600 ring-background-400/40";
  const ROSE = "bg-rose-500/15 text-rose-700 ring-rose-500/30";
  if (fail) {
    const reason = shortReason(fail.code, fail.msg);
    const when = new Date(fail.at).toLocaleString("zh-CN", { hour12: false });
    const full = `${fail.msg || "未知错误"}${fail.code ? `（${fail.code}）` : ""}\n时间：${when}`;
    return { text: reason ? `失败·${reason}` : "失败", cls: ROSE, title: full };
  }
  if (busy) {
    return act === "on"
      ? { text: "开机中…", cls: AMBER, title: "开机指令已下发，云端启动中（就绪后自动接单）" }
      : { text: "关机中…", cls: AMBER, title: "关机指令已下发，节点约 30s 内自动摘牌" };
  }
  if (w.op_state === "starting") return { text: "开机中…", cls: AMBER, title: "开机指令已下发，云端启动中（就绪后自动接单）" };
  if (w.op_state === "stopping") return { text: "关机中…", cls: AMBER, title: "关机指令已下发，节点约 30s 内自动摘牌" };
  if (w.instance_status === "running") return { text: "运行中", cls: GREEN, title: `实例运行中：${w.instance_id ?? ""}` };
  if (w.instance_status === "shutdown") return { text: "已关机", cls: GREY, title: "实例已关机（开机后可重新入池接单）" };
  return { text: w.instance_status || "未知", cls: GREY, title: w.instance_id ?? "" };
}

export default function AdminPage() {
  const { user } = useAuth();
  const [tab, setTab] = useState<Tab>("overview");
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [codes, setCodes] = useState<RedeemCodeOut[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [workerPool, setWorkerPool] = useState<WorkerPoolOut | null>(null);
  // 云端实例开关机
  const [cloudBusy, setCloudBusy] = useState("");
  const [cloudMsg, setCloudMsg] = useState<Record<string, PowerNote>>({});
  const [confirmPower, setConfirmPower] = useState<WorkerInfo | null>(null);
  const [cloudAct, setCloudAct] = useState<"" | "on" | "off">("");

  // 用户积分调整
  const [adjustTarget, setAdjustTarget] = useState<AdminUser | null>(null);
  const [adjustAmount, setAdjustAmount] = useState("");
  const [adjustNote, setAdjustNote] = useState("");
  // 兑换码生成
  const [codeValue, setCodeValue] = useState("100");
  const [codeCount, setCodeCount] = useState("5");
  const [message, setMessage] = useState("");

  // ---- 云端实例开机 / 关机（手动；本次不做任务检测拦截）----
  const doCloudPower = async (w: WorkerInfo, action: "on" | "off") => {
    setCloudBusy(w.url);
    setCloudMsg((m) => Object.fromEntries(Object.entries(m).filter(([k]) => k !== w.url)));
    try {
      setCloudAct(action);
      await api.adminCloudPower(w.url, action);
      // 指令受理后只保留失败信息；进行中/完成由电源列 pill 依据 op_state/instance_status 呈现
      // 3s 后刷新一次池状态（开机后节点回池约 30s 内）
      setTimeout(() => {
        api.adminWorkers().then(setWorkerPool).catch(() => {});
      }, 3000);
    } catch (e) {
      const err = e as { message?: string; errorCode?: string };
      setCloudMsg((m) => ({
        ...m,
        [w.url]: { code: err.errorCode, msg: err.message || "未知错误", at: Date.now() },
      }));
    } finally {
      setCloudBusy("");
      setCloudAct("");
    }
  };

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
    return <p className="py-20 text-center text-sm text-foreground-500">需要管理员权限</p>;
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
        <h1 className="text-lg font-semibold text-foreground-950">管理后台</h1>
        <div className="ml-4 flex flex-wrap rounded-xl bg-background-200 p-1 text-sm">
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
                tab === t ? "bg-background-50 text-foreground-900 shadow-sm" : "text-foreground-500 hover:text-foreground-800"
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

      {message && <p className="text-sm text-emerald-600">{message}</p>}

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
              <p className="text-xs text-foreground-500">{label}</p>
              <p className="mt-1.5 text-2xl font-semibold text-foreground-950">{value}</p>
            </div>
          ))}
        </div>
      )}

      {/* ---- 用户管理 ---- */}
      {tab === "users" && (
        <div className="panel overflow-x-auto p-5">
          <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground-950">
            <Users size={15} className="text-primary-500" /> 用户列表
          </h3>
          <table className="w-full min-w-[700px] text-sm">
            <thead>
              <tr className="border-b border-background-200 text-left text-xs text-foreground-500">
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
                <tr key={u.id} className="border-b border-background-100 last:border-0">
                  <td className="py-2.5 text-foreground-500">{u.id}</td>
                  <td className="py-2.5 text-foreground-800">{u.email}</td>
                  <td className="py-2.5 text-foreground-600">{u.username}</td>
                  <td className="py-2.5">
                    {u.role === "admin" ? (
                      <span className="text-accent-600">管理员</span>
                    ) : (
                      <span className="text-foreground-500">用户</span>
                    )}
                  </td>
                  <td className="py-2.5 text-primary-600">{u.credits}</td>
                  <td className="py-2.5 text-foreground-600">{u.task_count}</td>
                  <td className="py-2.5 text-xs text-foreground-500">{fmtTime(u.created_at)}</td>
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
            <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground-950">
              <Ticket size={15} className="text-accent-600" /> 生成兑换码
            </h3>
            <div className="flex flex-wrap items-end gap-3">
              <div>
                <p className="mb-1.5 text-xs text-foreground-500">面值（积分）</p>
                <select className="input w-32" value={codeValue} onChange={(e) => setCodeValue(e.target.value)}>
                  {[50, 100, 500, 2000].map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <p className="mb-1.5 text-xs text-foreground-500">数量</p>
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
                <tr className="border-b border-background-200 text-left text-xs text-foreground-500">
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
                  <tr key={c.id} className="border-b border-background-100 last:border-0">
                    <td className="py-2.5 font-mono text-xs tracking-wider text-foreground-900">{c.code}</td>
                    <td className="py-2.5 text-primary-600">{c.value}</td>
                    <td className="py-2.5">
                      {c.status === "unused" ? (
                        <span className="text-emerald-600">未使用</span>
                      ) : (
                        <span className="text-foreground-500">已使用</span>
                      )}
                    </td>
                    <td className="py-2.5 text-xs text-foreground-500">
                      {c.used_by_email ?? "-"}
                      {c.used_at && ` (${fmtTime(c.used_at)})`}
                    </td>
                    <td className="py-2.5 text-xs text-foreground-500">{fmtTime(c.created_at)}</td>
                    <td className="py-2.5">
                      {c.status === "unused" && (
                        <button
                          className="rounded-lg p-1.5 text-foreground-600 hover:bg-background-200 hover:text-foreground-900"
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
                    <td colSpan={6} className="py-8 text-center text-sm text-foreground-500">
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
              <tr className="border-b border-background-200 text-left text-xs text-foreground-500">
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
                <tr key={t.id} className="border-b border-background-100 align-top last:border-0">
                  <td className="py-2.5 text-foreground-500">#{t.id}</td>
                  <td className="py-2.5 text-xs text-foreground-600">{t.user_email ?? "-"}</td>
                  <td className="py-2.5 text-foreground-800">{MODE_LABEL[t.mode]}</td>
                  <td className="max-w-[220px] truncate py-2.5 text-xs text-foreground-500" title={t.prompt}>
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
                      title={t.error}
                    >
                      {STATUS_LABEL[t.status]}
                    </span>
                  </td>
                  <td className="py-2.5 text-foreground-800">{t.cost || "-"}</td>
                  <td className="whitespace-nowrap py-2.5 text-xs text-foreground-500">
                    {fmtTime(t.created_at)}
                  </td>
                </tr>
              ))}
              {tasks.length === 0 && (
                <tr>
                  <td colSpan={8} className="py-8 text-center text-sm text-foreground-500">
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
                <p className="text-xs text-foreground-500">{label}</p>
                <p className="mt-1.5 text-2xl font-semibold text-foreground-950">{value}</p>
              </div>
            ))}
          </div>

          <div className="panel overflow-x-auto p-5">
            <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground-950">
              <Cpu size={15} className="text-primary-500" /> ComfyUI 节点
              {workerPool.mock && <span className="text-xs font-normal text-amber-600">Mock 模式</span>}
              <span className="ml-auto text-xs font-normal text-foreground-500">每 5 秒自动刷新</span>
            </h3>
            <table className="w-full min-w-[560px] text-sm">
              <thead>
                <tr className="border-b border-background-200 text-left text-xs text-foreground-500">
                  <th className="pb-2 pr-3 font-normal">节点</th>
                  <th className="pb-2 pr-3 font-normal">角色</th>
                  <th className="pb-2 pr-3 font-normal">标签</th>
                  <th className="pb-2 pr-3 font-normal">状态</th>
                  <th className="pb-2 pr-3 font-normal">当前任务</th>
                  <th className="pb-2 font-normal">电源</th>
                </tr>
              </thead>
              <tbody>
                {workerPool.workers.map((w) => (
                  <tr key={w.url} className="border-b border-background-100 last:border-0">
                    <td className="py-2.5 pr-3">
                      <span className="flex items-center gap-1.5 whitespace-nowrap">
                        <span className="font-mono text-xs text-foreground-800" title={w.url}>
                          {w.url.replace(/^https?:\/\//, "")}
                        </span>
                        {w.platform && (
                          <span
                            className="rounded bg-sky-500/15 px-1.5 py-0.5 text-[10px] leading-none text-sky-700 ring-1 ring-sky-500/30"
                            title={w.instance_id ?? w.platform}
                          >
                            {w.platform}
                          </span>
                        )}
                      </span>
                    </td>
                    <td className="py-2.5 pr-3">
                      <span className={w.role === "generate" ? "text-primary-600" : "text-accent-600"}>
                        {w.role === "generate" ? "生成" : "超分"}
                      </span>
                    </td>
                    <td className="py-2.5 pr-3 text-xs text-foreground-500" title={w.tags.join("\n")}>
                      {compactTags(w.tags)}
                    </td>
                    <td className="py-2.5 pr-3 text-xs whitespace-nowrap">
                      {!w.healthy ? (
                        <span className="text-rose-600" title={`连续失败 ${w.consecutive_fails} 次，已从池中摘除`}>
                          ⊘ 离线·{w.consecutive_fails}
                        </span>
                      ) : w.busy ? (
                        <span className="text-amber-600" title="正在执行任务">
                          ● 忙碌
                        </span>
                      ) : (
                        <span className="text-emerald-600" title="空闲，可接单">
                          ● 空闲
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 text-xs text-foreground-500">{w.task_id ? `#${w.task_id}` : "-"}</td>
                    <td className="py-2.5">
                      {w.power_controllable ? (
                        (() => {
                          const p = powerPill(w, cloudBusy === w.url, cloudAct, liveFail(w, cloudMsg[w.url]));
                          return (
                            <div className="flex items-center gap-1.5 whitespace-nowrap">
                              <span
                                className={`rounded px-1.5 py-0.5 text-[11px] leading-none ring-1 ${p.cls}`}
                                title={p.title}
                              >
                                {p.text}
                              </span>
                              <button
                                className="btn-ghost !px-2 !py-0.5 text-xs"
                                title="开机：云端启动并自动拉起 ComfyUI，就绪后作为 Worker 接单"
                                disabled={!!cloudBusy || w.instance_status === "running"}
                                onClick={() => doCloudPower(w, "on")}
                              >
                                <Power size={12} /> 开机
                              </button>
                              <button
                                className="btn-ghost !px-2 !py-0.5 text-xs"
                                title="关机：实例退出 Worker 池"
                                disabled={!!cloudBusy || w.instance_status === "shutdown"}
                                onClick={() => setConfirmPower(w)}
                              >
                                <PowerOff size={12} /> 关机
                              </button>
                            </div>
                          );
                        })()
                      ) : (
                        <span className="text-xs text-foreground-500">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ---- 云端实例关机二次确认 ---- */}
      {confirmPower && (
        <div
          className="fixed inset-0 z-30 flex items-center justify-center bg-black/60 px-4"
          onClick={() => setConfirmPower(null)}
        >
          <div className="panel w-full max-w-sm p-6" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-1 text-base font-semibold text-foreground-950">确认关闭云端实例？</h3>
            <p className="mb-4 text-xs text-foreground-500">
              {confirmPower.instance_id}（{confirmPower.url}）将关机并退出 Worker 池；
              若节点上仍有任务在执行，会被中断。
            </p>
            <div className="flex justify-end gap-2">
              <button className="btn-ghost" onClick={() => setConfirmPower(null)}>
                取消
              </button>
              <button
                className="btn-primary"
                onClick={() => {
                  const w = confirmPower;
                  setConfirmPower(null);
                  doCloudPower(w, "off");
                }}
              >
                确认关机
              </button>
            </div>
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
            <h3 className="mb-1 text-base font-semibold text-foreground-950">调整积分</h3>
            <p className="mb-4 text-xs text-foreground-500">
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
