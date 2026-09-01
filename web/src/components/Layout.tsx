/** 页面骨架：顶部导航 + 内容区。 */
import { Clapperboard, Coins, ListChecks, LogOut, ShieldCheck } from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../state/auth";

function NavItem({ to, icon, label }: { to: string; icon: React.ReactNode; label: string }) {
  return (
    <NavLink
      to={to}
      end={to === "/"}
      className={({ isActive }) =>
        `inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition ${
          isActive
            ? "bg-white/[0.08] text-white"
            : "text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-200"
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
      <header className="sticky top-0 z-20 border-b border-white/[0.06] bg-ink-950/80 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4">
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-500 text-sm font-bold text-white">
              H3
            </div>
            <span className="text-sm font-semibold tracking-wide text-white">H3 Studio</span>
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
              className="inline-flex items-center gap-1.5 rounded-full border border-indigo-400/30 bg-indigo-500/10 px-3 py-1 text-sm text-indigo-300 transition hover:bg-indigo-500/20"
            >
              <Coins size={14} />
              {user?.credits ?? 0}
            </NavLink>
            <span className="hidden max-w-[160px] truncate text-sm text-zinc-400 sm:block">
              {user?.email}
            </span>
            <button
              className="rounded-lg p-1.5 text-zinc-400 transition hover:bg-white/[0.06] hover:text-zinc-200"
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

      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">
        <Outlet />
      </main>

      <footer className="border-t border-white/[0.05] py-4 text-center text-xs text-zinc-600">
        H3 Studio · Powered by MiniMax H3 + ComfyUI
      </footer>
    </div>
  );
}
