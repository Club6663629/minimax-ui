/** 大纲：对齐 codex `novel-outline` 的真实产物结构（adaptation / beats / episodes）。
 *
 * 平台**不改编、不拼提示词**：这一页只做三件事——把产物字段摊开给人看、允许就地改字段
 * （改的是文档 + 变更底账，门随即变 stale）、把 `outline-report.html` 原样嵌进来评审。
 * 门 A：通过 → 放行下游；带提示重跑 → 把一句业务意图塞进 input 再跑一次 skill。
 */
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { hibtvApi } from "../api";
import { Stage } from "../HibtvLayout";
import { CodexLight, useCodexGate } from "../components/CodexLight";
import { ArtifactsDrawer, ReportFrame } from "../components/StageArtifacts";
import { Empty, GateChips, GateList, GenBar, InlineEdit, Loading, PageHead } from "../components/ui";
import { useDoc, useHibtv, useJob, usePatch } from "../state";
import type { ArtData, CastData, OutlineData } from "../types";

const TABS = [
  { k: "inner", label: "改编方案" },
  { k: "cast", label: "角色表" },
  { k: "scene", label: "场景" },
  { k: "prop", label: "道具" },
  { k: "beat", label: "爽点" },
  { k: "ep", label: "分集" },
  { k: "report", label: "评审报告" },
  { k: "align", label: "对账" },
];

const WEIGHT_ZH: Record<string, string> = {
  major: "大爆点",
  minor: "小爽点",
  setup: "铺垫",
  payoff: "回收",
};

function listOf(v: unknown): string[] {
  if (Array.isArray(v)) return v.map((x) => (typeof x === "string" ? x : JSON.stringify(x)));
  if (typeof v === "string") return [v];
  return [];
}

