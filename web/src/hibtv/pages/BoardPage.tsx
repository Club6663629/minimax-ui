/** 分镜：总表 → 本集段表 → 一段一页（镜头素材 + 可选分镜图 + 分镜源提示词）。
 *
 * 口径（见 plan §4.1/§4.2）：
 * - 路线 A 以每镜引用素材为视频输入；段内汇总后一次生成；
 * - 分镜图是可选的构图控制，不阻塞路线 A；
 * - 段长 ≤ {cap}s（H3 只出 5+17n 帧：311 帧 = 12.9583s），超限只能重跑本集 storyboard；
 * - 每个切可生成一张可选分镜图（`cuts[].frame`），但默认不参与视频 Picture 映射。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { hibtvApi, mediaUrl } from "../api";
import { Stage } from "../HibtvLayout";
import { CodexLight, useCodexGate } from "../components/CodexLight";
import { ArtifactsDrawer } from "../components/StageArtifacts";
import {
  Empty,
  GateChips,
  GateList,
  GenBar,
  InlineEdit,
  Loading,
  PageHead,
  VideoBox,
  fmtTime,
} from "../components/ui";
import { errText, useDoc, useHibtv, useJob, usePatch } from "../state";
import type {
  FrameReference,
  FrameList,
  HibtvFrame,
  OutlineData,
  ReferenceAsset,
  RenderItem,
  Segment,
  SegmentList,
  StoryboardData,
} from "../types";

const REF_KIND_LABEL: Record<string, string> = {
  scene: "场景",
  cast: "角色",
  prop: "道具",
  continuity: "连续性",
};

const REF_ROLE_LABEL: Record<string, string> = {
  "scene-top": "定场图",
  scene: "场景图",
  "cast-anchor": "角色锚点",
  "cast-face": "角色面部",
  prop: "道具状态",
  "continuity-frame": "上一镜",
};

/* ------------------------------------------------------------------ 数据 hooks */
/** 多集段表：只对存在的集发起请求。 */
function useSegmentMap(eps: number[]) {
  const { project, revision, toast } = useHibtv();
  const [map, setMap] = useState<Record<number, SegmentList>>({});
  const [loading, setLoading] = useState(false);
  const key = eps.join(",");

  const load = useCallback(async () => {
    if (!project || !key) {
      setMap({});
      return;
    }
    setLoading(true);
    try {
      const rows = await Promise.all(
        key.split(",").map((n) => hibtvApi.listSegments(project.id, Number(n))),
      );
      const next: Record<number, SegmentList> = {};
      for (const row of rows) next[row.ep] = row;
      setMap(next);
    } catch (err) {
      toast(errText(err));
    } finally {
      setLoading(false);
    }
  }, [project, key, toast]);

  useEffect(() => {
    void load();
  }, [load, revision]);

  return { map, loading, reload: load };
}

/** 本集镜头记录与可选分镜图。 */
function useFrames(ep: number) {
  const { project, revision, toast } = useHibtv();
  const [list, setList] = useState<FrameList | null>(null);

  const load = useCallback(async () => {
    if (!project || !ep) {
      setList(null);
      return;
    }
    try {
      setList(await hibtvApi.listFrames(project.id, ep));
    } catch (err) {
      toast(errText(err));
    }
  }, [project, ep, toast]);

  useEffect(() => {
    void load();
  }, [load, revision]);

  return { list, reload: load };
}

function useRenders(ep: number) {
  const { project, revision, toast } = useHibtv();
  const [renders, setRenders] = useState<RenderItem[]>([]);

  const load = useCallback(async () => {
    if (!project || !ep) {
      setRenders([]);
      return;
    }
    try {
      setRenders(await hibtvApi.listRenders(project.id, ep));
    } catch (err) {
      toast(errText(err));
    }
  }, [project, ep, toast]);

  useEffect(() => {
    void load();
  }, [load, revision]);

  return { renders, reload: load };
}

/** 把本集帧按段分组，拿到「已出 / 共几」的快照。 */
function frameStats(frames: HibtvFrame[] | undefined, segId: string) {
  const rows = (frames || []).filter((f) => f.segId === segId);
  return { done: rows.filter((f) => f.ready).length, total: rows.length, rows };
}

function refKey(ref: Pick<FrameReference, "assetId" | "view" | "role">) {
  return `${ref.assetId}:${ref.view}:${ref.role}`;
}

function ShotReferences({
  frame,
  onEdit,
}: {
  frame: HibtvFrame;
  onEdit: () => void;
}) {
  const refs = frame.references || [];
  return (
    <div className="shot-refs">
      <div className="shot-ref-row">
        {refs.slice(0, 4).map((ref) => (
          <div
            key={refKey(ref)}
            className="shot-ref"
            title={`${REF_KIND_LABEL[ref.kind] || ref.kind} · ${ref.name} · ${REF_ROLE_LABEL[ref.role] || ref.role}`}
          >
            {ref.url ? (
              <img src={mediaUrl(ref.url)} alt="" loading="lazy" />
            ) : (
              <span>{ref.code.slice(0, 1) || "图"}</span>
            )}
            <b>{REF_ROLE_LABEL[ref.role] || ref.role}</b>
          </div>
        ))}
        {refs.length > 4 && <span className="chip tiny">+{refs.length - 4}</span>}
        <button className="btn xs ghost" onClick={onEdit}>
          {refs.length ? "调素材" : "＋ 加素材"}
        </button>
      </div>
      <div className="tiny dim">
        {refs.length
          ? refs.map((ref) => `${ref.code} ${REF_ROLE_LABEL[ref.role] || ref.role}`).join(" · ")
          : "未选择素材；生成时会按镜头人物、场景和道具自动挂载"}
      </div>
    </div>
  );
}

