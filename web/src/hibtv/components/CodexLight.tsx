/** codex 网关状态灯：亮绿=可用、黄=排队（有队列深度）、红=不可达（禁提）。
 *
 * 掉线时后端 `/ai/codex/status` 返 503，这里把原因（未配 token / 连不上 / 401）直说，
 * 因为**平台没有本地兜底**：网关不可达就是不可用，绝不假装生成成功。
 */
import { maskHost } from "../api";
import { useHibtv } from "../state";

export function CodexLight({ compact }: { compact?: boolean }) {
  const { codex, codexError, status, refreshCodex, toast } = useHibtv();
  const base = codex || status?.codex;
  const ok = Boolean(base?.available) && !codexError;
  const queue = Number(
    (codex?.stats as { running?: number; queued?: number } | undefined)?.queued ?? 0,
  );
  const color = !ok ? "red" : queue > 0 ? "gold" : "green";
  const label = !ok ? "codex 掉线" : queue > 0 ? `codex 排队 ${queue}` : "codex 可用";
  const note = [
    base?.url ? `网关 ${maskHost(base.url)}` : "",
    base?.hasToken === false ? "未配置 token 文件" : "",
    codex?.healthz?.codex ? `codex ${String(codex.healthz.codex)}` : "",
    codex?.skills?.length ? `${codex.skills.length} 个 skill` : "",
    codexError || base?.note || "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <span
      className={`chip ${color}`}
      title={note}
      style={{ cursor: "pointer", gap: 6 }}
      onClick={() => {
        void refreshCodex().then(() => {
          if (!codexError) toast("已重新自检 codex 网关");
        });
      }}
    >
      <i
        className="dot"
        style={{
          width: 7,
          height: 7,
          borderRadius: 4,
          display: "inline-block",
          background: color === "green" ? "#4ade80" : color === "gold" ? "#e0b341" : "#ef4444",
        }}
      />
      {compact ? "" : label}
    </span>
  );
}

/** 提交前的统一网关闸门：不可达就拦下并说明（红态禁用提交）。 */
export function useCodexGate(): { ok: boolean; why: string } {
  const { codex, codexError, status } = useHibtv();
  const base = codex || status?.codex;
  const ok = Boolean(base?.available) && !codexError;
  return {
    ok,
    why: ok
      ? ""
      : codexError ||
        base?.note ||
        "codex 网关不可达（本平台无本地兜底）：请先恢复网关再提文本阶段",
  };
}
