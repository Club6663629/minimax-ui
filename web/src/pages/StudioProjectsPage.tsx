/** 剧集工坊：项目列表 + 导入工作台（剧本 / 美术设定两份 Markdown）。 */
import { useEffect, useState } from "react";
import { BookOpen, CheckCircle2, FileUp, Plus, XCircle } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { GATE_LABEL, errText } from "../components/studio/labels";
import type { StudioGates, StudioProject, StudioStatus } from "../types";

const CHECK_LABEL = GATE_LABEL;

function GatePill({ kind, gates }: { kind: string; gates: StudioGates }) {
  const fail = gates.gates.filter((g) => !g.ok).length;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs ${
        gates.passed ? "bg-accent-100 text-accent-800" : "bg-red-100 text-red-700"
      }`}
      title={gates.gates
        .filter((g) => !g.ok)
        .map((g) => `${g.label}${g.detail ? `：${g.detail}` : ""}`)
        .join("\n")}
    >
      {gates.passed ? <CheckCircle2 size={12} /> : <XCircle size={12} />}
      {CHECK_LABEL[kind] || kind} {gates.gates.length - fail}/{gates.gates.length}
    </span>
  );
}

/** 选文件 → 读文本。两个框各一个按钮，避免拖拽区做两套样式。 */
function FilePick({
  label,
  value,
  onChange,
  hint,
}: {
  label: string;
  value: string;
  onChange: (text: string) => void;
  hint: string;
}) {
  return (
    <label className="block">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs font-medium text-foreground-700">{label}</span>
        <span className="text-[11px] text-foreground-500">{hint}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-background-300 px-3 text-xs text-foreground-700">
          <FileUp size={13} />
          <input
            type="file"
            accept=".md,.markdown,.txt"
            className="hidden"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (file) onChange(await file.text());
              e.target.value = "";
            }}
          />
          选择文件
        </span>
        <span className="truncate text-xs text-foreground-500">
          {value ? `已载入 ${value.length.toLocaleString()} 字` : "未选择"}
        </span>
      </div>
    </label>
  );
}

export default function StudioProjectsPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<StudioStatus | null>(null);
  const [projects, setProjects] = useState<StudioProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<"" | "preview" | "import">("");

  // 导入面板：一次只编辑一个项目；没有 id 表示新建
  const [form, setForm] = useState({
    id: null as number | null,
    title: "焚霜引",
    status: "draft",
    scriptMd: "",
    artMd: "",
  });
  const [preview, setPreview] = useState<{
    stats: Record<string, number>;
    checks: Record<string, StudioGates>;
  } | null>(null);
  const [notice, setNotice] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await api.studioProjects();
      setProjects(result.items);
    } catch (exc) {
      setError(errText(exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    (async () => {
      const remote = await api.studioStatus().catch(() => null);
      setStatus(remote);
      if (remote && !remote.enabled) {
        setLoading(false);
        return;
      }
      await load();
    })();
  }, []);

  const disabled = status !== null && !status.enabled;
  const ready = form.scriptMd.trim().length > 0 && form.artMd.trim().length > 0;

  /** 预览 / 导入都要先有项目行；新建时顺便把它落库。 */
  async function ensureProject(): Promise<number> {
    if (form.id) return form.id;
    const title = form.title.trim() || "未命名剧集";
    const created = await api.studioCreateProject(title);
    setForm((f) => ({ ...f, id: created.id, title, status: created.status }));
    setProjects((rows) => [created, ...rows]);
    return created.id;
  }

  async function handlePreview() {
    setBusy("preview");
    setError("");
    setNotice("");
    try {
      const pid = await ensureProject();
      const result = await api.studioPreview(pid, {
        script_md: form.scriptMd,
        art_md: form.artMd,
      });
      setPreview(result);
      setNotice("解析完成，确认门的结论后再导入");
    } catch (exc) {
      setError(errText(exc));
    } finally {
      setBusy("");
    }
  }

  async function handleImport() {
    setBusy("import");
    setError("");
    setNotice("");
    try {
      const pid = await ensureProject();
      const result = await api.studioImport(pid, {
        script_md: form.scriptMd,
        art_md: form.artMd,
        force: form.status !== "draft",
      });
      setNotice(
        `导入完成：${result.stats.episodes} 集 / ${result.stats.characters} 角色 / ` +
          `${result.stats.scenes} 场景 / ${result.stats.props} 道具`,
      );
      navigate(`/studio/${pid}`);
    } catch (exc) {
      setError(errText(exc));
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold text-foreground-950">
            <BookOpen size={18} /> 剧集工坊
          </h1>
          <p className="mt-0.5 text-xs text-foreground-500">
            剧本 + 美术设定 → 80 集地图与系列设定 → 逐集出切表与成片。
          </p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2 text-xs">
          <span className={`chip ${status?.enabled ? "" : "text-red-600"}`}>
            工坊 {status?.enabled ? "已开启" : "未开启"}
          </span>
          <span className={`chip ${status?.image?.available ? "" : "text-red-600"}`}>
            出图节点 {status?.image?.available ? "就绪" : "未配置"}
          </span>
          <span className="chip">单张 {status?.cost_image ?? "—"} 积分</span>
        </div>
      </div>

      {disabled && (
        <div className="panel border-amber-300 bg-amber-50 p-4 text-sm text-amber-800">
          剧集工坊未开启：需要后端设置 <code className="mx-1">STUDIO_ENABLED=1</code> 并重启。
        </div>
      )}

      {error && (
        <div className="panel border-red-300 bg-red-50 p-3 text-sm text-red-700">{error}</div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_420px]">
        {/* 项目列表 */}
        <div className="space-y-3">
          {loading ? (
            <div className="panel p-6 text-sm text-foreground-500">加载中…</div>
          ) : projects.length === 0 ? (
            <div className="panel p-6 text-sm text-foreground-500">
              还没有项目。右侧填标题、载入两份 Markdown 就能建第一部。
            </div>
          ) : (
            projects.map((p) => (
              <div key={p.id} className="panel flex items-center gap-3 p-4">
                <button
                  type="button"
                  className="min-w-0 flex-1 text-left"
                  onClick={() => navigate(`/studio/${p.id}`)}
                >
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold text-foreground-900">
                      {p.title}
                    </span>
                    <span className="chip">{p.status}</span>
                  </div>
                  <div className="mt-1 text-xs text-foreground-500">
                    {p.source_name || "—"} · 更新 {p.updated_at.replace("T", " ")}
                  </div>
                </button>
                <button
                  type="button"
                  className="btn-ghost !px-3 !py-1.5 text-xs"
                  onClick={() => {
                    setForm({
                      id: p.id,
                      title: p.title,
                      status: p.status,
                      scriptMd: "",
                      artMd: "",
                    });
                    setPreview(null);
                    setNotice("已选中项目，重新导入请先载入两份文件（会覆盖已有数据）");
                  }}
                >
                  重新导入
                </button>
              </div>
            ))
          )}
        </div>

        {/* 导入面板 */}
        <div className="panel space-y-3 p-4">
          <div className="flex items-center gap-2">
            <Plus size={15} className="text-foreground-600" />
            <span className="text-sm font-semibold text-foreground-900">
              {form.id ? `导入到「${form.title}」` : "新建并导入"}
            </span>
            {form.id && (
              <button
                type="button"
                className="ml-auto text-xs text-foreground-500 hover:text-foreground-800"
                onClick={() => {
                  setForm({ id: null, title: "焚霜引", status: "draft", scriptMd: "", artMd: "" });
                  setPreview(null);
                  setNotice("");
                }}
              >
                改为新建
              </button>
            )}
          </div>

          <label className="block">
            <div className="mb-1 text-xs font-medium text-foreground-700">剧名</div>
            <input
              className="input"
              value={form.title}
              onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
              placeholder="例如：焚霜引"
            />
          </label>

          <FilePick
            label="剧本（项目提案 + 全剧本）"
            hint="Markdown"
            value={form.scriptMd}
            onChange={(text) => setForm((f) => ({ ...f, scriptMd: text }))}
          />
          <FilePick
            label="美术设定集"
            hint="Markdown"
            value={form.artMd}
            onChange={(text) => setForm((f) => ({ ...f, artMd: text }))}
          />

          <div className="flex items-center gap-2">
            <button
              type="button"
              className="btn-ghost flex-1"
              disabled={disabled || !ready || busy !== ""}
              onClick={handlePreview}
            >
              {busy === "preview" ? "解析中…" : "先看门的结论"}
            </button>
            <button
              type="button"
              className="btn-primary flex-1"
              disabled={disabled || !ready || busy !== ""}
              onClick={handleImport}
            >
              {busy === "import" ? "导入中…" : "导入"}
            </button>
          </div>

          {notice && (
            <div className="rounded-lg bg-background-200 px-3 py-2 text-xs text-foreground-700">
              {notice}
            </div>
          )}

          {preview && (
            <div className="space-y-2 border-t border-background-200 pt-2">
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(preview.checks).map(([kind, g]) => (
                  <GatePill key={kind} kind={kind} gates={g} />
                ))}
              </div>
              <div className="text-[11px] leading-5 text-foreground-500">
                {preview.stats.episodes} 集 / {preview.stats.characters} 角色 /{" "}
                {preview.stats.scenes} 场景 / {preview.stats.props} 道具
              </div>
              {Object.entries(preview.checks).flatMap(([kind, g]) =>
                g.problems.map((p) => (
                  <div key={`${kind}-${p}`} className="text-[11px] text-amber-700">
                    {CHECK_LABEL[kind] || kind}：{p}
                  </div>
                )),
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
