/** 出视频：整集成片 + 时间轴 + 按段重跑 + 导出（cutlist 计划 / 实测并列）。 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { hibtvApi, mediaUrl } from "../api";
import { Stage } from "../HibtvLayout";
import { Empty, GateChips, Loading, PageHead, VideoBox, fmtTime } from "../components/ui";
import { errText, useDoc, useHibtv } from "../state";
import type { OutlineData, RenderItem, SegmentList } from "../types";

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
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!project) return;
    setLoading(true);
    try {
      const [segRes, renderRes] = await Promise.all([
        hibtvApi.listSegments(project.id, ep),
        hibtvApi.listRenders(project.id, ep),
      ]);
      setList(segRes);
      setRenders(renderRes);
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
  const withVideo = segments.filter((s) => s.url).length;
  const cuts = segments.reduce((n, s) => n + (s.cuts || []).length, 0);
  const planSecs = segments.reduce((n, s) => n + (s.planSeconds || 0), 0);
  const sceneName = (idx?: number) => outline.scenes?.[(idx || 1) - 1]?.name || `场景 ${idx || 1}`;

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
          引用素材 <span className="r">{withVideo} 段有片</span>
        </div>
        <div className="tiny dim" style={{ lineHeight: 1.8 }}>
          成片阶段只读：这里列出这集所有段真正发出去的参考图。要换素材去对应段的「引用素材」里改，改完可以只重跑那一段。
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
        {latest ? (
          <a
            className="btn gold"
            style={{ width: "100%", textAlign: "center", display: "block" }}
            href={`${mediaUrl(latest.url)}&download=1`}
          >
            导出 MP4
          </a>
        ) : (
          <button className="btn gold" style={{ width: "100%" }} onClick={() => void compose()}>
            先合成成片
          </button>
        )}
      </div>
      <div className="panel-sec">
        <div className="panel-title">版本</div>
        <div className="inline" style={{ flexWrap: "wrap" }}>
          {renders.length ? (
            renders.map((r) => (
              <span key={r.id} className={`chip i ${r.id === latest?.id ? "gold" : ""}`}>
                v{r.version}
                {r.id === latest?.id ? " 当前" : ""}
              </span>
            ))
          ) : (
            <span className="tiny dim">还没有合成记录</span>
          )}
        </div>
        <div className="tiny dim" style={{ marginTop: 6 }}>
          重新合成会追加版本，不覆盖；旧版本可随时回切。
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
          {latest && (
            <span className="chip">{(latest.size / 1024 / 1024).toFixed(1)} MB</span>
          )}
          <span style={{ marginLeft: "auto" }} />
          {latest && <span className="chip">v{latest.version}</span>}
        </div>
        <div className="card-bd">
          <VideoBox
            src={latest?.url}
            label={`第 ${ep} 集成片`}
            view={
              latest
                ? `${latest.durationSec.toFixed(1)}s · ${segments.length} 段 · v${latest.version}`
                : "还没有成片"
            }
            ratio={project.aspectRatio === "16:9" ? "16 / 9" : "9 / 16"}
            maxHeight={360}
          />
          <div className="timeline" style={{ marginTop: 10 }}>
            {segments.map((s, i) => (
              <span
                key={s.id}
                className={`tl ${i === 0 ? "on" : ""}`}
                onClick={() => navigate(`/hibtv/storyboard/${ep}/${s.id}`)}
                title={`${s.id} · ${s.planSeconds.toFixed(1)}s`}
              >
                {i + 1}
              </span>
            ))}
          </div>
          <div className="inline tiny dim" style={{ marginTop: 8, flexWrap: "wrap" }}>
            <span>计划 {planSecs.toFixed(1)}s</span>
            {latest && <span>· 实测 {latest.durationSec.toFixed(1)}s</span>}
            {latest && <span>· {latest.createdAt}</span>}
            <span className="sp" style={{ marginLeft: "auto" }} />
            <Link to={`/hibtv/storyboard/${ep}`}>回本集段表 →</Link>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14, overflow: "hidden" }}>
        <div className="card-hd">
          <span className="t">按段看 / 按段重跑</span>
          <span className="chip">{segments.length} 段</span>
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
              <th>成片</th>
              <th>版本</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {segments.map((s) => (
              <tr key={s.id}>
                <td className="id">{s.id}</td>
                <td className="small">{sceneName(s.data?.sceneIndex)}</td>
                <td className="mono">{(s.cuts || []).length}</td>
                <td className="mono">{s.planSeconds.toFixed(1)}s</td>
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
                    className="btn xs"
                    onClick={() => navigate(`/hibtv/storyboard/${ep}/${s.id}`)}
                  >
                    看镜头
                  </button>
                </td>
              </tr>
            ))}
            {!segments.length && (
              <tr>
                <td colSpan={7} style={{ padding: 18 }}>
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

      {latest && latest.cutlist?.length ? (
        <div className="card">
          <div className="card-hd">
            <span className="t">成片清单 · 计划 vs 实测</span>
            <span className="chip">{latest.cutlist.length} 段</span>
            <span style={{ marginLeft: "auto" }} />
            <a className="link tiny" href={`${mediaUrl(latest.url)}&download=1`}>
              下载 v{latest.version}
            </a>
          </div>
          <div className="card-bd">
            {latest.cutlist.map((row, i) => {
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
          成片按 ffmpeg concat 顺序拼接（同参数优先 -c copy，不一致时回退重编码）；音频为 {status?.compose?.ffmpeg ? "各段原声" : "各段原声（未检测到 ffmpeg 时无法拼接）"}。
        </div>
      )}
    </Stage>
  );
}