function ReferenceEditor({
  frame,
  catalog,
  onSave,
  onClose,
}: {
  frame: HibtvFrame;
  catalog: ReferenceAsset[];
  onSave: (items: Pick<FrameReference, "assetId" | "view" | "role" | "note">[]) => Promise<void>;
  onClose: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const current = frame.references || [];
  const [picked, setPicked] = useState<string[]>(
    current.map((ref) => refKey(ref)),
  );
  const [notes, setNotes] = useState<Record<string, string>>(
    Object.fromEntries(current.map((ref) => [refKey(ref), ref.note || ""])),
  );
  const byKey = useMemo(
    () => new Map(catalog.map((item) => [refKey(item), item])),
    [catalog],
  );
  const selected = picked.map((key) => byKey.get(key)).filter(Boolean) as ReferenceAsset[];
  const grouped = useMemo(() => {
    const result: Record<string, ReferenceAsset[]> = {};
    for (const item of catalog) {
      (result[item.kind] ||= []).push(item);
    }
    return result;
  }, [catalog]);

  const toggle = (item: ReferenceAsset) => {
    const key = refKey(item);
    setPicked((rows) => (rows.includes(key) ? rows.filter((row) => row !== key) : [...rows, key]));
  };

  return (
    <div className="reference-editor">
      <div className="reference-editor-hd">
        <div>
          <div className="eyebrow">镜 {frame.cutIndex} · 引用素材</div>
          <div className="h2" style={{ margin: "4px 0" }}>选择这一镜真正发给视频模型的图片</div>
          <div className="tiny dim">场景主图、定场图、角色锚点和道具状态图分别声明用途；同一角色的大头照与全身图仍只代表一个人。</div>
        </div>
        <button className="btn xs ghost" onClick={onClose}>✕</button>
      </div>
      <div className="reference-editor-body">
        <div className="reference-current">
          <div className="panel-title">已选 {selected.length} 张</div>
          {selected.length ? (
            <div className="reference-grid selected">
              {selected.map((item) => (
                <button key={refKey(item)} className="reference-card on" onClick={() => toggle(item)}>
                  <img src={mediaUrl(item.url)} alt="" loading="lazy" />
                  <span>{item.code} · {REF_ROLE_LABEL[item.role] || item.role}</span>
                  <small>{item.name} / {item.viewLabel}</small>
                </button>
              ))}
            </div>
          ) : (
            <div className="empty-mini">空白清单会在生成时恢复自动挂载。</div>
          )}
          {selected.length > 0 && (
            <div className="reference-notes">
              {selected.map((item) => {
                const key = refKey(item);
                return (
                  <label key={key}>
                    <span>{item.code} · {REF_ROLE_LABEL[item.role] || item.role}</span>
                    <input
                      value={notes[key] || ""}
                      placeholder="备注，如：只参考服装"
                      onChange={(event) =>
                        setNotes((rows) => ({ ...rows, [key]: event.target.value }))
                      }
                    />
                  </label>
                );
              })}
            </div>
          )}
        </div>
        <div className="reference-library">
          {["scene", "cast", "prop"].filter((kind) => grouped[kind]?.length).map((kind) => (
            <div className="reference-kind" key={kind}>
              <div className="panel-title">{REF_KIND_LABEL[kind] || kind}</div>
              <div className="reference-grid">
                {grouped[kind].map((item) => (
                  <button
                    key={refKey(item)}
                    className={`reference-card ${picked.includes(refKey(item)) ? "on" : ""}`}
                    onClick={() => toggle(item)}
                  >
                    <img src={mediaUrl(item.url)} alt="" loading="lazy" />
                    <span>{item.code} · {REF_ROLE_LABEL[item.role] || item.role}</span>
                    <small>{item.name} / {item.viewLabel}</small>
                  </button>
                ))}
              </div>
            </div>
          ))}
          {!catalog.length && <div className="empty-mini">还没有已确认的可用素材。</div>}
        </div>
      </div>
      <div className="reference-editor-ft">
        <button className="btn xs" onClick={() => setPicked([])}>恢复自动挂载</button>
        <span className="tiny dim">{selected.length} / 9</span>
        <span className="sp" style={{ marginLeft: "auto" }} />
        <button className="btn" onClick={onClose}>取消</button>
        <button
          className="btn gold"
          disabled={saving || selected.length > 9}
          onClick={() => {
            setSaving(true);
            void onSave(
              selected.map((item) => ({
                assetId: item.assetId,
                view: item.view,
                role: item.role,
                note: notes[refKey(item)] || "",
              })),
            ).finally(() => setSaving(false));
          }}
        >
          {saving ? "保存中…" : "保存引用素材"}
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ 可选分镜图 */
/** 可选分镜图：不再作为路线 A 视频生成的硬前置。 */
function FrameStrip({
  projectId,
  seg,
  onChanged,
  ratio = "9 / 16",
}: {
  projectId: number;
  seg: Segment;
  onChanged: () => void;
  ratio?: string;
}) {
  const { toast } = useHibtv();
  const [busy, setBusy] = useState(0);
  const inputs = useRef<Record<number, HTMLInputElement | null>>({});
  const frames = seg.frames || [];
  const missing = seg.missingFrames || [];

  const draw = async (frame: HibtvFrame) => {
    setBusy(frame.id);
    try {
      await hibtvApi.frameImage(projectId, frame.id);
      toast(`已排队：${seg.id} f${frame.cutIndex} 可选分镜图`);
      window.setTimeout(onChanged, 800);
    } catch (err) {
      toast(errText(err));
    } finally {
      setBusy(0);
    }
  };

  const drawAll = async () => {
    try {
      await hibtvApi.createJob(projectId, {
        kind: "images",
        payload: { mode: "frames", ep: seg.ep, seg_id: seg.id },
      });
      toast(`已排入可选分镜图队列：${frames.length} 张（缺 ${missing.length} 张）`);
      window.setTimeout(onChanged, 800);
    } catch (err) {
      toast(errText(err));
    }
  };

  const upload = async (frame: HibtvFrame, file: File) => {
    setBusy(frame.id);
    try {
      await hibtvApi.uploadFrame(projectId, frame.id, file);
      toast(`已上传 f${frame.cutIndex}，作为可选分镜图`);
      onChanged();
    } catch (err) {
      toast(errText(err));
    } finally {
      setBusy(0);
    }
  };

  if (!frames.length) {
    return (
      <div className="tiny dim">
        这一段还没有镜头记录——请先运行分镜。
      </div>
    );
  }

  return (
    <>
      <div className="inline" style={{ marginBottom: 8, flexWrap: "wrap" }}>
        <span className={`chip ${missing.length ? "red" : "green"}`}>
          可选图 {frames.length - missing.length}/{frames.length}
          {missing.length ? ` · 未生成 f${missing.join(" / f")}` : " · 可选图已齐"}
        </span>
        <span className="chip ghost">视频默认走镜头素材；这里的 f1..fN 仅用于构图审阅</span>
        <span style={{ marginLeft: "auto" }} />
        <button className="btn xs" onClick={() => void drawAll()}>
          {missing.length ? `补可选图 ${missing.length} 张` : "重出可选分镜图"}
        </button>
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(112px, 1fr))",
          gap: 8,
        }}
      >
        {frames.map((f) => {
          const isMissing = !f.ready;
          return (
            <div
              key={f.id}
              style={{
                border: `1px solid ${isMissing ? "rgba(224,120,90,.5)" : "var(--line-3)"}`,
                borderRadius: 6,
                overflow: "hidden",
                background: "#141210",
              }}
            >
              <a
                href={f.url ? mediaUrl(f.url) : undefined}
                target="_blank"
                rel="noreferrer"
                style={{ display: "block", position: "relative", aspectRatio: ratio, background: "#0f0e0c" }}
                title={f.prompt}
              >
                {f.url ? (
                  <img
                    src={mediaUrl(f.url)}
                    alt={`f${f.cutIndex}`}
                    loading="lazy"
                    style={{ width: "100%", height: "100%", objectFit: "cover" }}
                  />
                ) : (
                  <span
                    className="tiny dim"
                    style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}
                  >
                    可选
                  </span>
                )}
                <span
                  className="chip tiny"
                  style={{ position: "absolute", left: 4, top: 4, background: "rgba(0,0,0,.66)" }}
                >
                  f{f.cutIndex}
                </span>
              </a>
              <div className="inline" style={{ padding: "4px 6px", gap: 4 }}>
                <button
                  className="btn xs ghost"
                  disabled={busy === f.id}
                  onClick={() => void draw(f)}
                  title="用独立 HiBTV 生图工作流生成 / 重出这一张可选分镜图"
                >
                  {isMissing ? "出可选图" : "重出"}
                </button>
                <button
                  className="btn xs ghost"
                  onClick={() => inputs.current[f.id]?.click()}
                  title="上传真图顶掉这一张"
                >
                  上传
                </button>
                <input
                  ref={(el) => {
                    inputs.current[f.id] = el;
                  }}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  style={{ display: "none" }}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    if (file) void upload(f, file);
                  }}
                />
                {f.version > 1 && <span className="chip tiny">v{f.version}</span>}
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ 页面入口 */
export default function BoardPage() {
  const { ep: epParam, seg: segParam } = useParams();
  const ep = epParam ? Math.max(1, Number(epParam) || 1) : 0;

  if (segParam) return <SegmentPage ep={ep} segId={segParam} />;
  if (!ep) return <BoardListPage />;
  return <BoardEpisodePage ep={ep} />;
}

/* ------------------------------------------------------------------ 总表 */
function BoardListPage() {
  const navigate = useNavigate();
  const { project, status } = useHibtv();
  const { data: outlineData } = useDoc("outline");
  const outline = (outlineData || {}) as OutlineData;

  const eps = useMemo(() => {
    const total = Math.max(1, project?.episodes || 1);
    return Array.from({ length: total }, (_, i) => i + 1);
  }, [project?.episodes]);

  const withBoard = useMemo(
    () => eps.filter((n) => project?.docs?.[`storyboard#${n}`]?.status === "done"),
    [eps, project?.docs],
  );
  const { map } = useSegmentMap(withBoard);

  const scriptOf = (n: number) => project?.docs?.[`script#${n}`]?.status === "done";

  const totals = useMemo(() => {
    let segs = 0;
    let cuts = 0;
    let secs = 0;
    let over = 0;
    for (const row of Object.values(map)) {
      segs += row.segments.length;
      cuts += row.segments.reduce((n, s) => n + (s.cuts || []).length, 0);
      secs += row.segments.reduce((n, s) => n + (s.planSeconds || 0), 0);
      over += row.segments.filter((s) => s.oversize).length;
    }
    return { segs, cuts, secs, over };
  }, [map]);

  if (!project) {
    return (
      <Stage>
        <Empty glyph="剧" title="还没有项目" sub="先在首页上传定稿剧本与美术设定。">
          <button className="btn gold" onClick={() => navigate("/hibtv/home")}>
            去建项目
          </button>
        </Empty>
      </Stage>
    );
  }

  const cap = status?.segMaxSeconds || 13;

  return (
    <Stage>
      <PageHead
        eyebrow="下游 · 怎么拍 · STORYBOARD"
        title={
          <>
            分镜{" "}
            <span className="chip" style={{ verticalAlign: "middle" }}>
              {totals.segs} 段 · {totals.cuts} 镜
            </span>
          </>
        }
        lede="集 → 段 → 镜头三层：每镜选择引用素材，每段汇总后一次生成视频；分镜图只作为可选的构图控制。"
        right={
          <>
            <CodexLight />
            <span className="chip gold">段 ≤ {cap}s</span>
            <span className="chip">切 {status?.frameSize?.[project.aspectRatio || "9:16"]?.join("×") || "768×1344"}</span>
            {totals.over > 0 && <span className="chip red">{totals.over} 段超限</span>}
          </>
        }
      />

      <div className="card" style={{ overflow: "hidden" }}>
        <table className="tbl">
          <thead>
            <tr>
              <th>集</th>
              <th>状态</th>
              <th>段</th>
              <th>镜</th>
              <th>总时长</th>
              <th>超 {cap}s 段</th>
              <th>对账</th>
              <th>进入</th>
            </tr>
          </thead>
          <tbody>
            {eps.map((n) => {
              const row = map[n];
              const done = project.docs?.[`storyboard#${n}`]?.status === "done";
              const segs = row?.segments || [];
              const cuts = segs.reduce((a, s) => a + (s.cuts || []).length, 0);
              const secs = segs.reduce((a, s) => a + (s.planSeconds || 0), 0);
              const over = segs.filter((s) => s.oversize).length;
              const bad = segs.filter(
                (s) => (s.pictureCount || s.frameCount || 0) !== (s.cuts || []).length,
              ).length;
              return (
                <tr key={n} style={{ cursor: "pointer" }} onClick={() => navigate(`/hibtv/storyboard/${n}`)}>
                  <td className="id">E{n}</td>
                  <td className="small">
                    {done ? (
                      <span className="chip green">已完成 {segs.length} 段</span>
                    ) : scriptOf(n) ? (
                      <span className="chip gold">有剧本 · 未分镜</span>
                    ) : (
                      <span className="chip ghost">尚未剧本</span>
                    )}
                  </td>
                  <td className="mono">{done ? segs.length : "—"}</td>
                  <td className="mono">{done ? cuts : "—"}</td>
                  <td className="mono">{done ? `${secs.toFixed(1)}s` : "—"}</td>
                  <td className="mono">
                    {done ? (
                      over ? (
                        <span className="chip red">{over} 段</span>
                      ) : (
                        <span className="chip green">无</span>
                      )
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="small">
                    {done ? (
                      bad ? (
                        <span className="chip red">切数≠图数</span>
                      ) : (
                        <span className="chip green">切=图</span>
                      )
                    ) : (
                      <span className="dim">—</span>
                    )}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <span className="chip tiny">{done ? "看本集" : "去生成"}</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="tiny dim" style={{ marginTop: 12, lineHeight: 1.9 }}>
        分镜由 codex 跑 novel-storyboard：读剧本 + 美术 + 角色表 + 站位，产出 `*-storyboard.json`、
        `manifest.json`（图位清单）与每段 `prompt.md`。
        <br />
        超 {cap}s 的段平台**不拆、不改提示词**，只能带提示重跑本集 storyboard。
        {outline.params?.episodes ? `本剧设定 ${outline.params.episodes} 集。` : ""}
      </div>
    </Stage>
  );
}

/* ------------------------------------------------------------------ 本集段表 */
function BoardEpisodePage({ ep }: { ep: number }) {
  const navigate = useNavigate();
  const { project, status, runJob, toast, openLayer, closeLayer } = useHibtv();
  const { data, doc } = usePatch("storyboard", ep);
  const { data: outlineData } = useDoc("outline");
  const job = useJob("storyboard", ep);
  const { map, reload } = useSegmentMap([ep]);
  const { list: frameList, reload: reloadFrames } = useFrames(ep);
  const { renders } = useRenders(ep);
  const gate = useCodexGate();
  const list = map[ep];
  const segments = list?.segments || [];
  const outline = (outlineData || {}) as OutlineData;
  const latest = renders[0];

  const cuts = segments.reduce((n, s) => n + (s.cuts || []).length, 0);
  const secs = segments.reduce((n, s) => n + (s.planSeconds || 0), 0);
  const withVideo = segments.filter((s) => s.url).length;
  const frames = frameList?.frames || [];
  const cap = status?.segMaxSeconds || 13;

  const sceneName = (idx?: number) => (idx && outline.scenes?.[idx - 1]?.name) || `场景 ${idx || 1}`;

  const rerun = () => {
    if (!gate.ok) {
      toast(gate.why);
      return;
    }
    void runJob("storyboard", ep);
  };

  const side = (
    <>
      <div className="side-head">分镜 · STORYBOARD</div>
      <div className="subnav">
        <div className="nav-item" onClick={() => navigate("/hibtv/storyboard")}>
          总表
        </div>
        <div className="nav-item on" onClick={() => navigate(`/hibtv/storyboard/${ep}`)}>
          本集总表
          <span className="n">{segments.length}</span>
        </div>
      </div>
      <div className="side-head" style={{ paddingTop: 2 }}>
        第 {ep} 集
      </div>
      <div className="ep-list">
        {segments.map((s) => {
          const st = frameStats(frames, s.id);
          return (
            <div
              key={s.id}
              className="mini-row"
              onClick={() => navigate(`/hibtv/storyboard/${ep}/${s.id}`)}
              title={`图位 ${st.done}/${st.total}`}
            >
              <span className={s.url ? "ok" : s.oversize ? "dot bad" : "dot"}>●</span>
              {s.id}
              <span style={{ marginLeft: "auto", color: "#6d665c" }}>
                {s.planSeconds.toFixed(1)}s · {(s.cuts || []).length}镜
              </span>
            </div>
          );
        })}
        {!segments.length && (
          <div className="tiny dim" style={{ padding: 8 }}>
            还没有分镜段
          </div>
        )}
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
      eyebrow="下游 · 怎么拍 · STORYBOARD"
      title={
        <>
          分镜 / 第 {ep} 集{" "}
          <span className="chip" style={{ verticalAlign: "middle" }}>
            {segments.length} 段
          </span>{" "}
          <span className="chip" style={{ verticalAlign: "middle" }}>
            {cuts} 镜
          </span>
        </>
      }
      lede="整集成片按当前段清单拼接；每段按镜头素材汇总生成，可选分镜图缺失不阻塞出片。"
      right={
        <>
          <CodexLight />
          <GateChips gates={doc?.gates} none="分镜没有结构化门结果" />
          <button
            className="btn ghost"
            onClick={() =>
              openLayer(<ArtifactsDrawer projectId={project.id} jobId={job?.id || 0} onClose={closeLayer} />)
            }
          >
            产物 / 评审报告
          </button>
          <button className="btn" onClick={() => navigate(`/hibtv/video/${ep}`)}>
            出视频
          </button>
          <button className="btn gold" onClick={rerun} disabled={!gate.ok} title={gate.why}>
            {segments.length ? "重新生成本集" : "生成本集分镜"}
          </button>
        </>
      }
    />
  );

  const compose = async () => {
    if (!project) return;
    try {
      await hibtvApi.renderEpisode(project.id, ep);
      toast(`已排队：第 ${ep} 集整集拼接`);
    } catch (err) {
      toast(errText(err));
    }
  };

  return (
    <Stage side={side}>
      {head}
      <GenBar job={job} onCancel={job ? () => void hibtvApi.cancelRemoteJob(project.id, job.id) : undefined} />
      <GateList gates={doc?.gates} />
      {!data && !segments.length ? <Loading /> : null}

      <div className="card" style={{ margin: "14px 0 12px" }}>
        <div className="card-hd">
          <span className="t">整集成片</span>
          <span className="chip gold">
            {withVideo} / {segments.length} 段有片
          </span>
          <span className="chip">{frameList?.done || 0} / {frameList?.total || 0} 帧</span>
          {latest && <span className="chip">v{latest.version}</span>}
          <span style={{ marginLeft: "auto" }} />
          <button className="btn xs" onClick={() => void compose()} disabled={!withVideo}>
            {latest ? "重新合成" : "合成成片"}
          </button>
        </div>
        <div className="card-bd">
          <VideoBox
            src={latest?.url}
            label={`第 ${ep} 集成片`}
            view={latest ? `${fmtTime(latest.durationSec)} · ${segments.length} 段 · v${latest.version}` : "还没有成片"}
            ratio={project.aspectRatio === "16:9" ? "16 / 9" : "9 / 16"}
            maxHeight={320}
          />
          <div className="timeline" style={{ marginTop: 10 }}>
            {segments.map((s, i) => (
              <span
                key={s.id}
                className={`tl ${s.oversize ? "bad" : ""}`}
                onClick={() => navigate(`/hibtv/storyboard/${ep}/${s.id}`)}
                title={s.oversize ? "段长超过上限：需重跑本集 storyboard" : `${s.planSeconds.toFixed(1)}s`}
              >
                {i + 1}
              </span>
            ))}
          </div>
          <div className="inline tiny dim" style={{ marginTop: 8 }}>
            <span>计划 {secs.toFixed(1)}s</span>
            {latest && <span>· 成片 {latest.durationSec.toFixed(1)}s</span>}
            {latest && <span>· {(latest.size / 1024 / 1024).toFixed(1)} MB</span>}
            <span className="sp" style={{ marginLeft: "auto" }} />
            <Link to={`/hibtv/video/${ep}`}>交付页 →</Link>
          </div>
        </div>
      </div>

      <div className="card" style={{ overflow: "hidden" }}>
        <div
          className="segrow"
          style={{
            background: "rgba(255,255,255,.022)",
            color: "#7d766b",
            fontSize: 10,
            letterSpacing: ".08em",
          }}
        >
          <span>段</span>
          <span>场次</span>
          <span>镜</span>
          <span>时长</span>
          <span>图位</span>
          <span>提示词</span>
          <span>成片</span>
          <span>切点</span>
          <span />
        </div>
        {segments.map((s) => {
          const st = frameStats(frames, s.id);
          return (
            <div key={s.id} className="segrow" onClick={() => navigate(`/hibtv/storyboard/${ep}/${s.id}`)}>
              <span className="sid">{s.id}</span>
              <span>{sceneName(s.data?.sceneIndex)}</span>
              <span className="mono">{(s.cuts || []).length}</span>
              <span className={s.oversize ? "" : "mono"}>
                {s.oversize ? (
                  <span className="chip red tiny">{s.planSeconds.toFixed(1)}s 超限</span>
                ) : (
                  `${s.planSeconds.toFixed(1)}s`
                )}
              </span>
              <span>
                {st.total ? (
                  <span className={`chip tiny ${st.done === st.total ? "green" : "gold"}`}>
                    {st.done}/{st.total}
                  </span>
                ) : (
                  <span className="chip ghost tiny">无图位</span>
                )}
              </span>
              <span className="dim tiny">
                {s.promptSource === "prompt.md" ? `prompt.md·${s.promptSha8}` : "storyboard.h3Prompt"}
              </span>
              <span className={s.url ? "" : "dim"}>
                {s.url ? <span className="chip green tiny">有片</span> : <span className="chip ghost tiny">待出</span>}
              </span>
              <span className="clip">{(s.cuts || []).map((c) => c.seconds).join(" / ")}</span>
              <span style={{ textAlign: "right" }}>
                <span className="chip tiny">看镜头</span>
              </span>
            </div>
          );
        })}
        {!segments.length && (
          <div style={{ padding: 16 }}>
            <Empty
              glyph="镜"
              title="本集还没有分镜段"
              sub={
                job?.status === "failed"
                  ? `分镜生成失败：${job.error}`
                  : "由 codex 跑 novel-storyboard：按段切镜、每切写 frame（关键帧）与段的 h3Prompt，并给出 manifest 图位清单。"
              }
            >
              <button className="btn gold" onClick={rerun} disabled={!gate.ok}>
                生成本集分镜
              </button>
            </Empty>
          </div>
        )}
      </div>

      <div className="tiny dim" style={{ marginTop: 12, lineHeight: 1.9 }}>
        段长上限 {cap}s（H3 只出 5+17n 帧，13s 会吸附成 13.67s，所以请求 311 帧 = 12.9583s）。
        <br />
        路线 A 以每镜引用素材为视频输入；可选分镜图只用于提前审阅构图，不阻塞出片。
        <span className="link" style={{ marginLeft: 8 }} onClick={() => void reloadFrames()}>
          刷新图位
        </span>
        <span className="link" style={{ marginLeft: 8 }} onClick={() => void reload()}>
          刷新段表
        </span>
      </div>
    </Stage>
  );
}

/* ------------------------------------------------------------------ 一段一页 */
function SegmentPage({ ep, segId }: { ep: number; segId: string }) {
  const navigate = useNavigate();
  const { project, status, toast, openLayer, closeLayer } = useHibtv();
  const { data: docData, doc } = usePatch("storyboard", ep);
  const { map, reload } = useSegmentMap([ep]);
  const job = useJob("storyboard", ep);
  const gate = useCodexGate();
  const list = map[ep];
  const segments = list?.segments || [];
  const idx = segments.findIndex((s) => s.id === segId);
  const [seg, setSeg] = useState<Segment | null>(null);
  const [busy, setBusy] = useState(false);
  const [referenceAssets, setReferenceAssets] = useState<ReferenceAsset[]>([]);
  // 这段正在出片（后端 seg.status=running）时锁住所有提交口：重复点会重复扣积分。
  const generating = seg?.status === "running";
  const cap = status?.segMaxSeconds || seg?.capSeconds || 13;

  const load = useCallback(async () => {
    if (!project) return;
    try {
      setSeg(await hibtvApi.getSegment(project.id, segId));
    } catch (err) {
      toast(errText(err));
    }
  }, [project, segId, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadReferenceAssets = useCallback(async () => {
    if (!project) return;
    try {
      setReferenceAssets(await hibtvApi.referenceAssets(project.id));
    } catch (err) {
      toast(errText(err));
    }
  }, [project, toast]);

  useEffect(() => {
    void loadReferenceAssets();
  }, [loadReferenceAssets]);

  // 出片中每 5s 刷一次段状态，跑完自动解锁按钮
  useEffect(() => {
    if (seg?.status !== "running") return undefined;
    const t = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(t);
  }, [seg?.status, load]);

  const cuts = seg?.cuts || [];
  const referenceTotal = useMemo(
    () => (seg?.frames || []).reduce((total, frame) => total + (frame.references?.length || 0), 0),
    [seg],
  );
  const starts = useMemo(() => {
    const out: number[] = [];
    let t = 0;
    for (const c of cuts) {
      out.push(Math.round(t * 10) / 10);
      t += Number(c.seconds) || 0;
    }
    return out;
  }, [cuts]);

  /** storyboard 文档里本集的下标（skill 会一次给多集，不能写死 0）。 */
  const epIdx = useMemo(() => {
    const board = (docData || {}) as StoryboardData;
    const i = (board.episodes || []).findIndex((e) => Number(e.ep) === ep);
    return i >= 0 ? i : 0;
  }, [docData, ep]);

  const patchSeg = async (path: string, value: unknown, label: string) => {
    if (!project || !seg) return;
    try {
      await hibtvApi.patchDoc(project.id, "storyboard", {
        path: `episodes.${epIdx}.segments.${seg.idx}.${path}`,
        value,
        ep,
      });
      toast(`已改 ${label}（门未复跑，记得重跑本集分镜）`);
      await load();
      void reload();
    } catch (err) {
      toast(errText(err));
    }
  };

  const generate = async () => {
    if (!project || !seg) return;
    if (seg.timingMismatch) {
      toast("分镜时长未对齐模型帧预算，请重跑本集分镜");
      return;
    }
    if (seg.status === "running") {
      toast("这段正在出片，等它跑完再点，避免重复扣积分");
      return;
    }
    if (seg.oversize) {
      toast(`本段 ${seg.planSeconds.toFixed(1)}s 超过 ${cap}s：平台不拆段，请带提示重跑本集 storyboard`);
      return;
    }
    setBusy(true);
    try {
      const res = await hibtvApi.generateSegment(project.id, segId, {});
      const route = res.refSource === "assets"
        ? `路线 A · ${res.refs} 张镜头素材`
        : `ref2v · ${res.refs} 张参考图`;
      toast(
        `已排队出片：${segId} v${res.v} · ${res.seconds}s（${res.frames} 帧）· ${res.cost} 积分 · ${route}`,
      );
      await load();
      void reload();
    } catch (err) {
      toast(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const editReferences = (frame: HibtvFrame) => {
    if (!project) return;
    openLayer(
      <ReferenceEditor
        frame={frame}
        catalog={referenceAssets}
        onClose={closeLayer}
        onSave={async (items) => {
          try {
            await hibtvApi
            .patchFrameReferences(project.id, frame.id, items)
            .then(async () => {
              toast(items.length ? "已更新这一镜的引用素材" : "已恢复自动挂载");
              closeLayer();
              await load();
              await loadReferenceAssets();
            });
          } catch (err) {
            toast(errText(err));
            throw err;
          }
        }}
      />,
    );
  };

  /** 生成可选分镜图；路线 A 不要求它们齐全。 */
  const fillAllFrames = async () => {
    if (!project || !seg) return;
    try {
      await hibtvApi.createJob(project.id, {
        kind: "images",
        payload: { mode: "frames", ep: seg.ep, seg_id: seg.id },
      });
      const miss = (seg.missingFrames || []).length;
      toast(miss ? `已排入可选分镜图队列：${miss} 张` : "将重出本段全部可选分镜图");
      window.setTimeout(() => void load(), 800);
    } catch (err) {
      toast(errText(err));
    }
  };

  const choose = async (v: number) => {
    if (!project) return;
    try {
      setSeg(await hibtvApi.chooseVersion(project.id, segId, v));
      toast(`已切到 v${v}（整集要重新合成才生效）`);
      void reload();
    } catch (err) {
      toast(errText(err));
    }
  };

  /** 镜头重修 = 重跑本集 storyboard（平台不改写 prompt.md 的切点契约）。 */
  const rebuild = async () => {
    if (!project) return;
    if (!gate.ok) {
      toast(gate.why);
      return;
    }
    if (!window.confirm("重跑本集分镜会用 codex 产物覆盖本集 storyboard 文档（含就地编辑）。已在变更页留底，可撤销。继续？")) {
      return;
    }
    setBusy(true);
    try {
      await hibtvApi.rebuildSegment(project.id, segId);
      toast("已排队：重跑本集分镜");
      void reload();
    } catch (err) {
      toast(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const side = (
    <>
      <div className="side-head">分镜 · STORYBOARD</div>
      <div className="subnav">
        <div className="nav-item" onClick={() => navigate("/hibtv/storyboard")}>
          总表
        </div>
        <div className="nav-item" onClick={() => navigate(`/hibtv/storyboard/${ep}`)}>
          本集总表
          <span className="n">{segments.length}</span>
        </div>
      </div>
      <div className="side-head" style={{ paddingTop: 2 }}>
        第 {ep} 集
      </div>
      <div className="ep-list">
        {segments.map((s) => (
          <div
            key={s.id}
            className={`mini-row ${s.id === segId ? "on" : ""}`}
            onClick={() => navigate(`/hibtv/storyboard/${ep}/${s.id}`)}
          >
            <span className={s.url ? "ok" : s.oversize ? "dot bad" : "dot"}>●</span>
            {s.id}
            <span style={{ marginLeft: "auto", color: "#6d665c" }}>
              {s.planSeconds.toFixed(1)}s
            </span>
          </div>
        ))}
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

  const missing = seg?.missingFrames || [];
  const panel = seg && (
    <>
      <div className="panel-sec">
        <div className="panel-title">
          监看 <span className="r">Minimax-h3 · {project.aspectRatio}</span>
        </div>
        <VideoBox
          src={seg.url}
          label={`${seg.id} 成片`}
          view={`段级生片 · v${seg.currentVersion || 0}`}
          ratio={project.aspectRatio === "16:9" ? "16 / 9" : "9 / 16"}
          maxHeight={280}
        />
        <div className="playbar" style={{ marginTop: 8 }}>
          <span className="p">▶</span>
          <span className="time">0:00 / {fmtTime(seg.planSeconds)}</span>
          <span className="bar">
            <i style={{ width: "0%" }} />
          </span>
        </div>
        <div className="inline" style={{ marginTop: 8, flexWrap: "wrap", gap: 4 }}>
          <span className="chip">计划 {seg.planSeconds.toFixed(1)}s</span>
          <span className={`chip ${seg.oversize ? "red" : "gold"}`}>上限 {cap}s</span>
          {seg.request && (
            <span className="chip" title="H3 只出 5+17n 帧（24fps），请求值按帧对齐">
              请求 {seg.request.frames} 帧 / {seg.request.seconds}s
            </span>
          )}
        </div>
      </div>

      <div className="panel-sec">
        <div className="inline">
          <span className="chip">镜 {cuts.length} · 素材 {referenceTotal}</span>
          <span style={{ marginLeft: "auto" }} />
          <button
            className="btn sm gold"
            disabled={busy || generating || seg.oversize || seg.timingMismatch}
            title={
              seg.oversize
                ? "段长超限：先重跑本集 storyboard"
                : "路线 A：按镜头汇总场景、定场图、角色和道具素材"
            }
            onClick={() => void generate()}
          >
            重新生成视频
          </button>
        </div>
        <div className="inline" style={{ marginTop: 8, flexWrap: "wrap" }}>
          {seg.versions.length ? (
            seg.versions.map((v) => (
              <span
                key={v.v}
                className={`chip i ${v.v === seg.currentVersion ? "gold" : ""}`}
                onClick={() => void choose(v.v)}
                title={v.path ? "切到这一版" : "这一版还没出片"}
              >
                v{v.v}
                {v.v === seg.currentVersion ? " 当前" : ""}
                {v.failed ? " · 失败" : ""}
              </span>
            ))
          ) : (
            <span className="tiny dim">还没有版本</span>
          )}
        </div>
      </div>

      <div className="panel-sec">
        <div className="panel-title">
          出片入参 <span className="r">路线 A · 镜头素材引用</span>
        </div>
        <div className="tiny dim" style={{ lineHeight: 1.9 }}>
          程序按镜头顺序汇总引用素材并去重，一段一次视频生成；衔接上一段时会加入已验收尾帧，场景正面图与定场图冲突时优先保留定场图。
          {` 本段共 ${referenceTotal} 条镜头素材。`}
          {seg.promptSha8 ? ` 当前提示词 sha256 前 8 位：${seg.promptSha8}` : ""}
        </div>
        {missing.length ? (
          <div className="tiny" style={{ marginTop: 6, color: "var(--gold-2)" }}>
            可选分镜图 f{missing.join(" / f")} 尚未生成；不影响路线 A 出片。
          </div>
        ) : null}
      </div>

      <div className="panel-sec">
        <div className="panel-title">生成设置</div>
        <div className="tiny dim">
          Minimax-h3 · {project.aspectRatio} · 768p；
          {seg.request ? `请求 ${seg.request.frames} 帧 = ${seg.request.seconds}s` : `上限 ${cap}s`}；
          对白与音效随片出，不叠云端增强。
        </div>
      </div>
    </>
  );

  return (
    <Stage side={side} panel={panel}>
      <div className="eyebrow">下游 · 怎么拍 · STORYBOARD</div>
      <div className="inline" style={{ marginBottom: 10, flexWrap: "wrap", gap: 6 }}>
        <span className="h1" style={{ margin: 0 }}>
          第 {ep} 集 / <span className="mono" style={{ color: "var(--gold)" }}>{segId}</span>
        </span>
        <button
          className="btn xs"
          disabled={idx <= 0}
          onClick={() => navigate(`/hibtv/storyboard/${ep}/${segments[idx - 1].id}`)}
        >
          ‹
        </button>
        <span className="tiny dim">
          {idx + 1} / {segments.length}
        </span>
        <button
          className="btn xs"
          disabled={idx < 0 || idx >= segments.length - 1}
          onClick={() => navigate(`/hibtv/storyboard/${ep}/${segments[idx + 1].id}`)}
        >
          ›
        </button>
        <span className="chip">场次 {seg?.data?.sceneIndex || 1}</span>
        <span className={`chip ${seg?.oversize ? "red" : "gold"}`}>
          {(seg?.planSeconds || 0).toFixed(1)}s / {cap}s
        </span>
        <span className="chip">{cuts.length} 镜</span>
        <span className="chip green">路线 A · 素材 {referenceTotal}</span>
        {seg && seg.versions.length > 1 && <span className="chip gold">已出 {seg.versions.length} 版</span>}
        <span className="sp" style={{ marginLeft: "auto" }} />
        <button
          className="btn xs ghost"
          onClick={() =>
            openLayer(<ArtifactsDrawer projectId={project.id} jobId={job?.id || 0} onClose={closeLayer} />)
          }
        >
          产物
        </button>
        <button className="btn xs" disabled={!gate.ok} title={gate.why} onClick={() => void rebuild()}>
          ↻ 重跑本集分镜
        </button>
        <button className="btn xs" onClick={() => navigate(`/hibtv/storyboard/${ep}`)}>
          回本集总表
        </button>
      </div>

      <GenBar job={job} onCancel={job ? () => void hibtvApi.cancelRemoteJob(project.id, job.id) : undefined} />

      {seg?.oversize ? (
        <div className="gates bad" style={{ marginBottom: 10 }}>
          <div>
            本段计划 {seg.planSeconds.toFixed(1)}s，超过单次生成上限 {cap}s（H3 只出 5+17n 帧）。
            平台不拆段、不改提示词 —— 请带提示重跑本集 storyboard（本段 {seg.id} 需重新切镜）。
          </div>
        </div>
      ) : null}

      {!seg ? (
        <Loading text="读取这一段…" />
      ) : (
        <>
          <div className="card" style={{ marginBottom: 12 }}>
            <div className="card-hd">
              <span className="t">可选分镜图</span>
              <span className="chip">构图控制</span>
              <span style={{ marginLeft: "auto" }} />
              <span className="tiny dim">缺失或过期不阻塞路线 A 出片</span>
            </div>
            <div className="card-bd">
              <div className="inline" style={{ marginBottom: 8, flexWrap: "wrap" }}>
                <span className="lab tiny">画幅</span>
                <span className="chip gold">{project.aspectRatio}</span>
                <span className="chip tiny">
                  可选图 {(seg.frames || []).filter((f) => f.url).length}/{seg.pictureCount || cuts.length}
                </span>
                <span style={{ marginLeft: "auto" }} />
                <button className="btn xs" disabled={busy || generating} onClick={() => void fillAllFrames()}>
                  生成全部可选图
                </button>
              </div>
              <div className="tiny dim" style={{ lineHeight: 1.9 }}>
                需要提前看构图时，才为镜头生成一张独立、干净的单画面。它使用独立 HiBTV 生图工作流，
                参考图按 <span className="mono">&lt;image1&gt;</span> 声明用途；拼贴、多视图和重复人物直接判为失败。
                这些 f1..fN 不默认进入视频，也不要求与分段切点一一绑定。
              </div>
            </div>
          </div>

          <div className="card" style={{ background: "rgba(255,255,255,.014)" }}>
            <div className="card-bd">
              <div className="tiny dim" style={{ marginBottom: 6 }}>
                开场走位 · 本段各镜不变
              </div>
              <div className="small mut" style={{ lineHeight: 1.9 }}>
                {seg.data?.blocking || "（这一段没有走位描述）"}
              </div>
              <div className="tiny dim" style={{ margin: "10px 0 6px" }}>
                整体音景
              </div>
              <div className="small mut" style={{ lineHeight: 1.9 }}>
                {seg.data?.soundscape || "（未写音景）"}
              </div>
              <div className="tiny dim" style={{ margin: "10px 0 6px" }}>
                音乐
              </div>
              <div className="small mut" style={{ lineHeight: 1.9 }}>
                {seg.data?.music || "（未写音乐）"}
              </div>
            </div>
          </div>

          <div className="inline" style={{ margin: "16px 0 8px" }}>
            <span className="h2" style={{ margin: 0 }}>
              可选分镜图
            </span>
            <span className="tiny dim">只在需要提前定构图时生成；与视频素材引用不是同一层</span>
            <span className="sp" style={{ marginLeft: "auto" }} />
            <button className="btn xs" onClick={() => void load()}>
              刷新镜头
            </button>
          </div>
          <div className="card">
            <div className="card-bd">
              <FrameStrip projectId={project.id} seg={seg} onChanged={() => void load()} ratio={project.aspectRatio === "16:9" ? "16 / 9" : "9 / 16"} />
              <div className="tiny dim" style={{ marginTop: 8 }}>
                Qwen 出图提示词 = 本镜 <span className="mono">frame</span> + 六项构图 + 当前人物状态 + 每张参考图的 <span className="mono">&lt;imageN&gt;</span> 用途；
                尺寸按 {project.aspectRatio} 的原生画布，不缩放变形。
              </div>
            </div>
          </div>

          <div className="inline" style={{ margin: "16px 0 8px" }}>
            <span className="h2" style={{ margin: 0 }}>
              镜头
            </span>
            <span className="chip">
              镜 {cuts.length} · 可选图 {(seg.frames || []).filter((f) => f.ready).length}
            </span>
            <span className="tiny dim">每个镜头可单独选择真正发给视频模型的素材</span>
            <span className="sp" style={{ marginLeft: "auto" }} />
            <button className="btn xs" disabled={busy || generating} onClick={() => void fillAllFrames()}>
              生成全部可选图
            </button>
            <button
              className="btn xs"
              disabled={busy || generating || seg.oversize || seg.timingMismatch}
              title="路线 A：按镜头素材直接生成段视频"
              onClick={() => void generate()}
            >
              用当前提示词出片
            </button>
          </div>

          <div className="card" style={{ overflowX: "auto" }}>
            <table className="tbl" style={{ minWidth: 1240 }}>
              <thead>
                <tr>
                  <th>#</th>
                  <th>引用素材</th>
                  <th>可选分镜图</th>
                  <th>区间</th>
                  <th>秒</th>
                  <th>节拍</th>
                  <th>景别</th>
                  <th>运镜</th>
                  <th>机位</th>
                  <th>镜头</th>
                  <th>焦段</th>
                  <th>构图</th>
                  <th>视线</th>
                  <th>焦点</th>
                  <th>稳定</th>
                  <th>出场</th>
                  <th>道具</th>
                  <th>关键帧提示词</th>
                </tr>
              </thead>
              <tbody>
                {cuts.map((cut, i) => {
                  const from = starts[i] ?? 0;
                  const to = Math.round((from + Number(cut.seconds || 0)) * 10) / 10;
                  const fr = (seg.frames || []).find((f) => f.cutIndex === i + 1);
                  return (
                    <tr key={i}>
                      <td className="mono">{i + 1}</td>
                      <td style={{ minWidth: 250 }}>
                        {fr ? (
                          <ShotReferences frame={fr} onEdit={() => editReferences(fr)} />
                        ) : (
                          <span className="tiny dim">等待镜头记录</span>
                        )}
                      </td>
                      <td>
                        {fr ? (
                          <a
                            href={fr.url ? mediaUrl(fr.url) : undefined}
                            target="_blank"
                            rel="noreferrer"
                            title={fr.url ? `f${fr.cutIndex} 关键帧（点开看大图）` : `f${fr.cutIndex} 缺图`}
                            style={{
                              display: "block",
                              width: 52,
                              aspectRatio: project.aspectRatio === "16:9" ? "16 / 9" : "9 / 16",
                              border: `1px solid ${fr.url ? "var(--line-3)" : "rgba(224,120,90,.5)"}`,
                              borderRadius: 4,
                              overflow: "hidden",
                              background: "#0f0e0c",
                              position: "relative",
                            }}
                          >
                            {fr.url ? (
                              <img
                                src={mediaUrl(fr.url)}
                                alt={`f${fr.cutIndex}`}
                                loading="lazy"
                                style={{ width: "100%", height: "100%", objectFit: "cover" }}
                              />
                            ) : (
                              <span className="tiny dim" style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}>缺</span>
                            )}
                          </a>
                        ) : (
                          <span className="tiny dim">—</span>
                        )}
                      </td>
                      <td className="mono">
                        {from}–{to}s
                      </td>
                      <td className="mono">
                        <InlineEdit
                          value={Number(cut.seconds).toFixed(1)}
                          onSave={(v) => {
                            const n = Number(v.replace(/[^\d.]/g, ""));
                            if (!n || n <= 0) {
                              toast("切秒数需是正数");
                              return;
                            }
                            void patchSeg(`cuts.${i}.seconds`, Math.round(n * 10) / 10, `镜 ${i + 1} 秒数`);
                          }}
                        />
                      </td>
                      <td className="tiny dim">{(cut.beats || []).join("/") || "—"}</td>
                      <td className="tiny">{cut.size || "—"}</td>
                      <td className="tiny">{cut.camera || "—"}</td>
                      <td className="tiny">{cut.cameraPosition || "—"}</td>
                      <td className="tiny" style={{ maxWidth: 200 }}>
                        <InlineEdit
                          multiline
                          value={cut.shot || ""}
                          placeholder="（双击写这一镜）"
                          onSave={(v) => void patchSeg(`cuts.${i}.shot`, v, `镜 ${i + 1} 描述`)}
                        />
                      </td>
                      <td className="tiny">{cut.lens || "—"}</td>
                      <td className="tiny">{cut.composition || "—"}</td>
                      <td className="tiny">{cut.eyeline || "—"}</td>
                      <td className="tiny">{cut.focus || "—"}</td>
                      <td className="tiny">{cut.stability || "—"}</td>
                      <td className="tiny">
                        {(cut.characters || []).map((c) => (
                          <span className="chip tiny" key={c} style={{ marginRight: 3 }}>
                            {c}
                          </span>
                        ))}
                        {!(cut.characters || []).length && "—"}
                      </td>
                      <td className="tiny">
                        {(cut.props || []).map((p) => (
                          <span className="chip tiny" key={p} style={{ marginRight: 3 }}>
                            {p}
                          </span>
                        ))}
                        {!(cut.props || []).length && "—"}
                      </td>
                      <td className="tiny" style={{ maxWidth: 260 }}>
                        <InlineEdit
                          multiline
                          value={cut.frame || ""}
                          placeholder="（双击写这一切的关键帧提示词）"
                          onSave={(v) => void patchSeg(`cuts.${i}.frame`, v, `镜 ${i + 1} 帧提示词`)}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <div className="card-hd">
              <span className="t">分镜源提示词</span>
              <span className="chip">路线 A 编译基础</span>
              {seg.promptSha8 && <span className="chip ghost">sha256 {seg.promptSha8}</span>}
              <span className="chip">
                图 {seg.pictureCount || 0} / 切 {cuts.length}
              </span>
              <span style={{ marginLeft: "auto" }} />
              <button
                className="btn xs"
                disabled={busy || generating || seg.oversize || seg.timingMismatch}
                title="路线 A：镜头动作与镜头素材一起送入视频模型"
                onClick={() => void generate()}
              >
                重新生成视频
              </button>
            </div>
            <div className="card-bd">
              <InlineEdit
                multiline
                className="promptbox"
                style={{
                  display: "block",
                  maxHeight: 340,
                  overflow: "auto",
                  whiteSpace: "pre-wrap",
                  margin: 0,
                  fontFamily: "var(--mono)",
                  fontSize: 11.5,
                }}
                value={seg.h3Prompt || ""}
                placeholder="（还没有提示词；双击可改，或带提示重跑本集分镜）"
                onSave={(v) => void patchSeg("h3Prompt", v, "出片提示词")}
              />
              <div className="tiny dim" style={{ marginTop: 8, lineHeight: 1.9 }}>
                这里保留 skill 校验过的镜头动作、台词和声音。生成视频时，程序按各镜素材重新编译为路线 A 的
                Ref2VA 提示词，不再把这段文本里的关键帧时间点当成唯一图片映射。
              </div>
              <div style={{ marginTop: 10 }}>
                <InlineEdit
                  multiline
                  value={seg.data?.note || ""}
                  placeholder="（这一段的说明；双击可写）"
                  onSave={(v) => void patchSeg("note", v, "段说明")}
                />
              </div>
            </div>
          </div>

          <div className="tiny dim" style={{ marginTop: 12, lineHeight: 1.9 }}>
            门结果来自网关产物（`.gates.jsonl` / `storyboard-report.html`）：
            <GateChips gates={doc?.gates} none="这一集没有门结果" />
            <GateList gates={doc?.gates} />
          </div>
        </>
      )}
    </Stage>
  );
}
