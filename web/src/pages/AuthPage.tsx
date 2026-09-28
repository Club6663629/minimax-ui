/** 登录 / 注册页。 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import BrandMark from "../components/BrandMark";
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
      <div className="pointer-events-none absolute -top-40 left-1/4 h-96 w-96 rounded-full bg-primary-500/15 blur-3xl" />
      <div className="pointer-events-none absolute -bottom-40 right-1/4 h-96 w-96 rounded-full bg-accent-500/15 blur-3xl" />

      <div className="panel relative w-full max-w-md p-8">
        <div className="mb-8 text-center">
          <BrandMark size={56} className="mx-auto mb-3" />
          <h1 className="text-xl font-semibold text-foreground-950">海白菜</h1>
          <p className="mt-1 text-sm text-foreground-500">AI 视频创作平台</p>
        </div>

        <div className="mb-6 grid grid-cols-2 rounded-xl bg-background-200 p-1 text-sm">
          {(["login", "register"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => {
                setMode(m);
                setError("");
              }}
              className={`rounded-lg py-2 transition ${
                mode === m ? "bg-background-50 text-foreground-900 shadow-sm" : "text-foreground-500 hover:text-foreground-800"
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
          {error && <p className="text-sm text-rose-600">{error}</p>}
          <button className="btn-primary w-full" disabled={busy}>
            {busy ? "处理中…" : mode === "login" ? "登录" : "注册并领取试用积分"}
          </button>
        </form>

        {mode === "register" && (
          <p className="mt-4 text-center text-xs text-foreground-500">
            新用户注册即赠试用积分，立即体验视频生成
          </p>
        )}
      </div>
    </div>
  );
}
