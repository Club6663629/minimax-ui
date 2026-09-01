/** 登录 / 注册页。 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../state/auth";

export default function AuthPage() {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (mode === "login") {
        await login(email.trim(), password);
      } else {
        await register(email.trim(), username.trim(), password);
      }
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden px-4">
      {/* 背景光斑 */}
      <div className="pointer-events-none absolute -top-40 left-1/4 h-96 w-96 rounded-full bg-indigo-600/20 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-40 right-1/4 h-96 w-96 rounded-full bg-violet-600/20 blur-3xl" />

      <div className="panel relative w-full max-w-md p-8">
        <div className="mb-8 text-center">
          <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-500 text-lg font-bold text-white">
            H3
          </div>
          <h1 className="text-xl font-semibold text-white">H3 Studio</h1>
          <p className="mt-1 text-sm text-zinc-500">MiniMax H3 · AI 视频创作平台</p>
        </div>

        <div className="mb-6 grid grid-cols-2 rounded-xl bg-ink-800 p-1 text-sm">
          {(["login", "register"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => {
                setMode(m);
                setError("");
              }}
              className={`rounded-lg py-2 transition ${
                mode === m ? "bg-white/[0.08] text-white" : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              {m === "login" ? "登录" : "注册"}
            </button>
          ))}
        </div>

        <form onSubmit={onSubmit} className="space-y-4">
          <input
            className="input"
            type="email"
            required
            placeholder="邮箱"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          {mode === "register" && (
            <input
              className="input"
              required
              minLength={2}
              maxLength={30}
              placeholder="昵称"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          )}
          <input
            className="input"
            type="password"
            required
            minLength={6}
            placeholder={mode === "register" ? "密码（至少 6 位）" : "密码"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          {error && <p className="text-sm text-rose-400">{error}</p>}
          <button className="btn-primary w-full" disabled={busy}>
            {busy ? "处理中…" : mode === "login" ? "登录" : "注册并领取试用积分"}
          </button>
        </form>

        {mode === "register" && (
          <p className="mt-4 text-center text-xs text-zinc-600">
            新用户注册即赠试用积分，立即体验视频生成
          </p>
        )}
      </div>
    </div>
  );
}
