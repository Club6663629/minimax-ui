/** 短剧工作台外壳：暗金 rail + topbar + stage；与海白菜浅色布局完全隔离。 */
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Film,
  FolderOpen,
  History,
  Home,
  Image,
  LayoutGrid,
  ListTree,
  ScrollText,
  Settings,
  Users,
} from "lucide-react";
import { useHibtv } from "./state";
import "./hibtv.css";

const NAV = [
  { key: "home", label: "首页", to: "/hibtv/home", icon: Home },
  { key: "script", label: "剧本", to: "/hibtv/script/1", icon: ScrollText },
  { key: "cast", label: "角色", to: "/hibtv/cast", icon: Users },
  { key: "art", label: "美术", to: "/hibtv/art", icon: Image },
  { key: "board", label: "分镜", to: "/hibtv/storyboard/1", icon: LayoutGrid },
  { key: "overview", label: "概览", to: "/hibtv/overview", icon: ListTree },
  { key: "outline", label: "大纲", to: "/hibtv/outline/inner", icon: Film },
  { key: "changes", label: "变更", to: "/hibtv/changes", icon: History },
  { key: "assets", label: "素材库", to: "/hibtv/assets", icon: FolderOpen },
  { key: "settings", label: "设置", to: "/hibtv/settings", icon: Settings },
];

const TITLES: Record<string, string> = {
  home: "工作台",
  outline: "剧本大纲",
  cast: "角色",
  art: "美术设定",
  script: "剧本",
  storyboard: "分镜",
  video: "出视频",
  overview: "项目概览",
  changes: "变更",
  assets: "素材库",
  settings: "项目设置",
};

/** 页面骨架：side / main / panel 三段；缺哪段就把 stage 标成 no-xxx。 */
export function Stage({
  side,
  panel,
  children,
}: {
  side?: ReactNode;
  panel?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={`stage ${side ? "" : "no-side"} ${panel ? "" : "no-panel"}`.trim()}>
      <aside className="side">{side}</aside>
      <main className="main">{children}</main>
      <aside className="panel">{panel}</aside>
    </div>
  );
}

export default function HibtvLayout() {
  const { project, activeJobs, setRatio } = useHibtv();
  const location = useLocation();
  const navigate = useNavigate();
  const [queueOpen, setQueueOpen] = useState(false);

  const parts = location.pathname.replace(/^\/hibtv\/?/, "").split("/").filter(Boolean);
  const name = parts[0] || "home";
  const ratio = project?.aspectRatio || "9:16";
  const running = activeJobs.length;

  useEffect(() => {
    document.title = project ? `${project.name} · HiBTV` : "HiBTV · 短剧工作台";
  }, [project]);

  return (
    <div className="hibtv-root">
      <div className="app">
        <aside className="rail">
          <div className="logo">HiB</div>
          <div className="rail-list">
            {NAV.map((n) => {
              const cur = name === n.key || (n.key === "board" && name === "video");
              const Icon = n.icon;
              return (
                <button
                  key={n.key}
                  className={`rail-item ${cur ? "on" : ""}`}
                  title={n.label}
                  onClick={() => navigate(n.to)}
                >
                  <Icon size={17} strokeWidth={1.4} />
                  <span>{n.label}</span>
                </button>
              );
            })}
          </div>
          <div className="rail-foot">
            <div className="rail-sep" />
            <Link className="rail-item" to="/" title="返回海白菜">
              <ArrowLeft size={17} strokeWidth={1.4} />
              <span>海白菜</span>
            </Link>
            <button className="rail-item" title="项目列表" onClick={() => navigate("/hibtv/home")}>
              <FolderOpen size={17} strokeWidth={1.4} />
              <span>项目</span>
            </button>
            <button className="rail-item" title="设置" onClick={() => navigate("/hibtv/settings")}>
              <Settings size={17} strokeWidth={1.4} />
              <span>设置</span>
            </button>
          </div>
        </aside>

        <div className="col">
          <header className="topbar">
            <div className="crumb">
              <span>工作台</span>
              <span className="sep">›</span>
              <span className="prj" onClick={() => navigate("/hibtv/home")}>
                <b>{project?.name || "未选择项目"}</b> <span className="sep">⌄</span>
              </span>
              {name !== "home" && (
                <>
                  <span className="sep">›</span>
                  <span>{TITLES[name] || ""}</span>
                </>
              )}
            </div>
            <div className="top-right">
              <div className="seg" title="画面比例">
                <span
                  className={`s ${ratio === "9:16" ? "on" : ""}`}
                  onClick={() => void setRatio("9:16")}
                >
                  9:16
                </span>
                <span
                  className={`s ${ratio === "16:9" ? "on" : ""}`}
                  onClick={() => void setRatio("16:9")}
                >
                  16:9
                </span>
              </div>
              <button className="tbtn" onClick={() => navigate("/hibtv/assets")}>
                <span className="ico">▢</span>资产库
              </button>
              <button className="tbtn" onClick={() => setQueueOpen((v) => !v)}>
                <span className="ico">≡</span>生成队列{running ? <span className="q">{running}</span> : null}
              </button>
            </div>
          </header>

          <Outlet />

          {queueOpen && (
            <div className="card" style={{ position: "absolute", right: 16, top: 54, width: 380, zIndex: 30 }}>
              <div className="card-hd">
                <span className="t">生成队列</span>
                <span className="chip">{activeJobs.length} 进行中</span>
                <span style={{ marginLeft: "auto" }} className="x" onClick={() => setQueueOpen(false)}>
                  ✕
                </span>
              </div>
              <div className="card-bd">
                {activeJobs.length === 0 && <div className="tiny dim">队列空闲。</div>}
                {activeJobs.map((j) => (
                  <div key={j.id} className="inline" style={{ padding: "4px 0" }}>
                    <span className="chip gold">{j.kind}</span>
                    <span className="tiny">{j.stage || "排队中"}</span>
                    <span className="mono tiny dim" style={{ marginLeft: "auto" }}>
                      {j.progress || 0}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
