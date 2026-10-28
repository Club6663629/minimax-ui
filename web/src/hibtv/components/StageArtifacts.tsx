/** 文本阶段产物抽屉：report.html 内嵌 / md 预览 / JSON 折叠树 / 下载全部。
 *
 * 安全：`report.html` 一律 `sandbox="allow-scripts"`（**不给** allow-same-origin），
 * 否则网关侧的 HTML 脚本能同源读到 JWT。
 */
import { useEffect, useState } from "react";
import { hibtvApi, mediaUrl } from "../api";
import { errText, labelOfKind, useHibtv } from "../state";
import type { ArtifactItem, ArtifactList } from "../types";

/** 简易 JSON 折叠树（只展示，不编辑；编辑走 docs 的 PATCH 底账）。 */
function JsonTree({ value, depth = 0 }: { value: unknown; depth?: number }) {
  const [open, setOpen] = useState(depth < 2);
  if (value === null || value === undefined) return <span className="dim">null</span>;
  if (typeof value === "string") {
    return <span>{value.length > 240 ? `${value.slice(0, 240)}…` : value}</span>;
  }
  if (typeof value !== "object") return <span className="mono">{String(value)}</span>;
  const isArr = Array.isArray(value);
  const entries = Object.entries(value as Record<string, unknown>);
  if (!entries.length) return <span className="dim">{isArr ? "[ ]" : "{ }"}</span>;
  return (
    <div style={{ marginLeft: depth ? 12 : 0 }}>
      <span className="link" onClick={() => setOpen((v) => !v)}>
        {open ? "▾" : "▸"} {isArr ? `[${entries.length}]` : `{${entries.length}}`}
      </span>
      {open && (
        <div style={{ marginLeft: 10, borderLeft: "1px solid var(--line-3)", paddingLeft: 8 }}>
          {entries.map(([k, v]) => (
            <div key={k} style={{ padding: "1px 0" }}>
              <span className="mono tiny dim">{k}</span>: <JsonTree value={v} depth={depth + 1} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TextBlock({ url }: { url: string }) {
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    setText("");
    setErr("");
    fetch(mediaUrl(url))
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((t) => alive && setText(t))
      .catch((e) => alive && setErr(String(e?.message || e)));
    return () => {
      alive = false;
    };
  }, [url]);
  if (err) return <div className="tiny red">{err}</div>;
  if (!text) return <div className="tiny dim">读取中…</div>;
  return (
    <pre
      className="mono tiny"
      style={{ whiteSpace: "pre-wrap", maxHeight: 520, overflow: "auto", margin: 0 }}
    >
      {text}
    </pre>
  );
}

function JsonBlock({ url }: { url: string }) {
  const [data, setData] = useState<unknown>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    fetch(mediaUrl(url))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((j) => alive && setData(j))
      .catch((e) => alive && setErr(String(e?.message || e)));
    return () => {
      alive = false;
    };
  }, [url]);
  if (err) return <div className="tiny red">{err}</div>;
  if (!data) return <div className="tiny dim">读取中…</div>;
  return (
    <div className="tiny" style={{ maxHeight: 520, overflow: "auto" }}>
      <JsonTree value={data} />
    </div>
  );
}

const TABS = [
  { key: "report", label: "评审报告" },
  { key: "md", label: "人读稿" },
  { key: "json", label: "JSON" },
  { key: "all", label: "全部产物" },
] as const;

export function ArtifactsDrawer({
  projectId,
  jobId = 0,
  onClose,
}: {
  projectId: number;
  jobId?: number;
  onClose: () => void;
}) {
  const { toast } = useHibtv();
  const [data, setData] = useState<ArtifactList | null>(null);
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("report");
  const [picked, setPicked] = useState<ArtifactItem | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    hibtvApi
      .artifacts(projectId, jobId)
      .then((res) => {
        if (!alive) return;
        setData(res);
        setPicked(res.items.find((i) => i.kind === "html") || res.items[0] || null);
      })
      .catch((err) => alive && toast(errText(err)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [projectId, jobId, toast]);

  const items = data?.items || [];
  const reports = items.filter((i) => i.kind === "html");
  const marks = items.filter((i) => i.kind === "md");
  const jsons = items.filter((i) => i.kind === "json");

  const body = () => {
    if (loading) return <div className="tiny dim">读取产物清单…</div>;
    if (!data) return <div className="tiny dim">还没有产物。</div>;
    if (!items.length) return <div className="tiny dim">这次作业没有落盘任何产物。</div>;
    if (tab === "all") {
      return (
        <div style={{ maxHeight: 520, overflow: "auto" }}>
          {items.map((i) => (
            <div key={i.path} className="inline" style={{ padding: "3px 0" }}>
              <span className={`chip ${i.kind === "other" ? "ghost" : ""}`}>{i.kind}</span>
              <span className="mono tiny" title={i.path}>
                {i.path}
              </span>
              <span className="tiny dim" style={{ marginLeft: "auto" }}>
                {formatBytes(i.bytes)}
              </span>
              <a className="link tiny" href={mediaUrl(i.url)} target="_blank" rel="noreferrer">
                下载
              </a>
            </div>
          ))}
        </div>
      );
    }
    const pool = tab === "report" ? reports : tab === "md" ? marks : jsons;
    if (!pool.length) return <div className="tiny dim">这一类没有产物。</div>;
    const cur = picked && pool.some((p) => p.path === picked.path) ? picked : pool[0];
    return (
      <div>
        {pool.length > 1 && (
          <div className="tabs sm" style={{ marginBottom: 8 }}>
            {pool.map((p) => (
              <span
                key={p.path}
                className={`tab ${cur.path === p.path ? "on" : ""}`}
                onClick={() => setPicked(p)}
                title={p.path}
              >
                {p.name}
              </span>
            ))}
          </div>
        )}
        {cur.kind === "html" && (
          <iframe
            title={cur.name}
            src={mediaUrl(cur.url)}
            sandbox="allow-scripts"
            style={{
              width: "100%",
              height: 560,
              border: "1px solid var(--line-3)",
              borderRadius: 6,
              background: "#fff",
            }}
          />
        )}
        {cur.kind === "md" && <TextBlock url={cur.url} />}
        {cur.kind === "json" && <JsonBlock url={cur.url} />}
      </div>
    );
  };

  return (
    <div className="drawer wide">
      <div className="drawer-hd">
        <div>
          <div className="tiny dim">
            产物 · {data ? labelOfKind(data.kind) : ""}
            {data?.ep ? ` E${String(data.ep).padStart(2, "0")}` : ""}
            {data?.codexJobId ? ` · 网关 ${data.codexJobId.slice(0, 8)}` : ""}
          </div>
          <div className="t">skill 产出的全部文件</div>
        </div>
        <span className="x" onClick={onClose}>
          ✕
        </span>
      </div>
      <div className="drawer-bd">
        <div className="tabs" style={{ marginBottom: 10 }}>
          {TABS.map((t) => (
            <span
              key={t.key}
              className={`tab ${tab === t.key ? "on" : ""}`}
              onClick={() => {
                setTab(t.key);
                setPicked(null);
              }}
            >
              {t.label}
            </span>
          ))}
        </div>
        {body()}
      </div>
      <div className="drawer-ft">
        <span className="tiny dim">{items.length} 个产物</span>
        <span style={{ flex: 1 }} />
        <a className="btn ghost" href={hibtvApi.artifactsZipUrl(projectId, data?.jobId || jobId)}>
          下载全部
        </a>
        <button className="btn" onClick={onClose}>
          关闭
        </button>
      </div>
    </div>
  );
}

export function formatBytes(n: number): string {
  const v = Number(n || 0);
  if (v < 1024) return `${v} B`;
  if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
  return `${(v / 1024 / 1024).toFixed(1)} MB`;
}

/** 文档里记的 report.html 相对路径 → 可内嵌的 iframe（产物抽屉之外的「就在页面里看」）。 */
export function ReportFrame({
  projectId,
  jobId,
  rel,
  height = 520,
}: {
  projectId: number;
  jobId: string;
  rel: string;
  height?: number;
}) {
  if (!rel || !jobId) return null;
  const url = `/files/hibtv/${projectId}/codex/${jobId}/${rel}`;
  return (
    <iframe
      title={rel}
      src={mediaUrl(url)}
      sandbox="allow-scripts"
      style={{
        width: "100%",
        height,
        border: "1px solid var(--line-3)",
        borderRadius: 6,
        background: "#fff",
      }}
    />
  );
}
