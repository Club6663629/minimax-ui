/** 充值页：余额、套餐卡片、兑换码、积分记录。 */
import { Check, Coins, Ticket } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../state/auth";
import type { CreditLog, Pricing } from "../types";

const LOG_LABEL: Record<CreditLog["type"], string> = {
  signup: "注册赠送",
  consume: "生成消耗",
  refund: "失败退还",
  redeem: "兑换充值",
  adjust: "管理员调整",
};

export default function RechargePage() {
  const { user, refreshUser } = useAuth();
  const [pricing, setPricing] = useState<Pricing | null>(null);
  const [logs, setLogs] = useState<CreditLog[]>([]);
  const [code, setCode] = useState("");
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.pricing().then(setPricing).catch(() => {});
    api.creditLogs().then(setLogs).catch(() => {});
  }, []);

  async function redeem() {
    if (!code.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.redeem(code.trim());
      setMessage({ ok: true, text: `充值成功，到账 ${result.added} 积分` });
      setCode("");
      await Promise.all([refreshUser(), api.creditLogs().then(setLogs)]);
    } catch (err) {
      setMessage({ ok: false, text: err instanceof Error ? err.message : "兑换失败" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      {/* 余额 */}
      <div className="panel flex items-center justify-between p-6">
        <div>
          <p className="text-sm text-foreground-500">当前积分余额</p>
          <p className="mt-1 flex items-center gap-2 text-4xl font-semibold text-foreground-950">
            <Coins className="text-primary-500" size={30} />
            {user?.credits ?? 0}
          </p>
        </div>
        <div className="text-right text-xs leading-relaxed text-foreground-500">
          <p>768p·5s 消耗 {pricing?.cost_768p_5s ?? "-"} 积分</p>
          <p>768p·10s 消耗 {pricing?.cost_768p_10s ?? "-"} 积分</p>
          <p>1K 升级附加 {pricing?.cost_1k_extra ?? "-"} 积分</p>
          <p>2K 升级附加 {pricing?.cost_2k_extra ?? "-"} 积分</p>
        </div>
      </div>

      {/* 套餐 */}
      <div>
        <h2 className="mb-3 text-base font-semibold text-foreground-950">充值套餐</h2>
        <div className="grid gap-4 sm:grid-cols-3">
          {(pricing?.packages ?? []).map((p) => (
            <div
              key={p.name}
              className={`panel relative p-5 ${p.tag ? "border-primary-400/50" : ""}`}
            >
              {p.tag && (
                <span className="absolute -top-2.5 right-4 rounded-full bg-primary-500 px-2.5 py-0.5 text-xs text-background-50">
                  {p.tag}
                </span>
              )}
              <p className="text-sm text-foreground-600">{p.name}</p>
              <p className="mt-2 text-3xl font-semibold text-foreground-950">
                {p.credits}
                <span className="ml-1 text-sm font-normal text-foreground-500">积分</span>
              </p>
              <p className="mt-1 text-lg text-primary-600">{p.price}</p>
              <p className="mt-2 text-xs text-foreground-500">{p.description}</p>
              <div className="mt-3 space-y-1 text-xs text-foreground-500">
                <p className="flex items-center gap-1.5">
                  <Check size={12} className="text-emerald-600" /> 含全部创作模式
                </p>
                <p className="flex items-center gap-1.5">
                  <Check size={12} className="text-emerald-600" /> 失败自动退还积分
                </p>
              </div>
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs text-foreground-500">
          * 当前版本通过兑换码充值：请联系管理员获取兑换码后在下方兑换。在线支付通道规划中。
        </p>
        <p className="mt-2 text-xs text-foreground-500">
          充值即表示同意
          <a
            href="/legal/credits-rules.html"
            target="_blank"
            rel="noopener"
            className="text-primary-600 hover:underline"
          >
            《积分与计费规则》
          </a>
        </p>
      </div>

      {/* 兑换码 */}
      <div className="panel p-5">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground-950">
          <Ticket size={15} className="text-accent-600" /> 兑换码充值
        </h3>
        <div className="flex gap-3">
          <input
            className="input flex-1 uppercase tracking-wider"
            placeholder="输入兑换码，如 H3XXXX-XXXX-XXXX"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && redeem()}
          />
          <button className="btn-primary shrink-0" onClick={redeem} disabled={busy || !code.trim()}>
            {busy ? "兑换中…" : "立即兑换"}
          </button>
        </div>
        {message && (
          <p className={`mt-2 text-sm ${message.ok ? "text-emerald-600" : "text-rose-600"}`}>
            {message.text}
          </p>
        )}
      </div>

      {/* 积分记录 */}
      <div className="panel p-5">
        <h3 className="mb-3 text-sm font-semibold text-foreground-950">积分记录</h3>
        {logs.length === 0 ? (
          <p className="py-6 text-center text-sm text-foreground-500">暂无记录</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-background-200 text-left text-xs text-foreground-500">
                <th className="pb-2 font-normal">时间</th>
                <th className="pb-2 font-normal">类型</th>
                <th className="pb-2 font-normal">说明</th>
                <th className="pb-2 text-right font-normal">积分变动</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((l) => (
                <tr key={l.id} className="border-b border-background-100 last:border-0">
                  <td className="py-2.5 text-xs text-foreground-500">
                    {new Date(l.created_at).toLocaleString("zh-CN", { hour12: false })}
                  </td>
                  <td className="py-2.5 text-foreground-800">{LOG_LABEL[l.type] ?? l.type}</td>
                  <td className="py-2.5 text-foreground-500">{l.note}</td>
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
    </div>
  );
}
