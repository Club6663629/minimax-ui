/** 页面骨架：顶部导航（海白菜品牌）+ 协议更新横幅 + 内容区 + 页脚常驻法律文本入口。 */
import { Clapperboard, Coins, ListChecks, LogOut, ShieldCheck } from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import BrandLogo from "./BrandLogo";
import LegalNotice from "./LegalNotice";
import { FOOTER_LINKS, LEGAL_UPDATED, LEGAL_VERSION } from "../legal";
import { useAuth } from "../state/auth";

function NavItem({ to, icon, label }: { to: string; icon: React.ReactNode; label: string }) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        `inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition ${
          isActive
            ? "bg-primary-100 text-primary-700"
            : "text-foreground-600 hover:bg-background-200 hover:text-foreground-900"
        }`
      }
    >
      {icon}
      {label}
    </NavLink>
  );
}

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-20 border-b border-background-200 bg-background-50/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4">
          <div className="flex shrink-0 items-center gap-2">
            <BrandLogo size={34} />
          </div>

          <nav className="flex items-center gap-1">
            <NavItem to="/" icon={<Clapperboard size={15} />} label="创作" />
            <NavItem to="/recharge" icon={<Coins size={15} />} label="充值" />
            <NavItem to="/ledger" icon={<ListChecks size={15} />} label="台账" />
            {user?.role === "admin" && (
              <NavItem to="/admin" icon={<ShieldCheck size={15} />} label="管理" />
            )}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <NavLink
              to="/recharge"
              className="inline-flex items-center gap-1.5 rounded-full border border-primary-300/40 bg-primary-100 px-3 py-1 text-sm text-primary-700 transition hover:bg-primary-200"
            >
              <Coins size={14} />
              {user?.credits ?? 0}
            </NavLink>
            <span className="hidden max-w-[160px] truncate text-sm text-foreground-500 sm:block">
              {user?.email}
            </span>
            <button
              className="rounded-lg p-1.5 text-foreground-500 transition hover:bg-background-200 hover:text-foreground-900"
              title="退出登录"
              onClick={() => {
                logout();
                navigate("/login");
              }}
            >
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </header>

      <LegalNotice />

      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">
        <Outlet />
      </main>

      <footer className="border-t border-background-200 py-5 text-center text-xs text-foreground-500">
        <div className="mb-2 flex flex-wrap items-center justify-center gap-x-4 gap-y-1">
          {FOOTER_LINKS.map((l) => (
            <a
              key={l.key}
              href={l.href}
              target="_blank"
              rel="noopener"
              className="transition hover:text-foreground-800 hover:underline"
            >
              {l.title}
            </a>
          ))}
        </div>
        <p>海白菜 · Powered by MiniMax H3 + ComfyUI</p>
        <p className="mt-1">
          版本 {LEGAL_VERSION} · 更新日期 {LEGAL_UPDATED}
        </p>
      </footer>
    </div>
  );
}
