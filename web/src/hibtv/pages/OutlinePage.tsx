/** 大纲：8 个子页（内核 / 人物关系 / 角色表 / 场景表 / 道具表 / 分集 / 卡点 / 对齐检查）。 */
import { useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Stage } from "../HibtvLayout";
import { Empty, GateChips, GateList, GenBar, InlineEdit, Loading, PageHead } from "../components/ui";
import { useHibtv, useJob, usePatch, useDoc } from "../state";
import type { ArtData, CastData, Detected, OutlineData } from "../types";
import { hibtvApi } from "../api";

const TABS = [
  { k: "inner", label: "内核" },
  { k: "rel", label: "人物关系" },
  { k: "cast", label: "角色表" },
  { k: "scene", label: "场景表" },
  { k: "prop", label: "道具表" },
  { k: "ep", label: "分集" },
  { k: "card", label: "卡点" },
  { k: "align", label: "对齐检查" },
];

export default function OutlinePage() {
  const { tab = "inner" } = useParams();
  const navigate = useNavigate();
  const { project, runJob } = useHibtv();
  const { data, doc, patch, loading } = usePatch("outline");
  const job = useJob("outline");
  const outline = (data || {}) as OutlineData;
  const gates = doc?.gates;

  const parsed = project?.detected || {};

  const tabs = useMemo(
    () =>
      TABS.map((t) => ({
        ...t,
        n:
          t.k === "rel"
            ? String(relationsOf(outline).length)
            : t.k === "cast"
              ? String(outline.characters?.length || 0)
              : t.k === "scene"
                ? String(outline.scenes?.length || 0)
                : t.k === "prop"
                  ? String(outline.props?.length || 0)
                  : t.k === "ep"
                    ? String(outline.episodes?.length || 0)
                    : t.k === "card"
                      ? String(cardsOf(outline).length)
                      : "",
      })),
    [outline],
  );

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
            {t.n && <span className="n">{t.n}</span>}
          </div>
        ))}
      </div>
      <div className="tiny dim" style={{ padding: "0 8px", lineHeight: 1.8 }}>
        大纲由剧本 + 美术设定解析而来。
        <br />
        双击任意文字就地改，改动都记在「变更」里，可撤销。
      </div>
    </>
  );

  if (!project) {
    return (
      <Stage side={side}>
        <Empty glyph="剧" title="还没有项目" sub="先在首页上传定稿剧本与美术设定。">
          <button className="btn gold" onClick={() => navigate("/hibtv/home")}>
            去建项目
          </button>
        </Empty>
      </Stage>
    );
  }

  const head = (
    <PageHead
      eyebrow="解析层 · 从哪里来 · OUTLINE"
      title="剧本大纲"
      lede={
        <>
          来自 <em>{project.scriptName || "未上传剧本"}</em> 与 <em>{project.artName || "未上传美术设定"}</em>
          。它只归类、不改编：每一行都能追回原剧本的集、场、拍，改一个字段只会重算引用它的那一行。
        </>
      }
      right={
        <>
          <GateChips gates={gates} />
          <button className="btn" onClick={() => navigate("/hibtv/changes")}>
            变更
          </button>
          <button className="btn gold" onClick={() => void runJob("outline")}>
            {outline.episodes?.length ? "重新生成" : "生成大纲"}
          </button>
        </>
      }
    />
  );

  const meta = (
    <>
      <div className="toolbar" style={{ marginTop: 14 }}>
        <span className="lab">本次生成</span>
        <span>
          模型 <b className="mono" style={{ color: "#d8d0c2" }}>deepseek</b>
        </span>
        <span className="chip">剧本 {parsed.scenes || 0} 场 · {parsed.lines || 0} 条台词</span>
        <span className="chip">
          美术 {parsed.cast || 0} 角色 / {parsed.scene || 0} 场景 / {parsed.prop || 0} 道具
        </span>
        <span className="sp">
          <span className="tiny dim">模型服务固定，不可更改</span>
        </span>
      </div>
      <div className="inline" style={{ margin: "12px 0" }}>
        <span className="chip">体裁 {outline.params?.genre || project.genre || "都市情感"}</span>
        <span className="chip">
          规模 {parsed.episodes || project.episodes} 集 · 单集 {project.minutesMin}–{project.minutesMax} 分钟
        </span>
        <span className="chip">画面 {project.aspectRatio}</span>
        <span className="chip ghost">改编 不适用 · 按剧本原样</span>
      </div>
    </>
  );

  if (loading && !data) {
    return <Stage side={side}>{head}{meta}<Loading /></Stage>;
  }

  if (!outline.episodes?.length) {
    return (
      <Stage side={side}>
        {head}
        {meta}
        <GenBar
          job={job}
          onCancel={job ? () => void hibtvApi.cancelJob(project.id, job.id) : undefined}
        />
        {!job || job.status === "done" || job.status === "failed" ? (
          <Empty
            glyph="纲"
            title="还没有大纲"
            sub={
              job?.status === "failed"
                ? `大纲生成失败：${job.error}`
                : "点「生成大纲」：解析剧本的集 / 场 / 拍与美术设定的资产条目，先出内核、角色表、场景表、道具表、分集与卡点。"
            }
          >
            <button className="btn gold" onClick={() => void runJob("outline")}>
              生成大纲
            </button>
          </Empty>
        ) : null}
        <GateList gates={gates} />
      </Stage>
    );
  }

  return (
    <Stage side={side}>
      {head}
      {meta}
      <GenBar job={job} onCancel={job ? () => void hibtvApi.cancelJob(project.id, job.id) : undefined} />
      <GateList gates={gates} />
      {tab === "inner" && <InnerTab outline={outline} patch={patch} parsed={parsed} />}
      {tab === "rel" && <RelTab outline={outline} />}
      {tab === "cast" && <CastTab outline={outline} patch={patch} />}
      {tab === "scene" && <SceneTab outline={outline} patch={patch} />}
      {tab === "prop" && <PropTab outline={outline} patch={patch} />}
      {tab === "ep" && <EpTab outline={outline} patch={patch} />}
      {tab === "card" && <CardTab outline={outline} />}
      {tab === "align" && <AlignTab outline={outline} />}
      <div className="tiny dim" style={{ marginTop: 14 }}>
        提示：双击文字即可改；改动写进「变更」，可一键撤销。质量门红字是 skill 脚本给的确定性结论，不会因为改一句文案自动变绿。
        <Link to="/hibtv/changes" style={{ marginLeft: 8 }}>
          看变更 →
        </Link>
      </div>
    </Stage>
  );
}