export default function OutlinePage() {
  const { tab = "inner" } = useParams();
  const navigate = useNavigate();
  const { project, toast, openLayer, closeLayer } = useHibtv();
  const { data, doc, patch, loading } = usePatch("outline");
  const job = useJob("outline");
  const gate = useCodexGate();
  const outline = (data || {}) as OutlineData;
  const gates = doc?.gates;
  const [hint, setHint] = useState("");

  const tabs = useMemo(
    () =>
      TABS.map((t) => ({
        ...t,
        n:
          t.k === "cast"
            ? outline.characters?.length || 0
            : t.k === "scene"
              ? outline.scenes?.length || 0
              : t.k === "prop"
                ? outline.props?.length || 0
                : t.k === "beat"
                  ? outline.beats?.length || 0
                  : t.k === "ep"
                    ? outline.episodes?.length || 0
                    : 0,
      })),
    [outline],
  );

  const rerun = (extra?: string) => {
    if (!gate.ok) {
      toast(gate.why);
      return;
    }
    void runJobWithHint(extra ?? "");
  };
  const runJobWithHint = async (text: string) => {
    if (!project) return;
    try {
      await hibtvApi.createJob(project.id, { kind: "outline", payload: text ? { hint: text } : {} });
      toast(text ? "已排队：带提示重跑大纲（同键复用会自动失效）" : "已排队：重跑大纲");
      setHint("");
    } catch (err) {
      toast(String(err));
    }
  };

  const side = (
    <>
      <div className="side-head">大纲 · OUTLINE</div>
      <div className="subnav">
        {tabs.map((t) => (
          <div
            key={t.k}
            className={`nav-item ${tab === t.k ? "on" : ""}`}
            onClick={() => navigate(`/hibtv/outline/${t.k}`)}
          >
            {t.label}
            {t.n ? <span className="n">{t.n}</span> : null}
          </div>
        ))}
      </div>
      <div className="tiny dim" style={{ padding: "0 8px", lineHeight: 1.8 }}>
        产物来自 codex 的 <span className="mono">novel-outline</span>：
        <br />
        adaptation / beats / episodes 三段是下游的唯一事实来源。
      </div>
    </>
  );

  if (!project) {
    return (
      <Stage side={side}>
        <Empty glyph="剧" title="还没有项目" sub="先在首页粘贴或上传小说正文。">
          <button className="btn gold" onClick={() => navigate("/hibtv/home")}>
            去建项目
          </button>
        </Empty>
      </Stage>
    );
  }

  const head = (
    <PageHead
      eyebrow="收敛层 · 改成什么 · OUTLINE · 门 A"
      title="大纲"
      lede={
        <>
          来自 <em>{project.novelName || "小说正文"}</em>，由 <span className="mono">novel-outline</span>{" "}
          跑 skill 产出。平台只搬字段：改一行写进变更底账，门随即标「改动后门未复跑」。
        </>
      }
      right={
        <>
          <CodexLight />
          <GateChips gates={gates} none="大纲没有结构化门结果" />
          <button
            className="btn ghost"
            onClick={() =>
              openLayer(<ArtifactsDrawer projectId={project.id} jobId={job?.id || 0} onClose={closeLayer} />)
            }
          >
            产物
          </button>
          <button className="btn" onClick={() => rerun()} disabled={!gate.ok} title={gate.why}>
            {outline.episodes?.length ? "重跑大纲" : "生成大纲"}
          </button>
          <button
            className="btn gold"
            disabled={!outline.episodes?.length}
            onClick={() => {
              toast("大纲已通过（门 A）");
              navigate("/hibtv/cast");
            }}
          >
            通过 · 去角色
          </button>
        </>
      }
    />
  );

  const meta = (
    <>
      <div className="toolbar" style={{ marginTop: 14 }}>
        <span className="lab">本次产物</span>
        <span className="chip">stage {doc?.meta?.stage || "novel-outline"}</span>
        {doc?.meta?.codex_job_id ? (
          <span className="chip ghost mono">jid {String(doc.meta.codex_job_id).slice(0, 8)}</span>
        ) : null}
        {doc?.meta?.mainSha8 ? <span className="chip ghost mono">sha {doc.meta.mainSha8}</span> : null}
        <span className="chip">
          {outline.episodes?.length || 0} 集 · {outline.beats?.length || 0} 爽点
        </span>
        <span className="chip ghost">{outline.characters?.length || 0} 角色</span>
        <span className="sp">
          <span className="tiny dim">{doc?.updated_at || "还没有产物"}</span>
        </span>
      </div>
      <div className="inline" style={{ margin: "12px 0" }}>
        <span className="chip">题材 {outline.params?.genre || project.genre || "未定"}</span>
        <span className="chip">
          规模 {outline.params?.episodes || project.episodes} 集 · 单集 {outline.params?.minutesPerEpisode || "—"} 分钟
        </span>
        <span className="chip">画面 {outline.params?.aspectRatio || project.aspectRatio}</span>
        <span className="chip ghost">改编 {outline.params?.adaptMode || "skill 默认"}</span>
      </div>
      <div className="card" style={{ marginBottom: 12 }}>
        <div className="card-bd">
          <div className="inline" style={{ flexWrap: "wrap" }}>
            <span className="lab tiny">门 A · 带提示重跑</span>
            <input
              className="input"
              style={{ flex: 1, minWidth: 260 }}
              value={hint}
              placeholder="例：钩子再狠一点，第 1 集前 10 秒就要见血；别把结局说透"
              onChange={(e) => setHint(e.target.value)}
            />
            <button className="btn sm" disabled={!gate.ok} title={gate.why} onClick={() => rerun(hint)}>
              带提示重跑
            </button>
          </div>
          <div className="tiny dim" style={{ marginTop: 6 }}>
            提示会写进 input 的「额外要求」，也让幂等键失效（同键复用不再命中）。
          </div>
        </div>
      </div>
    </>
  );

  if (loading && !data) {
    return (
      <Stage side={side}>
        {head}
        {meta}
        <Loading />
      </Stage>
    );
  }

  if (!outline.episodes?.length) {
    return (
      <Stage side={side}>
        {head}
        {meta}
        <GenBar
          job={job}
          onCancel={job ? () => void hibtvApi.cancelRemoteJob(project.id, job.id) : undefined}
        />
        <GateList gates={gates} />
        <Empty
          glyph="纲"
          title="还没有大纲"
          sub={
            job?.status === "failed"
              ? `大纲生成失败：${job.error}${job.timeoutKind ? `（${job.timeoutKind}）` : ""}`
              : "由 codex 跑 novel-outline：出集数/时长/比例、改编方案（保留·砍·合并·风险）、爽点表与分集梗概。"
          }
        >
          <button className="btn gold" onClick={() => rerun()} disabled={!gate.ok} title={gate.why}>
            生成大纲
          </button>
          {!gate.ok && (
            <div className="tiny red" style={{ marginTop: 8 }}>
              {gate.why}
            </div>
          )}
        </Empty>
      </Stage>
    );
  }

  return (
    <Stage side={side}>
      {head}
      {meta}
      <GenBar job={job} onCancel={job ? () => void hibtvApi.cancelRemoteJob(project.id, job.id) : undefined} />
      <GateList gates={gates} />
      {tab === "inner" && <InnerTab outline={outline} patch={patch} />}
      {tab === "cast" && <CastTab outline={outline} patch={patch} />}
      {tab === "scene" && <SceneTab outline={outline} patch={patch} />}
      {tab === "prop" && <PropTab outline={outline} patch={patch} />}
      {tab === "beat" && <BeatTab outline={outline} patch={patch} />}
      {tab === "ep" && <EpTab outline={outline} patch={patch} />}
      {tab === "report" && (
        <ReportTab projectId={project.id} jobId={job?.id || 0} meta={doc?.meta} />
      )}
      {tab === "align" && <AlignTab outline={outline} />}
      <div className="tiny dim" style={{ marginTop: 14, lineHeight: 1.9 }}>
        双击文字即可改；改动写进「变更」，可一键撤销。门结果只来自网关产物（.gates.jsonl / report.html），
        就地改稿后一律标「改动后门未复跑」，不会伪装成通过。
      </div>
    </Stage>
  );
}

