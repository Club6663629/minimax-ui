/** 登录 / 注册页。
 *  用户协议 V2（体验优先）：注册页 1 个合并勾选框（默认不勾）。
 *  未勾选时主按钮**仍可点**，点提交在勾选行下方给一行红字提示（对齐海螺「请先同意条款」），
 *  不置灰、不弹窗、不阻断；注册成功记录本机已同意版本。
 *  登录页仅在按钮下方给一行弱提示，不勾选、不弹窗、不阻断。 */
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import BrandMark from "../components/BrandMark";
import { LEGAL_LINKS, LEGAL_SEEN_KEY, LEGAL_VERSION } from "../legal";
import { useAuth } from "../state/auth";

function LegalA({ k, className = "" }: { k: string; className?: string }) {
  const l = LEGAL_LINKS[k];
  return (
    <a
      href={l.href}
      target="_blank"
      rel="noopener"
      className={`text-primary-600 hover:underline ${className}`}
    >
      《{l.title}》
    </a>
  );
}

export default function AuthPage() {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const agreeRef = useRef<HTMLInputElement>(null);

  const needAgree = mode === "register" && !agreed;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (needAgree) {
      setError("请先阅读并同意《用户协议》和《隐私政策》");
      agreeRef.current?.focus();
      return;
    }
    setBusy(true);
    try {
      if (mode === "login") {
        await login(email.trim(), password);
      } else {
        await register(email.trim(), username.trim(), password, {
          ua: LEGAL_VERSION,
          pp: LEGAL_VERSION,
          ai: LEGAL_VERSION,
        });
      }
      if (mode === "register" && agreed) {
        // 新用户已在注册页勾选同意当前版本 → 记录本机已同意版本，首屏不再弹"协议更新"横幅
        localStorage.setItem(LEGAL_SEEN_KEY, LEGAL_VERSION);
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

          {mode === "register" && (
            <label className="flex cursor-pointer items-start gap-2 text-xs leading-relaxed text-foreground-600">
              <input
                type="checkbox"
                ref={agreeRef}
                checked={agreed}
                onChange={(e) => setAgreed(e.target.checked)}
                className="mt-0.5 h-4 w-4 shrink-0 accent-primary-600"
              />
              <span>
                我已阅读并同意
                <LegalA k="ua" className="mx-0.5" />
                <LegalA k="pp" />
                <LegalA k="ai" />
              </span>
            </label>
          )}

          {error && <p className="text-sm text-rose-600">{error}</p>}
          <button className="btn-primary w-full" disabled={busy}>
            {busy ? "处理中…" : mode === "login" ? "登录" : "注册并领取试用积分"}
          </button>
        </form>

        {mode === "login" ? (
          <p className="mt-4 text-center text-xs text-foreground-500">
            登录即表示你同意
            <LegalA k="ua" className="mx-1" />
            和
            <LegalA k="pp" className="ml-1" />
          </p>
        ) : (
          <p className="mt-4 text-center text-xs text-foreground-500">
            新用户注册即赠试用积分，立即体验视频生成
          </p>
        )}
      </div>
    </div>
  );
}