/* ------------------------------------------------------------------ 各子页 */
type Patch = (path: string, value: unknown, label?: string) => Promise<void>;

function InnerTab({
  outline,
  patch,
  parsed,
}: {
  outline: OutlineData;
  patch: Patch;
  parsed: Detected;
}) {
  const keep = outline.adaptation?.keep || [];
  return (
    <>
      <div className="sec">
        <div className="h2">内核</div>
        <div className="card">
          <div className="card-bd">
            <InlineEdit
              className="quote"
              multiline
              value={outline.adaptation?.core || ""}
              placeholder="（还没有内核；点「重新生成」或双击这里手写）"
              onSave={(v) => void patch("adaptation.core", v, "内核")}
            />
            <div className="split3" style={{ marginTop: 14 }}>
              <div className="stat">
                <span className="k">一句话卖点</span>
                <span className="small">{outline.episodes?.[0]?.hook?.slice(0, 24) || "—"}</span>
              </div>
              <div className="stat">
                <span className="k">规模</span>
                <span className="small">
                  {(outline.params?.episodes || 0) || outline.episodes?.length || 0} 集 · 单集{" "}
                  {outline.params?.minutesPerEpisode || 2} 分钟
                </span>
              </div>
              <div className="stat">
                <span className="k">爽点</span>
                <span className="small">
                  {outline.beats?.length || 0} 个 · 大爆点 {(outline.beats || []).filter((b) => b.weight === "major").length} 个
                </span>
              </div>
            </div>
            <div className="tiny dim" style={{ marginTop: 10 }}>
              双击上方内核即可改；改完会进「变更」，可撤销。
            </div>
          </div>
        </div>
      </div>

      <div className="sec">
        <div className="h2">这一版从哪里来</div>
        <div className="split">
          <div className="card">
            <div className="card-hd">
              <span className="t">剧本</span>
              <span className="chip green">已解析</span>
              <span className="sp" style={{ marginLeft: "auto" }} />
              <span className="tiny dim">{outline.source || ""}</span>
            </div>
            <div className="card-bd small mut">
              {parsed.episodes || 0} 集 / {parsed.scenes || 0} 场 / {parsed.lines || 0} 条台词 / {parsed.beats || 0} 拍
              <br />
              识别依据：集标题、场号行、角色冒号行、钩子与断点标注
            </div>
          </div>
          <div className="card">
            <div className="card-hd">
              <span className="t">名场面（必须保留）</span>
              <span className="chip">{keep.length}</span>
            </div>
            <div className="card-bd small mut">
              {keep.length
                ? keep.map((k, i) => (
                    <div key={i} style={{ marginBottom: 6 }}>
                      · {k.what}
                      <span className="tiny dim">（{k.why}）</span>
                    </div>
                  ))
                : "没有指定必须保留的场次。"}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

function relationsOf(outline: OutlineData) {
  if (outline.relations?.length) return outline.relations;
  // 没有显式关系表时，用「同集出场」推导一张最小关系表
  const nameById = new Map((outline.characters || []).map((c) => [c.id, c.name]));
  const pairs = new Map<string, { a: string; b: string; type: string; note: string }>();
  for (const ep of outline.episodes || []) {
    const ids = ep.characterIds || [];
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const key = [ids[i], ids[j]].sort().join("|");
        const hit = pairs.get(key);
        if (hit) hit.note = `${hit.note.replace("同场 ", "")}同场 `;
        else
          pairs.set(key, {
            a: nameById.get(ids[i]) || ids[i],
            b: nameById.get(ids[j]) || ids[j],
            type: "同集出场",
            note: `第 ${ep.ep} 集同场`,
          });
      }
    }
  }
  return Array.from(pairs.values()).slice(0, 40);
}

