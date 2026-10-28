/** 出视频：整集成片 + 时间轴 + 按段重跑 + 导出（cutlist 计划 / 实测并列）。
 *
 * 出片只有一条路：ref2v + 每切一张关键帧。这里的前置校验就是「关键帧齐否」：
 * 缺图 / 段长超 13s / 切数≠图数 → 段级出片按钮置灰，并说清楚卡在哪。
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { hibtvApi, mediaUrl } from "../api";
import { Stage } from "../HibtvLayout";
import { Empty, GateChips, Loading, PageHead, VideoBox, fmtTime } from "../components/ui";
import { errText, useDoc, useHibtv } from "../state";
import type { FrameList, HibtvFrame, OutlineData, RenderItem, Segment, SegmentList } from "../types";

export default function VideoPage() {
  const { ep: epParam = "1" } = useParams();
  const ep = Math.max(1, Number(epParam) || 1);
  const navigate = useNavigate();
  const { project, status, toast } = useHibtv();
  const { data: outlineData } = useDoc("outline");
  const { doc: boardDoc } = useDoc("storyboard", ep);
  const outline = (outlineData || {}) as OutlineData;

  const [list, setList] = useState<SegmentList | null>(null);
  const [renders, setRenders] = useState<RenderItem[]>([]);
  const [frames, setFrames] = useState<HibtvFrame[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!project) return;
    setLoading(true);
    try {
      const [segRes, renderRes, frameRes] = await Promise.all([
        hibtvApi.listSegments(project.id, ep),
        hibtvApi.listRenders(project.id, ep),
        hibtvApi.listFrames(project.id, ep),
      ]);
      setList(segRes);
      setRenders(renderRes);
      setFrames((frameRes as FrameList).frames || []);
    } catch (err) {
      toast(errText(err));
    } finally {
      setLoading(false);
    }
  }, [project, ep, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  // 拼接作业在队列里跑完时刷新成片
  useEffect(() => {
    const timer = window.setInterval(() => {
      void load();
    }, 6000);
    return () => window.clearInterval(timer);
  }, [load]);

  const segments = list?.segments || [];
  const latest = renders[0];
  // 版本预览：后端还没有「选定 render」字段，先只切前端预览（刷新回最新版），
  // 播放器 / 体积 / 时长 / 成片清单 / 导出都跟着 shown 走，避免“看到 v2 下到 v1”。
  const [pickId, setPickId] = useState<number | null>(null);
  const shown = renders.find((r) => r.id === pickId) || latest;
  const withVideo = segments.filter((s) => s.url).length;
  const cuts = segments.reduce((n, s) => n + (s.cuts || []).length, 0);
  const planSecs = segments.reduce((n, s) => n + (s.planSeconds || 0), 0);
  const sceneName = (idx?: number) => outline.scenes?.[(idx || 1) - 1]?.name || `场景 ${idx || 1}`;
  const cap = status?.segMaxSeconds || 13;

  /** 出片前置校验：段长 / 图位齐否 / 切数=图数（ref2v 少一张图就会切到空白）。 */
  const readiness = (seg: Segment) => {
    const rows = frames.filter((f) => f.segId === seg.id);
    const cuts = (seg.cuts || []).length;
    const missing = rows.filter((f) => !f.url).map((f) => f.cutIndex);
    const done = rows.length - missing.length;
    const total = rows.length || cuts;
    if (seg.oversize)
      return { ok: false, done, total, why: `段长 ${seg.planSeconds.toFixed(1)}s 超过 ${cap}s：需重跑本集 storyboard` };
    if (!rows.length) return { ok: false, done, total, why: "还没有图位：先跑本集分镜" };
    if (missing.length) return { ok: false, done, total, why: `缺关键帧 f${missing.join(" / f")}` };
    if (rows.length !== cuts)
      return { ok: false, done, total, why: `切数 ${cuts} ≠ 关键帧 ${rows.length}` };
    return { ok: true, done, total, why: `ref2v 原样直出：${rows.length} 张关键帧` };
  };

  const genOne = async (seg: Segment) => {
    if (!project) return;
    const state = readiness(seg);
    if (!state.ok) {
      toast(`${seg.id}：${state.why}`);
      return;
    }
    try {
      const res = await hibtvApi.generateSegment(project.id, seg.id, {});
      toast(`${seg.id} 已排队：v${res.v} · ${res.seconds}s（${res.frames} 帧）· ${res.refs} 张关键帧`);
      void load();
    } catch (err) {
      toast(errText(err));
    }
  };

  const compose = async () => {
    if (!project) return;
    try {
      await hibtvApi.renderEpisode(project.id, ep);
      toast(`已排队：第 ${ep} 集整集拼接`);
      void load();
    } catch (err) {
      toast(errText(err));
    }
  };

  const side = (
    <>
      <div className="side-head">交付 · 成不成片 · VIDEO</div>
      <div className="ep-list">
        {Array.from({ length: Math.max(1, project?.episodes || 1) }, (_, i) => i + 1).map((n) => (
          <div
            key={n}
            className={`ep-item ${ep === n ? "on" : ""}`}
            onClick={() => navigate(`/hibtv/video/${n}`)}
          >
            <div className="r1">
              <span className="no">第 {n} 集</span>
              <span className="t">
                {project?.docs?.[`storyboard#${n}`]?.status === "done" ? "已分镜" : "未分镜"}
              </span>
            </div>
            <div className="r2">
              <em>成片</em> {n === ep ? (latest ? `v${latest.version} · ${fmtTime(latest.durationSec)}` : "待合成") : "—"}
            </div>
          </div>
        ))}
      </div>
      <div className="tiny dim" style={{ padding: "12px 8px", lineHeight: 1.8 }}>
        按段的顺序把「每段选中的那一版」直接拼起来；哪一段不满意就只重跑那一段。
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

  const panel = (
    <>
      <div className="panel-sec">
        <div className="panel-title">
          参考图 · 关键帧 <span className="r">
            {frames.filter((f) => f.url).length} / {frames.length} 已出
          </span>
        </div>
        <div className="tiny dim" style={{ lineHeight: 1.8, marginBottom: 8 }}>
          出片时按切序号挂 f1..fN（≤ 9 张）；换图去段的「图位条」里重出 / 上传，然后只重跑那一段。
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(46px, 1fr))", gap: 4 }}>
          {frames.map((f) => (
            <a
              key={f.id}
              href={f.url ? mediaUrl(f.url) : undefined}
              target="_blank"
              rel="noreferrer"
              title={`${f.segId} · f${f.cutIndex}`}
              style={{
                display: "block",
                aspectRatio: "9 / 12",
                borderRadius: 4,
                overflow: "hidden",
                background: "#141210",
                border: `1px solid ${f.url ? "var(--line-3)" : "rgba(224,116,90,.45)"}`,
              }}
            >
              {f.url ? (
                <img
                  src={mediaUrl(f.url)}
                  alt={`${f.segId}-f${f.cutIndex}`}
                  loading="lazy"
                  style={{ width: "100%", height: "100%", objectFit: "cover" }}
                />
              ) : (
                <span
                  className="tiny dim"
                  style={{ display: "grid", placeItems: "center", height: "100%", fontSize: 9 }}
                >
                  缺
                </span>
              )}
            </a>
          ))}
          {!frames.length && <span className="tiny dim">还没有关键帧</span>}
        </div>
      </div>
      <div className="panel-sec">
        <div className="panel-title">导出</div>
        <div className="field">
          <div className="label">分辨率</div>
          <div className="inline">
            <span className="chip gold">
              {project.aspectRatio === "16:9" ? "1920 × 1080" : "1080 × 1920"}（本剧）
            </span>
          </div>
        </div>
        <div className="field">
          <div className="label">音轨</div>
          <div className="inline">
            <span className="chip gold">对白</span>
            <span className="chip">音效</span>
            <span className="chip ghost">字幕 · 后续版本</span>
          </div>
        </div>
        {shown ? (
          <a
            className="btn gold"
            style={{ width: "100%", textAlign: "center", display: "block" }}
            href={`${mediaUrl(shown.url)}&download=1`}
          >
            导出 MP4
          </a>
        ) : (
          <button
            className="btn gold"
            style={{ width: "100%" }}
            onClick={() => void compose()}
            disabled={!withVideo}
            title={withVideo ? "按段顺序拼成本集" : "还没有任何一段出过片，先出一段再合成"}
          >
            先合成成片
          </button>
        )}
      </div>
      <div className="panel-sec">
        <div className="panel-title">版本</div>
        <div className="inline" style={{ flexWrap: "wrap" }}>
          {renders.length ? (
            renders.map((r) => (
              <span
                key={r.id}
                className={`chip i ${r.id === shown?.id ? "gold" : ""}`}
                style={{ cursor: "pointer" }}
                title={r.id === latest?.id ? "最新合成版本（正在预览）" : "点一下预览这一版"}
                onClick={() => setPickId(r.id)}
              >
                v{r.version}
                {r.id === latest?.id ? " 最新" : ""}
                {r.id === shown?.id ? " · 预览中" : ""}
              </span>
            ))
          ) : (
            <span className="tiny dim">还没有合成记录</span>
          )}
        </div>
        <div className="tiny dim" style={{ marginTop: 6 }}>
          重新合成会追加版本，不覆盖；点任意版本可切到该版预览，导出跟当前预览版本走。
        </div>
      </div>
    </>
  );

  return (
    <Stage side={side} panel={panel}>
      <PageHead
        eyebrow="交付层 · 成不成片 · VIDEO"
        title={`出视频 / 第 ${ep} 集`}
        lede="按段的顺序把「每段选中的那一版」直接拼起来。哪一段不满意就只重跑那一段，整集只在最后合成一次。"
        right={
          <>
            <GateChips gates={boardDoc?.gates} />
            <span className={`chip ${frames.length && frames.every((f) => f.url) ? "green" : "red"}`}>
              关键帧 {frames.filter((f) => f.url).length}/{frames.length}
            </span>
            <span className="chip gold">段 ≤ {cap}s</span>
            <button className="btn gold" onClick={() => void compose()} disabled={!withVideo}
              title={withVideo ? "按段顺序拼成本集" : "还没有任何一段出过片，先出一段再合成"}>
              {latest ? "重新合成" : "合成成片"}
            </button>
          </>
        }
      />

      <div className="split3" style={{ margin: "14px 0" }}>
        <div className="stat">
          <span className="k">段</span>
          <span className="v">
            {segments.length} <small>段</small>
          </span>
        </div>
        <div className="stat">
          <span className="k">镜头</span>
          <span className="v">
            {cuts} <small>镜</small>
          </span>
        </div>
        <div className="stat">
          <span className="k">成片</span>
          <span className="v">
            {latest ? fmtTime(latest.durationSec) : "—"}{" "}
            <small>{latest ? `${latest.durationSec.toFixed(1)}s` : "待合成"}</small>
          </span>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-hd">
          <span className="t">整集成片</span>
          <span className="chip gold">
            {withVideo} / {segments.length} 段有片
          </span>
          {shown && (
            <span className="chip">{(shown.size / 1024 / 1024).toFixed(1)} MB</span>
          )}
          <span style={{ marginLeft: "auto" }} />
          {shown && <span className="chip">v{shown.version}</span>}
        </div>
        <div className="card-bd">
          <VideoBox
            src={shown?.url}
            label={`第 ${ep} 集成片`}
            view={
              shown
                ? `${shown.durationSec.toFixed(1)}s · ${segments.length} 段 · v${shown.version}`
                : "还没有成片"
            }
            ratio={project.aspectRatio === "16:9" ? "16 / 9" : "9 / 16"}
            maxHeight={360}
          />
          <div className="timeline" style={{ marginTop: 10 }}>
            {segments.map((s, i) => (
              <span
                key={s.id}
                className={`tl ${s.oversize ? "bad" : i === 0 ? "on" : ""}`}
                onClick={() => navigate(`/hibtv/storyboard/${ep}/${s.id}`)}
                title={`${s.id} · ${s.planSeconds.toFixed(1)}s${s.oversize ? " · 超限，需重跑分镜" : ""}`}
              >
                {i + 1}
              </span>
            ))}
          </div>
          <div className="inline tiny dim" style={{ marginTop: 8, flexWrap: "wrap" }}>
            <span>计划 {planSecs.toFixed(1)}s</span>
            {shown && <span>· 实测 {shown.durationSec.toFixed(1)}s</span>}
            {shown && <span>· {shown.createdAt}</span>}
            <span className="sp" style={{ marginLeft: "auto" }} />
            <Link to={`/hibtv/storyboard/${ep}`}>回本集段表 →</Link>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14, overflow: "hidden" }}>
        <div className="card-hd">
          <span className="t">按段看 / 按段重跑</span>
          <span className="chip">{segments.length} 段</span>
          <span className="chip" title="段级出片 = ref2v + 每切一张关键帧">
            出片前置：关键帧齐 + 段长 ≤ {cap}s
          </span>
          <span style={{ marginLeft: "auto" }} />
          <span className="tiny dim">点一段进一段的镜头页</span>
        </div>
        <table className="tbl">
          <thead>
            <tr>
              <th>段</th>
              <th>场次</th>
              <th>镜</th>
              <th>时长</th>
              <th>图位</th>
              <th>成片</th>
              <th>版本</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {segments.map((s) => {
              const st = readiness(s);
              return (
                <tr key={s.id}>
                  <td className="id">{s.id}</td>
                  <td className="small">{sceneName(s.data?.sceneIndex)}</td>
                  <td className="mono">{(s.cuts || []).length}</td>
                  <td className="mono">
                    {s.oversize ? (
                      <span className="chip red">{s.planSeconds.toFixed(1)}s 超限</span>
                    ) : (
                      `${s.planSeconds.toFixed(1)}s`
                    )}
                  </td>
                  <td>
                    <span className={`chip tiny ${st.ok ? "green" : "gold"}`} title={st.why}>
                      {st.total ? `${st.done}/${st.total}` : "无图位"}
                    </span>
                  </td>
                  <td>
                    {s.url ? (
                      <span className="chip green">有片</span>
                    ) : (
                      <span className="chip ghost">待出</span>
                    )}
                  </td>
                  <td className="small dim">v{s.currentVersion || 0}</td>
                  <td style={{ textAlign: "right" }}>
                    <button
                      className={`btn xs ${st.ok ? "gold" : ""}`}
                      onClick={() => void genOne(s)}
                      title={st.why}
                    >
                      {s.url ? "重出" : "出片"}
                    </button>
                    <button
                      className="btn xs ghost"
                      style={{ marginLeft: 4 }}
                      onClick={() => navigate(`/hibtv/storyboard/${ep}/${s.id}`)}
                    >
                      看镜头
                    </button>
                  </td>
                </tr>
              );
            })}
            {!segments.length && (
              <tr>
                <td colSpan={8} style={{ padding: 18 }}>
                  {loading ? (
                    <Loading text="读取本集分镜…" />
                  ) : (
                    <Empty
                      glyph="片"
                      title="本集还没有分镜段"
                      sub="先跑分镜拿到段表，再逐段出片，最后整集拼接。"
                    >
                      <button
                        className="btn gold"
                        onClick={() => navigate(`/hibtv/storyboard/${ep}`)}
                      >
                        去生成本集分镜
                      </button>
                    </Empty>
                  )}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {shown && shown.cutlist?.length ? (
        <div className="card">
          <div className="card-hd">
            <span className="t">成片清单 · 计划 vs 实测</span>
            <span className="chip">{shown.cutlist.length} 段</span>
            <span style={{ marginLeft: "auto" }} />
            <a className="link tiny" href={`${mediaUrl(shown.url)}&download=1`}>
              下载 v{shown.version}
            </a>
          </div>
          <div className="card-bd">
            {shown.cutlist.map((row, i) => {
              // 未探到 ffprobe 时 actual 为 null，退回按计划秒显示并标灰
              const plan = Number(row.plan || 0);
              const actual = row.actual === null || row.actual === undefined ? plan : Number(row.actual);
              const off = actual - plan;
              return (
                <div className="inline" key={`${row.file || i}`} style={{ padding: "4px 0" }}>
                  <span className="mono tiny" style={{ minWidth: 76 }}>
                    {row.file || `#${row.index || i + 1}`}
                  </span>
                  <span className="tiny dim">计划 {plan.toFixed(1)}s</span>
                  <span className="tiny dim">实测 {actual.toFixed(1)}s</span>
                  <span className="sp" style={{ marginLeft: "auto" }} />
                  <span className={`tiny ${Math.abs(off) > 0.6 ? "diff" : "dim"}`}>
                    差 {off.toFixed(1)}s
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="tiny dim">
          成片按 ffmpeg concat 顺序拼接（同参数优先 -c copy，不一致时回退重编码）；音频为 {status?.compose?.available ? "各段原声" : "各段原声（未检测到 ffmpeg 时无法拼接）"}。
        </div>
      )}
    </Stage>
  );
}
