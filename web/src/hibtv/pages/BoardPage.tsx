/** 分镜：总表 → 本集段表 → 一段一页（cut 表 + 图位条 f1..fN + prompt.md 原文）。
 *
 * 口径（见 plan §4.1/§4.2）：
 * - 段的出片提示词就是 `prompt.md` 原文（`Picture N` = 切点契约），平台不改写、不裁剪；
 * - `manifest.pictures` 长度 == 切数 == prompt.md 的 Picture 条数，三者不等即拦出片；
 * - 段长 ≤ {cap}s（H3 只出 5+17n 帧：311 帧 = 12.9583s），超限只能重跑本集 storyboard；
 * - 每个切一张关键帧（`cuts[].frame`），出片时按序号挂 f1..fN 当参考图。
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
  FrameList,
  HibtvFrame,
  OutlineData,
  RenderItem,
  Segment,
  SegmentList,
  StoryboardData,
} from "../types";

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

/** 本集关键帧（每切一张）：图位条与出片前置校验都读它。 */
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
  return { done: rows.filter((f) => f.url).length, total: rows.length, rows };
}

/* ------------------------------------------------------------------ 图位条 */
/** 图位条：f1..fN 一格一切，缺图时段出片按钮置灰并说明缺第几张。 */
function FrameStrip({
  projectId,
  seg,
  onChanged,
}: {
  projectId: number;
  seg: Segment;
  onChanged: () => void;
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
      toast(`已排队：${seg.id} f${frame.cutIndex} 关键帧`);
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
      toast(`已排入关键帧队列：${frames.length} 张（缺 ${missing.length} 张）`);
      window.setTimeout(onChanged, 800);
    } catch (err) {
      toast(errText(err));
    }
  };

  const upload = async (frame: HibtvFrame, file: File) => {
    setBusy(frame.id);
    try {
      await hibtvApi.uploadFrame(projectId, frame.id, file);
      toast(`已上传 f${frame.cutIndex}，顶掉这一张`);
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
        这一段还没有图位（manifest.pictures 为空）——先跑本集分镜。
      </div>
    );
  }

  return (
    <>
      <div className="inline" style={{ marginBottom: 8, flexWrap: "wrap" }}>
        <span className={`chip ${missing.length ? "red" : "green"}`}>
          图位 {frames.length - missing.length}/{frames.length}
          {missing.length ? ` · 缺 f${missing.join(" / f")}` : " · 齐"}
        </span>
        <span className="chip ghost">f1..fN 与 prompt.md 的 Picture 序号一致</span>
        <span style={{ marginLeft: "auto" }} />
        <button className="btn xs" onClick={() => void drawAll()}>
          {missing.length ? `补齐缺的 ${missing.length} 张` : "重出本段全部帧"}
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
          const isMissing = !f.url;
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
                style={{ display: "block", position: "relative", aspectRatio: "9 / 16", background: "#0f0e0c" }}
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
                    缺图
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
                  title="用这一镜的 frame 提示词出 / 重出这一张"
                >
                  {isMissing ? "出图" : "重出"}
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
        <Empty glyph="剧" title="还没有项目" sub="先在首页粘贴或上传小说正文。">
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
        lede="段 → 切 → 图三层：每段一个 prompt.md（Picture N 钉切点），每切一张关键帧；图数与切数不等就拦出片。"
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
      lede="整集成片按段拼；每段 ≤ 13 秒、每切 2–5 秒一个镜头。出片只有一条路：ref2v + 每切一张关键帧。"
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
        图位数字是「已出关键帧 / 需出张数」；缺图时段出片会被后端拦下。
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

  // 出片中每 5s 刷一次段状态，跑完自动解锁按钮
  useEffect(() => {
    if (seg?.status !== "running") return undefined;
    const t = window.setInterval(() => void load(), 5000);
    return () => window.clearInterval(t);
  }, [seg?.status, load]);

  const cuts = seg?.cuts || [];
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
    if (seg.status === "running") {
      toast("这段正在出片，等它跑完再点，避免重复扣积分");
      return;
    }
    if (seg.oversize) {
      toast(`本段 ${seg.planSeconds.toFixed(1)}s 超过 ${cap}s：平台不拆段，请带提示重跑本集 storyboard`);
      return;
    }
    if (seg.framesReady === false) {
      toast(`缺关键帧 f${(seg.missingFrames || []).join(" / f") || "?"}：ref2v 少一张图就会切到空白`);
      return;
    }
    setBusy(true);
    try {
      const res = await hibtvApi.generateSegment(project.id, segId, {});
      toast(
        `已排队出片：${segId} v${res.v} · ${res.seconds}s（${res.frames} 帧）· ${res.cost} 积分 · ${res.refs} 张关键帧`,
      );
      await load();
      void reload();
    } catch (err) {
      toast(errText(err));
    } finally {
      setBusy(false);
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
        <Empty glyph="剧" title="还没有项目" sub="先在首页粘贴或上传小说正文。">
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
          <span className="chip">切 {cuts.length} · 图 {seg.pictureCount || 0}</span>
          <span style={{ marginLeft: "auto" }} />
          <button
            className="btn sm gold"
            disabled={busy || generating || seg.oversize || seg.framesReady === false}
            title={
              seg.oversize
                ? "段长超限：先重跑本集 storyboard"
                : seg.framesReady === false
                  ? `缺关键帧 f${missing.join(" / f")}`
                  : "ref2v + 关键帧原样直出"
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
          出片入参 <span className="r">ref2v · {cuts.length} 张参考图</span>
        </div>
        <div className="tiny dim" style={{ lineHeight: 1.9 }}>
          提示词 = prompt.md 原文（Picture N 钉切点），平台不裁剪、不改写；
          参考图 = f1..fN 按序号挂，`ref_image_size=match` 负责归一。
          {seg.promptSha8 ? ` 当前提示词 sha256 前 8 位：${seg.promptSha8}` : ""}
        </div>
        {missing.length ? (
          <div className="tiny red" style={{ marginTop: 6 }}>
            缺关键帧 f{missing.join(" / f")}：ref2v 少一张图就会「切到空白」，补齐后才能出片。
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
        <span className={`chip ${seg && seg.framesReady === false ? "red" : ""}`}>
          图位 {seg ? (seg.frames || []).filter((f) => f.url).length : 0}/{seg?.pictureCount || cuts.length}
        </span>
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
              图位条
            </span>
            <span className="tiny dim">每切一张关键帧，序号 = prompt.md 的 Picture N</span>
            <span className="sp" style={{ marginLeft: "auto" }} />
            <button className="btn xs" onClick={() => void load()}>
              刷新图位
            </button>
          </div>
          <div className="card">
            <div className="card-bd">
              <FrameStrip projectId={project.id} seg={seg} onChanged={() => void load()} />
              <div className="tiny dim" style={{ marginTop: 8 }}>
                出图提示词 = 本切 <span className="mono">frame</span> + 该场 lighting + 竖屏构图；
                尺寸按 {project.aspectRatio} 的原生画布（9:16 → 768×1344），不缩放变形。
              </div>
            </div>
          </div>

          <div className="inline" style={{ margin: "16px 0 8px" }}>
            <span className="h2" style={{ margin: 0 }}>
              镜头
            </span>
            <span className="chip">
              切 {cuts.length} · 图 {seg.pictureCount || 0}
            </span>
            <span className="sp" style={{ marginLeft: "auto" }} />
            <button
              className="btn xs"
              disabled={busy || generating || seg.oversize || seg.framesReady === false}
              onClick={() => void generate()}
            >
              用当前提示词出片
            </button>
          </div>

          <div className="card" style={{ overflowX: "auto" }}>
            <table className="tbl" style={{ minWidth: 1180 }}>
              <thead>
                <tr>
                  <th>#</th>
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
                  return (
                    <tr key={i}>
                      <td className="mono">{i + 1}</td>
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
              <span className="t">出片提示词（prompt.md 原文 · 逐字发给视频模型）</span>
              <span className="chip">{seg.promptSource === "prompt.md" ? "来自 prompt.md" : "来自 storyboard.h3Prompt"}</span>
              {seg.promptSha8 && <span className="chip ghost">sha256 {seg.promptSha8}</span>}
              <span className="chip">
                图 {seg.pictureCount || 0} / 切 {cuts.length}
              </span>
              <span style={{ marginLeft: "auto" }} />
              <button
                className="btn xs"
                disabled={busy || generating || seg.oversize || seg.framesReady === false}
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
                这段提示词是 ref2v 的唯一入参：`Picture N` 就是切点契约，改它等于改「切点↔图」的对应关系。
                真要改镜头，请点右上「重跑本集分镜」，让 codex 重写整段（含 prompt.md 与 manifest）。
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