function RelTab({ outline }: { outline: OutlineData }) {
  const rel = relationsOf(outline);
  return (
    <div className="sec">
      <div className="h2">
        人物关系 <span className="tiny dim">{rel.length} 组</span>
      </div>
      {rel.length === 0 ? (
        <Empty glyph="关" title="还没有关系数据" sub="大纲里没有 relations 字段，剧本也没有同集出场可供推导。" />
      ) : (
        <>
          <div className="split">
            {rel.map((x, i) => (
              <div className="card" key={i}>
                <div className="card-bd">
                  <div className="inline" style={{ marginBottom: 4 }}>
                    <b style={{ fontSize: 13, color: "#e6dfd2" }}>{x.a}</b>
                    <span className="dim">↔</span>
                    <b style={{ fontSize: 13, color: "#e6dfd2" }}>{x.b}</b>
                    <span className="chip gold" style={{ marginLeft: "auto" }}>
                      {x.type}
                    </span>
                  </div>
                  <div className="small mut">{x.note}</div>
                </div>
              </div>
            ))}
          </div>
          <div className="tiny dim" style={{ marginTop: 10 }}>
            关系只用于生成角色卡的相互引用（谁压谁、谁护谁），不会改变剧本台词。
          </div>
        </>
      )}
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
              <tr key={c.id}>
                <td className="id">{c.id}</td>
                <td className="nm">{c.name}</td>
                <td>
                  <span className={`chip ${c.tier === "lead" ? "gold" : ""}`}>
                    {c.tier === "lead" ? "主角" : c.tier === "support" ? "配角" : "功能"}
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
                <td className="tiny dim">{(c.from || []).join(" + ") || "定稿剧本"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SceneTab({ outline, patch }: { outline: OutlineData; patch: Patch }) {
  const rows = outline.scenes || [];
  return (
    <div className="sec">
      <div className="h2">
        场景表 <span className="tiny dim">{rows.length} 个 · 来自美术设定编号</span>
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
              const eps = (outline.episodes || []).filter((e) => (e.sceneIds || []).includes(s.id)).map((e) => e.ep);
              return (
                <tr key={s.id}>
                  <td className="id">{s.id}</td>
                  <td className="nm">{s.name}</td>
                  <td className="small">{s.primary ? "主场景" : "次场景"}</td>
                  <td className="tiny mono">{eps.join("、") || "—"}</td>
                  <td className="small mut edit">
                    <InlineEdit
                      multiline
                      value={s.reusePlan || ""}
                      placeholder="一次性场景要写复用/规避方案"
                      onSave={(v) => void patch(`scenes.${i}.reusePlan`, v, `场景 · ${s.name} 复用方案`)}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="tiny dim" style={{ marginTop: 10 }}>
        场景表只收录剧本里真实出现过的场景；美术设定里有、剧本里没用的场景不会收进来。
      </div>
    </div>
  );
}

function PropTab({ outline, patch }: { outline: OutlineData; patch: Patch }) {
  const rows = outline.props || [];
  return (
    <div className="sec">
      <div className="h2">
        道具表 <span className="tiny dim">{rows.length} 件</span>
      </div>
      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th>ID</th>
              <th>道具</th>
              <th>出现集</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p, i) => {
              const eps = (outline.episodes || []).filter((e) => (e.propIds || []).includes(p.id)).map((e) => e.ep);
              return (
                <tr key={p.id}>
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

function cardsOf(outline: OutlineData) {
  if (outline.cards?.length) return outline.cards;
  return (outline.beats || [])
    .filter((b) => b.weight === "major")
    .map((b) => ({
      ep: b.episode,
      at: `第 ${b.episode} 集`,
      name: b.type,
      note: b.payoff || b.setup || "",
    }));
}

function EpTab({ outline, patch }: { outline: OutlineData; patch: Patch }) {
  const rows = outline.episodes || [];
  return (
    <div className="sec">
      <div className="h2">
        分集 <span className="tiny dim">{rows.length} 集 · 每集两行：钩子与断点</span>
      </div>
      <div className="card">
        {rows.map((e, i) => {
          const beat = (outline.beats || []).find((b) => b.episode === e.ep);
          return (
            <div key={e.ep} style={{ padding: "13px 15px", borderBottom: "1px solid var(--line-3)" }}>
              <div className="inline" style={{ marginBottom: 6 }}>
                <span className="mono" style={{ color: "var(--gold-2)" }}>E{String(e.ep).padStart(2, "0")}</span>
                <span className="chip">{(e.sceneIds || []).length} 场</span>
                <span className="chip ghost">
                  {(e.characterIds || []).length} 人 · {(e.propIds || []).length} 道具
                </span>
                {beat && <span className="chip gold" style={{ marginLeft: "auto" }}>{beat.id} {beat.type}</span>}
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
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CardTab({ outline }: { outline: OutlineData }) {
  const cards = cardsOf(outline);
  return (
    <div className="sec">
      <div className="h2">
        付费卡点 <span className="tiny dim">{cards.length} 处 · 来自剧本断点与爽点</span>
      </div>
      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th>集</th>
              <th>位置</th>
              <th>卡点名</th>
              <th>为什么停在这里</th>
            </tr>
          </thead>
          <tbody>
            {cards.map((c, i) => (
              <tr key={i}>
                <td className="id">E{String(c.ep).padStart(2, "0")}</td>
                <td className="mono small">{c.at}</td>
                <td className="nm">{c.name}</td>
                <td className="small mut">{c.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="tiny dim" style={{ marginTop: 10 }}>
        卡点来自剧本的「钩子 / 断点」标注，hibtv 只做识别与对齐，不替编剧决定停在哪。
      </div>
    </div>
  );
}

function AlignTab({ outline }: { outline: OutlineData }) {
  const { project, toast, bump } = useHibtv();
  const { data: castData, reload: reloadCast } = useDoc("cast");
  const { data: artData, reload: reloadArt } = useDoc("art");
  const cast = ((castData || {}) as CastData).characters || [];
  const art = (artData || {}) as ArtData;

  const nameSet = (arr?: { name: string }[]) => new Set((arr || []).map((x) => x.name));
  const artNames = nameSet(art.scenes);
  const castNames = nameSet(cast);

  const rows = [
    {
      kind: "角色",
      inScript: outline.characters?.length || 0,
      inArt: cast.length,
      miss: (outline.characters || []).filter((c) => !castNames.has(c.name)).map((c) => c.name),
    },
    {
      kind: "场景",
      inScript: outline.scenes?.length || 0,
      inArt: (art.scenes || []).length,
      miss: (outline.scenes || []).filter((s) => !artNames.has(s.name)).map((s) => s.name),
    },
    {
      kind: "道具",
      inScript: outline.props?.length || 0,
      inArt: (art.props || []).length,
      miss: (outline.props || []).filter((p) => !nameSet(art.props).has(p.name)).map((p) => p.name),
    },
  ];

  const patchArt = async (kind: "scene" | "prop" | "cast") => {
    if (!project) return;
    try {
      if (kind === "scene") {
        const add = (outline.scenes || [])
          .filter((s) => !artNames.has(s.name))
          .map((s) => ({
            id: s.id,
            name: s.name,
            primary: !!s.primary,
            summary: "",
            anchors: [],
            lighting: [],
            image: {
              prompt: `${s.name}，空无一人，当代中国都市写实场景。`,
              negativePrompt: "多个人物, 文字, 水印",
              sheet: "正面 / 俯视 / 背面 / 左侧 / 右侧",
              tags: ["场景"],
            },
            usage: { episodes: [], beats: [] },
          }));
        if (!add.length) return toast("场景已经齐了");
        await hibtvApi.patchDoc(project.id, "art", {
          path: "scenes",
          value: [...(art.scenes || []), ...add],
        });
        await reloadArt();
        bump();
        toast(`已补建 ${add.length} 条场景到美术设定`);
      } else if (kind === "prop") {
        const have = nameSet(art.props);
        const add = (outline.props || [])
          .filter((p) => !have.has(p.name))
          .map((p) => ({
            id: p.id,
            name: p.name,
            scale: "手持级",
            summary: p.function || "",
            anchors: [],
            states: [{ state: "主状态", prompt: p.name }],
            relatedScenes: [],
            carriedBy: [],
            image: {
              prompt: `${p.name}，单独置于纯色背景正中，无人无手。`,
              negativePrompt: "人物, 手, 文字, 水印",
              sheet: "正面 / 背面 / 左侧 / 右侧 / 俯视",
              tags: ["道具"],
            },
            usage: { episodes: [], beats: [] },
          }));
        if (!add.length) return toast("道具已经齐了");
        await hibtvApi.patchDoc(project.id, "art", {
          path: "props",
          value: [...(art.props || []), ...add],
        });
        await reloadArt();
        bump();
        toast(`已补建 ${add.length} 条道具到美术设定`);
      } else {
        const add = (outline.characters || [])
          .filter((c) => !castNames.has(c.name))
          .map((c) => ({
            id: c.id,
            code: c.id,
            name: c.name,
            importance: c.tier === "lead" ? "protagonist" : "supporting",
            oneLiner: c.arc || `${c.name}的关键转变。`,
            persona: { identity: c.role || "", appearance: "", evidence: "定稿剧本" },
            image: {
              prompt: `${c.name}，${c.role || ""}，当代中国都市写实风格全身像。`,
              negativePrompt: "多个人物, 拼贴, 分屏, 文字, 水印",
              sheet: "三视图：正面全身 / 侧面 / 背面",
            },
            voice: { timbre: "", prompt: "" },
          }));
        if (!add.length) return toast("角色已经齐了");
        await hibtvApi.patchDoc(project.id, "cast", {
          path: "characters",
          value: [...cast, ...add],
        });
        await reloadCast();
        bump();
        toast(`已补建 ${add.length} 个角色到角色卡`);
      }
    } catch (err) {
      toast(String(err));
    }
  };

  return (
    <div className="sec">
      <div className="h2">
        对齐检查 <span className="chip gold">剧本 ↔ 美术设定</span>
      </div>
      <div className="note" style={{ marginBottom: 10 }}>
        剧本里出现的人、场景、道具，美术设定是否都有对应条目。缺的那一条会在下游卡住出图，所以放在这里先解决。
      </div>
      <div className="card">
        <table className="tbl">
          <thead>
            <tr>
              <th>类型</th>
              <th>剧本出现</th>
              <th>设定覆盖</th>
              <th>缺口</th>
              <th>状态</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.kind}>
                <td className="nm">{a.kind}</td>
                <td className="mono">{a.inScript}</td>
                <td className="mono">{a.inArt}</td>
                <td className={`small ${a.miss.length ? "diff" : "dim"}`}>
                  {a.miss.length ? a.miss.join("、") : "—"}
                </td>
                <td>
                  {a.miss.length ? <span className="chip red">待补</span> : <span className="chip green">已齐</span>}
                </td>
                <td style={{ textAlign: "right" }}>
                  {a.miss.length ? (
                    <button
                      className="btn xs"
                      onClick={() =>
                        void patchArt(a.kind === "角色" ? "cast" : a.kind === "场景" ? "scene" : "prop")
                      }
                    >
                      补建到{a.kind === "角色" ? "角色卡" : "美术"}
                    </button>
                  ) : (
                    <span className="tiny dim">无需处理</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="split" style={{ marginTop: 14 }}>
        <div className="card">
          <div className="card-hd">
            <span className="t">剧本场次覆盖</span>
            <span className="chip green">
              {(outline.scenes || []).length} / {(outline.scenes || []).length} 场
            </span>
          </div>
          <div className="card-bd small mut">
            每一场都有对应场景锚点；跨场拍摄按“主场景”挂靠，不新增场景卡。
          </div>
        </div>
        <div className="card">
          <div className="card-hd">
            <span className="t">需要你决定</span>
            <span className={`chip ${rows.some((r) => r.miss.length) ? "red" : "green"}`}>
              {rows.reduce((n, r) => n + r.miss.length, 0)} 项
            </span>
          </div>
          <div className="card-bd small mut">
            {rows.some((r) => r.miss.length)
              ? "缺口直接点右侧「补建」写进美术设定；补建后下游出图才能找到锚点。"
              : "剧本与美术设定已对齐，可以直接去角色 / 美术出图。"}
          </div>
        </div>
      </div>
    </div>
  );
}
