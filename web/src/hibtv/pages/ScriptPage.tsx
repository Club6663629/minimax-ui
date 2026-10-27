/** 剧本：左侧集列表 + 当集节拍表（台词逐字、只切句；每一拍可双击就地改）。 */
import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { hibtvApi } from "../api";
import { Stage } from "../HibtvLayout";
import { Empty, GateChips, GateList, GenBar, InlineEdit, Loading, PageHead } from "../components/ui";
import { useDoc, useHibtv, useJob, usePatch } from "../state";
import type { OutlineData, ScriptEpisode } from "../types";

interface Params {
  charsPerSecond?: number;
  actionSeconds?: number;
  maxLineChars?: number;
  tolerance?: number;
}

export default function ScriptPage() {
  const { ep: epParam = "1" } = useParams();
  const ep = Math.max(1, Number(epParam) || 1);
  const navigate = useNavigate();
  const { project, runJob, toast } = useHibtv();
  const { data, doc, patch, loading } = usePatch("script", ep);
  const { data: outlineData } = useDoc("outline");
  const job = useJob("script", ep);

  const outline = (outlineData || {}) as OutlineData;
  const params = (data?.params || {}) as Params;
  const cps = Number(params.charsPerSecond) || 4.5;
  const actionSec = Number(params.actionSeconds) || 2.5;
  const episode: ScriptEpisode | undefined = data?.episodes?.[0];
  const scenes = episode?.scenes || [];

  const charName = useMemo(() => {
    const map = new Map<string, string>();
    for (const c of outline.characters || []) map.set(c.id, c.name);
    return map;
  }, [outline.characters]);

  const sceneName = useMemo(() => {
    const map = new Map<string, string>();
    for (const s of outline.scenes || []) map.set(s.id, s.name);
    return map;
  }, [outline.scenes]);

  const beatSeconds = (beat: { line?: string; action?: string }) =>
    beat.line ? Math.max(1, Math.round((beat.line.replace(/\s/g, "").length / cps) * 10) / 10) : actionSec;

  const totalSeconds = scenes.reduce(
    (sum, sc) => sum + (sc.flow || []).reduce((a, b) => a + beatSeconds(b), 0),
    0,
  );
  const target = Number(episode?.targetSeconds) || 0;
  const totalBeats = scenes.reduce((n, sc) => n + (sc.flow || []).length, 0);
  const totalLines = scenes.reduce(
    (n, sc) => n + (sc.flow || []).filter((b) => b.line).length,
    0,
  );

  const side = (
    <>
      <div className="side-head">剧本 · SCRIPT</div>
      <div className="ep-list">
        {Array.from({ length: Math.max(1, project?.episodes || 1) }, (_, i) => i + 1).map((n) => {
          const meta = project?.docs?.[`script#${n}`];
          const row = outline.episodes?.find((e) => e.ep === n);
          return (
            <div
              key={n}
              className={`ep-item ${ep === n ? "on" : ""}`}
              onClick={() => navigate(`/hibtv/script/${n}`)}
            >
              <div className="r1">
                <span className="no">第 {n} 集</span>
                <span className="t">
                  {meta?.status === "done" ? "已解析" : meta?.status === "failed" ? "失败" : "待生成"}
                </span>
              </div>
              <div className="r2">
                <em>钩子</em> {(row?.hook || "—").slice(0, 26)}
              </div>
            </div>
          );
        })}
      </div>
      <div className="tiny dim" style={{ padding: "12px 8px", lineHeight: 1.8 }}>
        每集一页：一页内是这一集的拍，每一拍是一句台词或一个动作。台词逐字保留，只按标点切句与拆拍。
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
      eyebrow="本 · 怎么说 · SCRIPT"
      title={`剧本 / 第 ${ep} 集`}
      lede="剧本是上游，不是产物：hibtv 不重写台词，只把每一拍拆成可拍的秒数，并把表演提示留给演员与镜头。"
      right={
        <>
          <GateChips gates={doc?.gates} />
          <button
            className="btn"
            onClick={() => toast("朗读为后续能力：当前版本先把台词逐字结构化，供分镜与配音使用")}
          >
            ◁ 朗读
          </button>
          <button className="btn" onClick={() => navigate("/hibtv/outline/ep")}>
            看大纲
          </button>
          <button className="btn" onClick={() => navigate(`/hibtv/storyboard/${ep}`)}>
            看分镜
          </button>
          <button className="btn gold" onClick={() => void runJob("script", ep)}>
            {episode ? "重新解析本集" : "生成本集剧本"}
          </button>
        </>
      }
    />
  );

  if (loading && !data) {
    return (
      <Stage side={side}>
        {head}
        <Loading />
      </Stage>
    );
  }

  if (!episode) {
    return (
      <Stage side={side}>
        {head}
        <GenBar job={job} onCancel={job ? () => void hibtvApi.cancelJob(project.id, job.id) : undefined} />
        <Empty
          glyph="本"
          title={`第 ${ep} 集还没有节拍表`}
          sub={
            job?.status === "failed"
              ? `生成失败：${job.error}`
              : "从上传的剧本里解析这一集的场次与拍：台词逐字保留，动作节拍不写引号对白。"
          }
        >
          <button className="btn gold" onClick={() => void runJob("script", ep)}>
            生成第 {ep} 集剧本
          </button>
        </Empty>
        <GateList gates={doc?.gates} />
      </Stage>
    );
  }

  const bar = (
    <div className="inline" style={{ margin: "10px 0" }}>
      <span className="chip gold">
        {totalSeconds.toFixed(1)}s / 目标 {target.toFixed(1)}s
      </span>
      <span className="chip">
        {scenes.length} 场 · {totalBeats} 拍 · {totalLines} 台词
      </span>
      <span className="chip ghost">
        爽点认领 <b style={{ color: "var(--gold)" }}>{(episode.beatsClaimed || []).join(" / ") || "—"}</b>
      </span>
      <span className="sp" style={{ marginLeft: "auto" }} />
      <span className="tiny dim">双击台词 / 表演提示可改</span>
    </div>
  );

  const hooks = (
    <div className="hookgrid">
      <div className="hook">
        <div className="k">钩子</div>
        <div className="v">
          <InlineEdit
            value={episode.hook || ""}
            placeholder="（还没有钩子；双击补一句）"
            onSave={(v) => void patch("episodes.0.hook", v, `第 ${ep} 集钩子`)}
          />
        </div>
      </div>
      <div className="hook">
        <div className="k">断点</div>
        <div className="v">
          <InlineEdit
            value={episode.cliff || ""}
            placeholder="（还没有断点；双击补一句）"
            onSave={(v) => void patch("episodes.0.cliff", v, `第 ${ep} 集断点`)}
          />
        </div>
      </div>
    </div>
  );

  return (
    <Stage side={side}>
      {head}
      {bar}
      {hooks}
      <GenBar job={job} onCancel={job ? () => void hibtvApi.cancelJob(project.id, job.id) : undefined} />
      <GateList gates={doc?.gates} />

      {scenes.map((sc, si) => {
        const sceneTotal = (sc.flow || []).reduce((a, b) => a + beatSeconds(b), 0);
        return (
          <div className="sec" key={`${sc.sceneId}-${si}`}>
            <div className="inline" style={{ marginBottom: 8, flexWrap: "wrap" }}>
              <span className="mono" style={{ color: "var(--gold-2)" }}>
                场次 {si + 1}
              </span>
              <span className="chip">{sc.sceneId}</span>
              <b style={{ fontSize: 13 }}>{sceneName.get(sc.sceneId) || "未命名场景"}</b>
              {(sc.characters || []).length > 0 && <span className="tiny dim">人物</span>}
              {(sc.characters || []).map((c) => (
                <span className="chip" key={c}>
                  {charName.get(c) || c}
                </span>
              ))}
              {sc.lighting ? <span className="chip ghost">光 {sc.lighting}</span> : null}
              <span className="sp" style={{ marginLeft: "auto" }} />
              <span className="tiny dim">场次合计 {sceneTotal.toFixed(1)}s</span>
            </div>
            <div className="beats">
              <div className="beat-hd">
                <span>#</span>
                <span>角色</span>
                <span>台词 / 动作</span>
                <span>表演提示</span>
                <span style={{ textAlign: "right" }}>秒数</span>
              </div>
              {(sc.flow || []).map((b, bi) => (
                <div className="beat" key={bi}>
                  <span className="n">{bi + 1}</span>
                  <span className={`who ${b.line ? "" : "act"}`}>
                    {b.line ? charName.get(b.speaker || "") || b.speaker || "VO" : "动作"}
                  </span>
                  <span className="line">
                    <InlineEdit
                      value={b.line || b.action || ""}
                      multiline
                      onSave={(v) =>
                        void patch(
                          `episodes.0.scenes.${si}.flow.${bi}.${b.line ? "line" : "action"}`,
                          v,
                          `第 ${ep} 集 场 ${si + 1} 第 ${bi + 1} 拍`,
                        )
                      }
                    />
                  </span>
                  <span className="note">
                    {b.line ? (
                      <InlineEdit
                        value={b.delivery || ""}
                        placeholder="—"
                        onSave={(v) =>
                          void patch(
                            `episodes.0.scenes.${si}.flow.${bi}.delivery`,
                            v,
                            `第 ${ep} 集 场 ${si + 1} 第 ${bi + 1} 拍提示`,
                          )
                        }
                      />
                    ) : (
                      "—"
                    )}
                  </span>
                  <span className="sec">{beatSeconds(b).toFixed(1)} s</span>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      <div className="inline tiny dim" style={{ marginTop: 8, flexWrap: "wrap" }}>
        <span>全集 {totalSeconds.toFixed(1)}s</span>
        <span>·</span>
        <span>目标 {target.toFixed(1)}s（±{Math.round((Number(params.tolerance) || 0.15) * 100)}%）</span>
        <span>·</span>
        <span>单句 ≤ {Number(params.maxLineChars) || 15} 字</span>
        <span>·</span>
        <span>台词带着「怎么说」，会写进后面每一镜的视频提示词</span>
      </div>
    </Stage>
  );
}
