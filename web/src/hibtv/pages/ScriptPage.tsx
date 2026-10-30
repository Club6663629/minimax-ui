/** 剧本：左侧集列表 + 当集节拍表（台词逐字、只切句；每一拍可双击就地改）。 */
import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { hibtvApi } from "../api";
import { Stage } from "../HibtvLayout";
import { CodexLight, useCodexGate } from "../components/CodexLight";
import { ReportFrame } from "../components/StageArtifacts";
import { Empty, GateChips, GateList, GenBar, InlineEdit, Loading, PageHead } from "../components/ui";
import { useDoc, useHibtv, useJob, usePatch } from "../state";
import type { OutlineData, ScriptEpisode, StagingData, StagingScene } from "../types";

interface Params {
  charsPerSecond?: number;
  actionSeconds?: number;
  maxLineChars?: number;
  tolerance?: number;
}

/** 每场次下的站位 bar：有报告就点开可拖动版报告，没排过就去排一版。 */
function StagingBar({
  scene,
  fallbackCast,
  hasReport,
  onOpen,
  onRestage,
}: {
  scene?: StagingScene;
  fallbackCast: number;
  hasReport: boolean;
  onOpen: () => void;
  onRestage: () => void;
}) {
  const ready = Boolean(scene) || hasReport;
  const n = scene?.cast?.length || fallbackCast;
  if (!ready) {
    return (
      <div className="genbar" style={{ marginBottom: 10 }}>
        <span className="chip ghost">尚未排位</span>
        <span className="tiny dim">这一场还没排站位；站位是分镜 blocking 的上游，先排好点位分镜才拍得顺。</span>
        <span className="sp" style={{ marginLeft: "auto" }} />
        <button className="btn xs gold" onClick={onRestage}>去排一版</button>
      </div>
    );
  }
  return (
    <div className="genbar" style={{ marginBottom: 10, cursor: "pointer" }} onClick={onOpen}>
      <span className="chip green">站位 已排好{n ? ` · ${n} 人同框` : ""}</span>
      <span className="tiny dim">
        {scene?.why || "点位 / 朝向 / 群演已排定；换机位不会重排。点开看可拖动版报告。"}
      </span>
      <span className="sp" style={{ marginLeft: "auto" }} />
      <button className="btn xs" onClick={(e) => { e.stopPropagation(); onOpen(); }}>↗ 打开可拖动版</button>
      <button className="btn xs" onClick={(e) => { e.stopPropagation(); onRestage(); }}>重新排一版</button>
    </div>
  );
}

export default function ScriptPage() {
  const { ep: epParam = "1" } = useParams();
  const ep = Math.max(1, Number(epParam) || 1);
  const navigate = useNavigate();
  const { project, runJob, toast, openLayer, closeLayer } = useHibtv();
  const { data, doc, patch, loading } = usePatch("script", ep);
  const { data: outlineData } = useDoc("outline");
  const { data: stagingData, doc: stagingDoc } = useDoc("staging", ep);
  const job = useJob("script", ep);
  const gate = useCodexGate();

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

  const staging = (stagingData || {}) as StagingData;
  const stagingByScene = useMemo(() => {
    const map = new Map<string, StagingScene>();
    for (const s of staging.scenes || []) {
      const key = s.sceneId || (s.scene != null ? `#${s.scene}` : "");
      if (key) map.set(key, s);
    }
    return map;
  }, [staging.scenes]);
  const stagingReport = stagingDoc?.meta?.report || "";
  const stagingJobId = String(stagingDoc?.meta?.codex_job_id || "");
  const hasStagingReport = Boolean(stagingReport && stagingJobId);

  // 点击 bar → 弹层里内嵌可拖动版站位报告（复用 StagingPage 的 ReportFrame）
  const openStagingReport = () => {
    if (!project) return;
    if (!hasStagingReport) {
      navigate(`/hibtv/staging/${ep}`);
      return;
    }
    openLayer(
      <div className="drawer wide">
        <div className="drawer-hd">
          <div>
            <div className="tiny dim">站位 · 第 {ep} 集</div>
            <div className="t">可拖动版站位报告</div>
          </div>
          <span className="x" onClick={closeLayer}>✕</span>
        </div>
        <div className="drawer-bd">
          <div className="tiny dim" style={{ marginBottom: 8 }}>
            拖动结果不回写；要固化站位，改 docs/staging 后重跑分镜（分镜 blocking 读的就是它）。
          </div>
          <ReportFrame projectId={project.id} jobId={stagingJobId} rel={stagingReport} height={560} />
        </div>
        <div className="drawer-ft">
          <span className="tiny dim">staging-report.html · 可拖动版工具</span>
          <span style={{ flex: 1 }} />
          <button className="btn ghost" onClick={() => { closeLayer(); navigate(`/hibtv/staging/${ep}`); }}>去完整页</button>
          <button className="btn" onClick={closeLayer}>关闭</button>
        </div>
      </div>,
    );
  };

  const restage = () => {
    if (!gate.ok) {
      toast(gate.why);
      return;
    }
    void runJob("staging", ep);
  };

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

  const runScript = () => {
    // 网关掉线时不允许假装入队：先在本地拦下，说清楚是网关而不是「先跑大纲」
    if (!gate.ok) {
      toast(gate.why);
      return;
    }
    void runJob("script", ep);
  };

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
      eyebrow="本 · 怎么说 · SCRIPT"
      title={`剧本 / 第 ${ep} 集`}
      lede="剧本是上游，不是产物：hibtv 不重写台词，只把每一拍拆成可拍的秒数，并把表演提示留给演员与镜头。"
      right={
        <>
          <CodexLight />
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
          <button className="btn gold" onClick={runScript} disabled={!gate.ok} title={gate.why}>
            {episode ? "重新生成本集剧本" : "生成本集剧本"}
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
          <button className="btn gold" onClick={runScript} disabled={!gate.ok} title={gate.why}>
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
        const stg = stagingByScene.get(sc.sceneId) || stagingByScene.get(`#${si + 1}`);
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
            <StagingBar
              scene={stg}
              fallbackCast={(sc.characters || []).length}
              hasReport={hasStagingReport}
              onOpen={openStagingReport}
              onRestage={restage}
            />
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
