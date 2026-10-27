/** 分镜：总表 → 本集段表 → 一段一页（镜卡 + 右栏监看 / 版本 / 引用素材）。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { hibtvApi } from "../api";
import { Stage } from "../HibtvLayout";
import {
  Empty,
  GateChips,
  GateList,
  GenBar,
  InlineEdit,
  Loading,
  PageHead,
  Plate,
  VideoBox,
  fmtTime,
} from "../components/ui";
import { errText, useDoc, useHibtv, useJob, usePatch } from "../state";
import type { OutlineData, RenderItem, Segment, SegmentList } from "../types";

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

export default function BoardPage() {
  const { ep: epParam, seg: segParam } = useParams();
  const ep = epParam ? Math.max(1, Number(epParam) || 1) : 0;

  if (segParam) {
    return <SegmentPage ep={ep} segId={segParam} />;
  }
  if (!ep) {
    return <BoardListPage />;
  }
  return <BoardEpisodePage ep={ep} />;
}

/* ------------------------------------------------------------------ 总表 */
function BoardListPage() {
  const navigate = useNavigate();
  const { project, status, runJob, toast } = useHibtv();
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
    for (const row of Object.values(map)) {
      segs += row.segments.length;
      cuts += row.segments.reduce((n, s) => n + (s.cuts || []).length, 0);
      secs += row.segments.reduce((n, s) => n + (s.planSeconds || 0), 0);
    }
    return { segs, cuts, secs };
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
        lede="来自剧本：段号、切点、节拍区间都不单独存——它们由镜头秒数和拍子归属推导，所以改一个镜头长度，整段的时间轴自己就对齐了。"
        right={
          <>
            <span className="chip gold">段 ≤ {status?.segMaxSeconds || 10}s</span>
            <span className="chip">镜建议 2–5s</span>
            <span className="chip">同框 ≤ 3</span>
          </>
        }
      />

      <div className="toolbar">
        <span className="chip">整部剧</span>
        <span className="lab">出图模型</span>
        <span className="chip">{status?.image?.worker || "qwen21"}</span>
        <span className="chip">画幅 {project.aspectRatio}</span>
        <span className="lab">视频模型</span>
        <span className="chip">Minimax-h3</span>
        <span className="sp" />
        <span className="tiny dim">
          已分镜 {withBoard.length} / {eps.length} 集
        </span>
      </div>

      <div className="card" style={{ overflow: "hidden" }}>
        <table className="tbl">
          <thead>
            <tr>
              <th>集</th>
              <th>状态</th>
              <th>段</th>
              <th>镜</th>
              <th>总时长</th>
              <th>均镜长</th>
              <th>建议时长</th>
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
              const avg = cuts ? secs / cuts : 0;
              return (
                <tr
                  key={n}
                  className={n === 1 ? "sel" : ""}
                  style={{ cursor: "pointer" }}
                  onClick={() => navigate(`/hibtv/storyboard/${n}`)}
                >
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
                  <td className="mono">{done && avg ? `${avg.toFixed(1)}s` : "—"}</td>
                  <td>
                    {done ? (
                      avg >= 1.9 && avg <= 5.1 ? (
                        <span className="chip green">镜长 2–5s</span>
                      ) : (
                        <span className="chip red">镜长越界 · 均 {avg.toFixed(1)}s</span>
                      )
                    ) : (
                      <span className="dim">—</span>
                    )}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {done ? (
                      <span className="chip tiny">看本集</span>
                    ) : (
                      <button
                        className="btn xs"
                        onClick={(e) => {
                          e.stopPropagation();
                          void runJob("storyboard", n);
                          toast(`已排队：第 ${n} 集分镜`);
                        }}
                      >
                        生成本集
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="tiny dim" style={{ marginTop: 12, lineHeight: 1.9 }}>
        段长上限 {status?.segMaxSeconds || 10}s、镜建议 2–5s；分镜会先补跑本集剧本，再按剧本的拍切镜。
        <br />
        点任意一行进入本集段表；一段一页可以改提示词、选版本、只重跑那一段。
      </div>
    </Stage>
  );
}

/* ------------------------------------------------------------------ 本集段表 */
function BoardEpisodePage({ ep }: { ep: number }) {
  const navigate = useNavigate();
  const { project, status, runJob, toast } = useHibtv();
  const { data, doc } = usePatch("storyboard", ep);
  const { data: outlineData } = useDoc("outline");
  const job = useJob("storyboard", ep);
  const { map, reload } = useSegmentMap([ep]);
  const { renders } = useRenders(ep);
  const list = map[ep];
  const segments = list?.segments || [];
  const outline = (outlineData || {}) as OutlineData;
  const latest = renders[0];

  const cuts = segments.reduce((n, s) => n + (s.cuts || []).length, 0);
  const secs = segments.reduce((n, s) => n + (s.planSeconds || 0), 0);
  const withVideo = segments.filter((s) => s.url).length;

  const sceneName = (idx?: number) =>
    (idx && outline.scenes?.[idx - 1]?.name) || `场景 ${idx || 1}`;

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
        {segments.map((s) => (
          <div
            key={s.id}
            className="mini-row"
            onClick={() => navigate(`/hibtv/storyboard/${ep}/${s.id}`)}
          >
            <span className={s.url ? "ok" : "dot"}>●</span>
            {s.id}
            <span style={{ marginLeft: "auto", color: "#6d665c" }}>
              {s.planSeconds.toFixed(1)}s · {(s.cuts || []).length}镜
            </span>
          </div>
        ))}
        {!segments.length && <div className="tiny dim" style={{ padding: 8 }}>还没有分镜段</div>}
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
      lede="整集成片按段拼；每段 ≤ 段长上限，每镜 2–5 秒。没有分镜图也能出片——直接拿角色与场景锚点图出视频。"
      right={
        <>
          <GateChips gates={doc?.gates} />
          <button className="btn ghost" onClick={() => navigate(`/hibtv/script/${ep}`)}>
            看剧本
          </button>
          <button className="btn" onClick={() => navigate(`/hibtv/video/${ep}`)}>
            出视频
          </button>
          <button className="btn gold" onClick={() => void runJob("storyboard", ep)}>
            {segments.length ? "重新生成本集" : "生成本集分镜"}
          </button>
        </>
      }
    />
  );

  const genAll = async () => {
    if (!project) return;
    const pending = segments.filter((s) => !s.url);
    if (!pending.length) {
      toast("本集每段都已有成片");
      return;
    }
    for (const seg of pending) {
      try {
        await hibtvApi.generateSegment(project.id, seg.id, {});
      } catch (err) {
        toast(`${seg.id}：${errText(err)}`);
        break;
      }
    }
    toast(`已排入出片队列：${pending.length} 段`);
    void reload();
  };

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
      <GenBar job={job} onCancel={job ? () => void hibtvApi.cancelJob(project.id, job.id) : undefined} />
      <GateList gates={doc?.gates} />
      {!data && !segments.length ? <Loading /> : null}

      <div className="split" style={{ margin: "14px 0" }}>
        <div className="card">
          <div className="card-hd">
            <span className="t">分镜图</span>
            <span className="chip">可选</span>
            <span style={{ marginLeft: "auto" }} />
            <span className="tiny dim">不出也能出片</span>
          </div>
          <div className="card-bd">
            <div className="inline" style={{ marginBottom: 8 }}>
              <span className="lab tiny">出图模型</span>
              <span className="chip">{status?.image?.worker || "qwen21"}</span>
              <span className="lab tiny">画幅</span>
              <span className="chip gold">{project.aspectRatio}</span>
            </div>
            <button
              className="btn"
              onClick={() => toast("当前版本直接拿场景 / 角色 / 道具锚点图出片；分镜图逐镜出图在后续版本提供")}
            >
              补齐本集分镜图
            </button>
            <div className="tiny dim" style={{ marginTop: 8 }}>
              没有分镜图的段挂角色 / 场景 / 道具的参考图出。想先定构图，或者某一镜出片总不对，再给它出分镜图。
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-hd">
            <span className="t">视频</span>
            <span className="chip">
              {withVideo} / {segments.length} 段有片
            </span>
            <span style={{ marginLeft: "auto" }} />
            <span className="tiny dim">每段一次生成</span>
          </div>
          <div className="card-bd">
            <div className="inline" style={{ marginBottom: 8 }}>
              <span className="lab tiny">视频模型</span>
              <span className="chip">Minimax-h3</span>
              <span className="lab tiny">段长</span>
              <span className="chip gold">{status?.segMaxSeconds || 10}s 上限</span>
            </div>
            <button className="btn" onClick={() => void genAll()}>
              补齐本集视频
            </button>
            <div className="tiny dim" style={{ marginTop: 8 }}>
              段级排队：哪一段不满足，就只重跑那一段，不整集重来。
            </div>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 12 }}>
        <div className="card-hd">
          <span className="t">整集成片</span>
          <span className="chip gold">
            {withVideo} / {segments.length} 段有片
          </span>
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
                className={`tl ${i === 0 ? "on" : ""}`}
                onClick={() => navigate(`/hibtv/storyboard/${ep}/${s.id}`)}
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
          <span>版本</span>
          <span>成片</span>
          <span>切点</span>
          <span>说明</span>
          <span />
        </div>
        {segments.map((s) => (
          <div
            key={s.id}
            className="segrow"
            onClick={() => navigate(`/hibtv/storyboard/${ep}/${s.id}`)}
          >
            <span className="sid">{s.id}</span>
            <span>{sceneName(s.data?.sceneIndex)}</span>
            <span className="mono">{(s.cuts || []).length}</span>
            <span className="mono">{s.planSeconds.toFixed(1)}s</span>
            <span className="dim">v{s.currentVersion || 0}</span>
            <span className={s.url ? "" : "dim"}>{s.url ? <span className="chip green tiny">有片</span> : <span className="chip ghost tiny">待出</span>}</span>
            <span className="clip">
              {(s.cuts || []).map((c) => c.seconds).join(" / ")}
            </span>
            <span className="note">
              {s.versions.length > 1 && (
                <span className="chip gold tiny" style={{ height: 15, marginRight: 5 }}>
                  v{s.versions.length}
                </span>
              )}
              {s.data?.note || ""}
            </span>
            <span style={{ textAlign: "right" }}>
              <span className="chip tiny">看镜头</span>
            </span>
          </div>
        ))}
        {!segments.length && (
          <div style={{ padding: 16 }}>
            <Empty
              glyph="镜"
              title="本集还没有分镜段"
              sub={
                job?.status === "failed"
                  ? `分镜生成失败：${job.error}`
                  : "点「生成本集分镜」：先补跑本集剧本，再按拍切镜、逐镜写 h3 视频提示词。"
              }
            >
              <button className="btn gold" onClick={() => void runJob("storyboard", ep)}>
                生成本集分镜
              </button>
            </Empty>
          </div>
        )}
      </div>
    </Stage>
  );
}