/* ------------------------------------------------------------------ 各子页 */
type Patch = (path: string, value: unknown, label?: string) => Promise<void>;

function InnerTab({ outline, patch }: { outline: OutlineData; patch: Patch }) {
  const adaptation = outline.adaptation || {};
  return (
    <div className="sec">
      <div className="h2">改编方案</div>
      <div className="card">
        <div className="card-bd">
          <InlineEdit
            className="quote"
            multiline
            value={adaptation.core || ""}
            placeholder="（还没有内核；点「重跑大纲」或双击这里手写）"
            onSave={(v) => void patch("adaptation.core", v, "改编内核")}
          />
          <div className="split3" style={{ marginTop: 14 }}>
            <div className="stat">
              <span className="k">集数 / 单集</span>
              <span className="small">
                {outline.params?.episodes || outline.episodes?.length || 0} 集 ·{" "}
                {outline.params?.minutesPerEpisode || "—"} 分钟
              </span>
            </div>
            <div className="stat">
              <span className="k">爽点</span>
              <span className="small">
                {outline.beats?.length || 0} 个 · 大爆点{" "}
                {(outline.beats || []).filter((b) => b.weight === "major").length} 个
              </span>
            </div>
            <div className="stat">
              <span className="k">资产清单</span>
              <span className="small">
                {outline.characters?.length || 0} 人 / {outline.scenes?.length || 0} 场景 /{" "}
                {outline.props?.length || 0} 道具
              </span>
            </div>
          </div>
        </div>
      </div>

      {(
        [
          ["keep", "保留", "原文明写的核心情节，改编必须留"],
          ["cut", "砍掉", "为了时长 / 合规砍掉的支线与人物"],
          ["merge", "合并", "合并的人物或场景"],
          ["risks", "风险", "合规 / 观感 / 制作风险"],
        ] as [keyof typeof adaptation, string, string][]
      ).map(([key, label, hint]) => {
        const items = listOf(adaptation[key]);
        return (
          <div className="sec" key={String(key)}>
            <div className="inline" style={{ marginBottom: 6 }}>
              <span className="h2" style={{ margin: 0 }}>
                {label}
              </span>
              <span className="chip">{items.length}</span>
              <span className="tiny dim">{hint}</span>
            </div>
            <div className="card">
              <div className="card-bd small mut" style={{ lineHeight: 1.9 }}>
                <InlineEdit
                  multiline
                  value={items.length ? items.map((x) => `· ${x}`).join("\n") : ""}
                  placeholder="（空）双击可写；一段一条，用换行分隔"
                  onSave={(v) =>
                    void patch(
                      `adaptation.${String(key)}`,
                      v
                        .split("\n")
                        .map((x) => x.replace(/^[·\-*]\s*/, "").trim())
                        .filter(Boolean),
                      `改编 · ${label}`,
                    )
                  }
                />
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function CastTab({ outline, patch }: { outline: OutlineData; patch: Patch }) {
  const rows = outline.characters || [];
  return (
    <div className="sec">
      <div className="h2">
        角色表 <span className="tiny dim">{rows.length} 人 · 双击单元格可改</span>
      </div>
      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th>ID</th>
              <th>姓名</th>
              <th>层级</th>
              <th>定位</th>
              <th>弧光</th>
              <th>来源</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c, i) => (
              <tr key={c.id || i}>
                <td className="id">{c.id}</td>
                <td className="nm">{c.name}</td>
                <td>
                  <span className={`chip ${c.tier === "lead" ? "gold" : ""}`}>
                    {c.tier === "lead" ? "主角" : c.tier === "support" ? "配角" : c.tier || "功能"}
                  </span>
                </td>
                <td className="small">{c.role || "—"}</td>
                <td className="small mut edit">
                  <InlineEdit
                    multiline
                    value={c.arc || ""}
                    onSave={(v) => void patch(`characters.${i}.arc`, v, `角色 · ${c.name} 弧光`)}
                  />
                </td>
                <td className="tiny dim">{(c.from || []).join(" + ") || "skill 推断"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="tiny dim" style={{ marginTop: 10 }}>
        这一张是「改编后的角色表」；形象提示词与音色在角色页（novel-characters 的产物）里。
      </div>
    </div>
  );
}

function SceneTab({ outline, patch }: { outline: OutlineData; patch: Patch }) {
  const rows = outline.scenes || [];
  return (
    <div className="sec">
      <div className="h2">
        场景 <span className="tiny dim">{rows.length} 个</span>
      </div>
      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th>ID</th>
              <th>场景</th>
              <th>类型</th>
              <th>出现集</th>
              <th>复用方案</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s, i) => {
              const eps = (outline.episodes || [])
                .filter((e) => (e.sceneIds || []).includes(s.id))
                .map((e) => e.ep);
              return (
                <tr key={s.id || i}>
                  <td className="id">{s.id}</td>
                  <td className="nm">{s.name}</td>
                  <td className="small">{s.primary ? "主场景" : "次场景"}</td>
                  <td className="tiny mono">{eps.join("、") || "—"}</td>
                  <td className="small mut edit">
                    <InlineEdit
                      multiline
                      value={s.reusePlan || ""}
                      placeholder="一次性场景要写复用 / 规避方案"
                      onSave={(v) => void patch(`scenes.${i}.reusePlan`, v, `场景 · ${s.name} 复用方案`)}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PropTab({ outline, patch }: { outline: OutlineData; patch: Patch }) {
  const rows = outline.props || [];
  return (
    <div className="sec">
      <div className="h2">
        道具 <span className="tiny dim">{rows.length} 件</span>
      </div>
      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th>ID</th>
              <th>道具</th>
              <th>出现集</th>
              <th>作用</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p, i) => {
              const eps = (outline.episodes || [])
                .filter((e) => (e.propIds || []).includes(p.id))
                .map((e) => e.ep);
              return (
                <tr key={p.id || i}>
                  <td className="id">{p.id}</td>
                  <td className="nm">{p.name}</td>
                  <td className="tiny mono">{eps.join("、") || "—"}</td>
                  <td className="small mut edit">
                    <InlineEdit
                      multiline
                      value={p.function || ""}
                      onSave={(v) => void patch(`props.${i}.function`, v, `道具 · ${p.name}`)}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function BeatTab({ outline, patch }: { outline: OutlineData; patch: Patch }) {
  const beats = outline.beats || [];
  const byEp = useMemo(() => {
    const map = new Map<number, number>();
    for (const b of beats) map.set(b.episode, (map.get(b.episode) || 0) + 1);
    return Array.from(map.entries()).sort((a, b) => a[0] - b[0]);
  }, [beats]);

  if (!beats.length) {
    return <Empty glyph="点" title="这一版没有爽点表" sub="beats[] 为空：重跑大纲，或直接在分集页写钩子。" />;
  }

  return (
    <div className="sec">
      <div className="h2">
        爽点时间轴 <span className="tiny dim">{beats.length} 个 · 每集几个</span>
      </div>
      <div className="timeline" style={{ marginBottom: 12 }}>
        {byEp.map(([ep, n]) => (
          <span className="tl" key={ep} title={`第 ${ep} 集 · ${n} 个爽点`}>
            E{ep}·{n}
          </span>
        ))}
      </div>
      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th>ID</th>
              <th>集</th>
              <th>类型</th>
              <th>权重</th>
              <th>铺垫</th>
              <th>兑现</th>
            </tr>
          </thead>
          <tbody>
            {beats.map((b, i) => (
              <tr key={b.id || i}>
                <td className="id">{b.id}</td>
                <td className="mono">E{String(b.episode).padStart(2, "0")}</td>
                <td className="small">{b.type}</td>
                <td>
                  <span className={`chip ${b.weight === "major" ? "gold" : ""}`}>
                    {WEIGHT_ZH[String(b.weight)] || String(b.weight ?? "—")}
                  </span>
                </td>
                <td className="small mut edit">
                  <InlineEdit
                    multiline
                    value={b.setup || ""}
                    onSave={(v) => void patch(`beats.${i}.setup`, v, `爽点 ${b.id} 铺垫`)}
                  />
                </td>
                <td className="small mut edit">
                  <InlineEdit
                    multiline
                    value={b.payoff || ""}
                    onSave={(v) => void patch(`beats.${i}.payoff`, v, `爽点 ${b.id} 兑现`)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EpTab({ outline, patch }: { outline: OutlineData; patch: Patch }) {
  const rows = outline.episodes || [];
  return (
    <div className="sec">
      <div className="h2">
        分集 <span className="tiny dim">{rows.length} 集 · 梗概 / 钩子 / 断点 / 群戏</span>
      </div>
      <div className="card">
        {rows.map((e, i) => {
          const beats = (outline.beats || []).filter((b) => b.episode === e.ep);
          return (
            <div key={e.ep} style={{ padding: "13px 15px", borderBottom: "1px solid var(--line-3)" }}>
              <div className="inline" style={{ marginBottom: 6, flexWrap: "wrap" }}>
                <span className="mono" style={{ color: "var(--gold-2)" }}>
                  E{String(e.ep).padStart(2, "0")}
                </span>
                <span className="chip">{(e.sceneIds || []).length} 场</span>
                <span className="chip ghost">
                  {(e.characterIds || []).length} 人 · {(e.propIds || []).length} 道具
                </span>
                {beats.slice(0, 2).map((b) => (
                  <span className="chip gold" key={b.id}>
                    {b.id} {b.type}
                  </span>
                ))}
                {e.crowdPlan ? <span className="chip ghost">群戏 {e.crowdPlan}</span> : null}
              </div>
              <div className="small mut edit" style={{ marginBottom: 6 }}>
                <InlineEdit
                  multiline
                  value={e.synopsis || ""}
                  onSave={(v) => void patch(`episodes.${i}.synopsis`, v, `分集 E${e.ep} 梗概`)}
                />
              </div>
              <div className="tiny" style={{ color: "#8f877b" }}>
                <b style={{ color: "var(--gold-dim)" }}>钩子</b>{" "}
                <InlineEdit
                  multiline
                  value={e.hook || ""}
                  onSave={(v) => void patch(`episodes.${i}.hook`, v, `分集 E${e.ep} 钩子`)}
                />
              </div>
              <div className="tiny" style={{ color: "#7d766b", marginTop: 3 }}>
                <b style={{ color: "var(--gold-dim)" }}>断点</b>{" "}
                <InlineEdit
                  multiline
                  value={e.suspense || ""}
                  onSave={(v) => void patch(`episodes.${i}.suspense`, v, `分集 E${e.ep} 断点`)}
                />
              </div>
              {(e.warnings || []).length ? (
                <div className="tiny red" style={{ marginTop: 4 }}>
                  {(e.warnings || []).join(" · ")}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ReportTab({
  projectId,
  jobId,
  meta,
}: {
  projectId: number;
  jobId: number;
  meta?: Record<string, unknown>;
}) {
  const report = typeof meta?.report === "string" ? meta.report : "";
  const md = typeof meta?.md === "string" ? meta.md : "";
  if (!report && !md) {
    return (
      <Empty
        glyph="报"
        title="这一版没有 report.html / md"
        sub="网关产物里没有找到 outline-report.html；「产物」抽屉里能看全部文件。"
      />
    );
  }
  return (
    <div className="sec">
      <div className="inline" style={{ marginBottom: 8 }}>
        <span className="chip">outline-report.html</span>
        <span className="chip ghost">自包含单页 · 嵌套 iframe 只给 allow-scripts</span>
        <span className="sp" style={{ marginLeft: "auto" }} />
        {md ? (
          <a className="btn xs ghost" href={`/files/hibtv/${projectId}/codex/${meta?.codex_job_id || ""}/${md}`} target="_blank" rel="noreferrer">
            下载 md
          </a>
        ) : null}
      </div>
      {report ? (
        <div className="card">
          <div className="card-bd">
            <ReportFrame
              projectId={projectId}
              jobId={String(meta?.codex_job_id || jobId)}
              rel={report}
              height={960}
            />
          </div>
        </div>
      ) : (
        <div className="tiny dim">只有 md，没有 html 报告。</div>
      )}
    </div>
  );
}

function AlignTab({ outline }: { outline: OutlineData }) {
  const { data: castData } = useDoc("cast");
  const { data: artData } = useDoc("art");
  const cast = ((castData || {}) as CastData).characters || [];
  const art = (artData || {}) as ArtData;

  const names = (arr?: { name?: string }[]) => new Set((arr || []).map((x) => String(x.name || "")));
  const castNames = names(cast);
  const sceneNames = names(art.scenes);
  const propNames = names(art.props);

  const rows = [
    {
      kind: "角色",
      want: (outline.characters || []).length,
      have: cast.length,
      miss: (outline.characters || []).filter((c) => !castNames.has(c.name)).map((c) => c.name),
    },
    {
      kind: "场景",
      want: (outline.scenes || []).length,
      have: (art.scenes || []).length,
      miss: (outline.scenes || []).filter((s) => !sceneNames.has(s.name)).map((s) => s.name),
    },
    {
      kind: "道具",
      want: (outline.props || []).length,
      have: (art.props || []).length,
      miss: (outline.props || [])
        .filter((p) => !propNames.has(String(p.name || p.id)))
        .map((p) => String(p.name || p.id)),
    },
  ];

  return (
    <div className="sec">
      <div className="h2">
        三方对账 <span className="chip gold">大纲 ↔ 角色表 ↔ 美术</span>
      </div>
      <div className="note" style={{ marginBottom: 10 }}>
        大纲列出的资产，下游 stage 是否都产出了对应条目。缺的那一条会在出图时卡住 —— 这里只如实报，
        **平台不再自己补条目、自己拼提示词**（那正是这次删掉的手搓）。
      </div>
      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th>类型</th>
              <th>大纲要求</th>
              <th>下游已产出</th>
              <th>缺口</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.kind}>
                <td className="nm">{a.kind}</td>
                <td className="mono">{a.want}</td>
                <td className="mono">{a.have}</td>
                <td className={`small ${a.miss.length ? "diff" : "dim"}`}>
                  {a.miss.length ? a.miss.join("、") : "—"}
                </td>
                <td>
                  {a.miss.length ? <span className="chip red">待补</span> : <span className="chip green">已齐</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="tiny dim" style={{ marginTop: 12, lineHeight: 1.9 }}>
        缺口的两条出路：① 改这里的大纲字段后重跑下游 stage；② 在大纲里删掉这个资产（确实不需要）。
      </div>
    </div>
  );
}