/* ------------------------------------------------------------------ 一段一页 */
function SegmentPage({ ep, segId }: { ep: number; segId: string }) {
  const navigate = useNavigate();
  const { project, status, toast } = useHibtv();
  const { map, reload } = useSegmentMap([ep]);
  const list = map[ep];
  const segments = list?.segments || [];
  const idx = segments.findIndex((s) => s.id === segId);
  const [seg, setSeg] = useState<Segment | null>(null);
  const [busy, setBusy] = useState(false);
  // 这段正在出片（后端 seg.status=running）时锁住所有提交口：重复点会重复扣积分。
  const generating = seg?.status === "running";

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

  const generate = async () => {
    if (!project) return;
    if (seg?.status === "running") {
      toast("这段正在出片，等它跑完再点，避免重复扣积分");
      return;
    }
    setBusy(true);
    try {
      const res = await hibtvApi.generateSegment(project.id, segId, {
        prompt: seg?.h3Prompt || "",
      });
      toast(
        `已排队出片：${segId} v${res.v} · ${res.seconds}s · ${res.cost} 积分 · ${res.refs} 张参考图`,
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

  /** 镜头重修第 1 步：改分镜文档里的字段（唯一事实来源），后端会同步段表。 */
  const patchSeg = async (path: string, value: unknown, label: string) => {
    if (!project || !seg) return;
    try {
      await hibtvApi.patchDoc(project.id, "storyboard", {
        path: `episodes.0.segments.${seg.idx}.${path}`,
        value,
        ep,
      });
      toast(`已改 ${label} · 记得重算提示词再出片`);
      await load();
      void reload();
    } catch (err) {
      toast(errText(err));
    }
  };

  /** 镜头重修第 2 步：对齐指令与切点时刻按新秒数逐字重算。 */
  const rebuild = async () => {
    if (!project) return;
    setBusy(true);
    try {
      const next = await hibtvApi.rebuildSegment(project.id, segId);
      setSeg(next);
      toast(`提示词已按 ${next.cuts.length} 镜重算（${next.planSeconds.toFixed(1)}s）`);
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
            <span className={s.url ? "ok" : "dot"}>●</span>
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
          <span className="time">
            0:00 / {fmtTime(seg.planSeconds)}
          </span>
          <span className="bar">
            <i style={{ width: "0%" }} />
          </span>
        </div>
        <div className="timeline" style={{ marginTop: 8 }}>
          {cuts.map((c, i) => (
            <span key={i} className={`tl ${i === 0 ? "on" : ""}`}>
              <span>
                {i + 1} · {Number(c.seconds).toFixed(1)}s
              </span>
            </span>
          ))}
        </div>
      </div>

      <div className="panel-sec">
        <div className="inline">
          <span className="chip">段长 {seg.planSeconds.toFixed(1)}s</span>
          <span style={{ marginLeft: "auto" }} />
          <button className="btn sm gold" disabled={busy || generating} onClick={() => void generate()}>
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
          引用素材 <span className="r">{(seg.refs || []).length} / 9</span>
        </div>
        <div className="tiny dim" style={{ marginBottom: 8 }}>
          出片时按「场景锚点 + 出场角色锚点 + 道具锚点」自动挂参考图（最多 9 张）；换图后要重跑这一段。
        </div>
        <div className="inline" style={{ flexWrap: "wrap", gap: 5 }}>
          {(seg.cuts?.[0]?.characters || []).map((c) => (
            <span className="chip" key={c}>
              角色 {c}
            </span>
          ))}
          {(seg.cuts?.[0]?.props || []).map((p) => (
            <span className="chip" key={p}>
              道具 {p}
            </span>
          ))}
        </div>
        <div className="refnote">已挂上传图 {(seg.refs || []).length} 张</div>
      </div>

      <div className="panel-sec">
        <div className="panel-title">生成设置</div>
        <div className="tiny dim">
          Minimax-h3 · {project.aspectRatio} · 768p；按切镜 {cuts.length} 镜拆提示词，对白与音效随片出。
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
        <span className="chip gold">
          {(seg?.planSeconds || 0).toFixed(1)}s / {status?.segMaxSeconds || 10}s
        </span>
        <span className="chip">{cuts.length} 镜</span>
        {seg && seg.versions.length > 1 && <span className="chip gold">已出 {seg.versions.length} 版</span>}
        <span className="sp" style={{ marginLeft: "auto" }} />
        <button className="btn xs gold" disabled={busy || generating} onClick={() => void rebuild()}>
          ↻ 按当前镜头重算提示词
        </button>
        <button className="btn xs" onClick={() => navigate(`/hibtv/storyboard/${ep}`)}>
          回本集总表
        </button>
      </div>

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
            </div>
          </div>

          <div className="inline" style={{ margin: "14px 0 8px" }}>
            <span className="h2" style={{ margin: 0 }}>
              镜头
            </span>
            <span className="tiny dim">分镜图可选，不出也能出片</span>
            <span className="sp" style={{ marginLeft: "auto" }} />
            <button className="btn xs" onClick={() => void generate()}>
              ↻ 用当前提示词出片
            </button>
          </div>

          {cuts.map((cut, i) => {
            const from = starts[i] ?? 0;
            const to = Math.round((from + Number(cut.seconds || 0)) * 10) / 10;
            return (
              <div className="shotcard" key={i}>
                <div className="shot-hd">
                  <span className="si">{i + 1}</span>
                  <span className="rng">
                    {from}–{to}s
                  </span>
                  <span className="chip" title="双击改这一镜的秒数（1–5.5s）">
                    <InlineEdit
                      value={Number(cut.seconds).toFixed(1)}
                      onSave={(v) => {
                        const n = Number(v.replace(/[^\d.]/g, ""));
                        if (!n || n < 1 || n > 5.5) {
                          toast("单镜秒数需在 1–5.5 秒之间（切镜上限 5.5s）");
                          return;
                        }
                        void patchSeg(`cuts.${i}.seconds`, Math.round(n * 10) / 10, `镜 ${i + 1} 秒数`);
                      }}
                    />{" "}秒
                  </span>
                  <span className="chip">{cut.size || "中景"}</span>
                  <span className="chip">{cut.camera || "固定"}</span>
                  <span className="chip">{cut.composition || cut.lens || "—"}</span>
                  <span className="sp" style={{ marginLeft: "auto" }} />
                  <span className="tiny dim">{cut.focus || ""}</span>
                </div>
                <div className="shot-body">
                  <div>
                    <Plate kind="frame" label={`镜 ${i + 1} · 分镜图`} view="可选" ratio="16 / 10" />
                    <div className="inline" style={{ marginTop: 6, flexWrap: "wrap", gap: 4 }}>
                      {(cut.characters || []).map((c) => (
                        <span className="chip tiny" key={c}>
                          {c}
                        </span>
                      ))}
                      {(cut.props || []).map((p) => (
                        <span className="chip tiny" key={p}>
                          {p}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="shottext">
                    <div>
                      <span className="k">镜头</span> {i + 1} · {from}–{to} 秒
                    </div>
                    <div style={{ marginTop: 6 }}>
                      <InlineEdit
                        multiline
                        value={cut.shot || ""}
                        placeholder="（双击写这一镜的画面）"
                        onSave={(v) => void patchSeg(`cuts.${i}.shot`, v, `镜 ${i + 1} 描述`)}
                      />
                    </div>
                    <div style={{ marginTop: 6, color: "#8f877b" }}>
                      {cut.frame || cut.eyeline || ""} {cut.stability || ""}
                    </div>
                  </div>
                </div>
              </div>
            );
          })}

          <div className="card" style={{ marginTop: 14 }}>
            <div className="card-hd">
              <span className="t">h3 视频提示词（逐字发给视频模型）</span>
              <span className="chip">对齐指令 + 视听描述</span>
              <span style={{ marginLeft: "auto" }} />
              <button className="btn xs" onClick={() => void generate()} disabled={busy || generating}>
                重新生成视频
              </button>
            </div>
            <div className="card-bd">
              <InlineEdit
                multiline
                className="promptbox"
                style={{
                  display: "block",
                  maxHeight: 320,
                  overflow: "auto",
                  whiteSpace: "pre-wrap",
                  margin: 0,
                  fontFamily: "var(--mono)",
                  fontSize: 11.5,
                }}
                value={seg.h3Prompt || ""}
                placeholder="（还没有提示词；双击可改，或点右上「按当前镜头重算提示词」）"
                onSave={(v) => void patchSeg("h3Prompt", v, "h3 提示词")}
              />
              <div className="tiny dim" style={{ marginTop: 8 }}>
                改镜头（秒数 / 描述）后点「按当前镜头重算提示词」，对齐指令与切点时刻会逐字重写；
                出片直接用这段提示词，不叠云端增强。
              </div>
              <div style={{ marginTop: 10 }}>
                <InlineEdit
                  multiline
                  value={seg.data?.note || ""}
                  placeholder="（这一段的说明；双击可写）"
                  onSave={async (v) => {
                    if (!project) return;
                    try {
                      await hibtvApi.patchDoc(project.id, "storyboard", {
                        path: `episodes.0.segments.${seg.idx}.note`,
                        value: v,
                        ep,
                      });
                      toast("已改这一段说明（分镜文档已更新）");
                      await load();
                      void reload();
                    } catch (err) {
                      toast(errText(err));
                    }
                  }}
                />
              </div>
            </div>
          </div>
        </>
      )}
    </Stage>
  );
}
