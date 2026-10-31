/** 站位（新页）：每场一张卡 —— 场景平面图（按 plan 比例画框）+ 人物点位 / 朝向 / 群演。
 *
 * 文本来自 codex 的 `novel-staging`（staging.json），它是 storyboard `blocking` 的上游。
 * `staging-report.html` 是可拖动版工具：拖动结果**不回写**（要固化就 PATCH docs/staging 重跑分镜）。
 */
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { hibtvApi } from "../api";
import { Stage } from "../HibtvLayout";
import { CodexLight, useCodexGate } from "../components/CodexLight";
import { ArtifactsDrawer, ReportFrame } from "../components/StageArtifacts";
import { Empty, GateChips, GateList, GenBar, Loading, PageHead } from "../components/ui";
import { useHibtv, useJob, usePatch } from "../state";
import type { StagingData, StagingScene } from "../types";

const SIDE_LABEL: Record<string, string> = {
  left: "左",
  right: "右",
  center: "中",
  front: "前",
  back: "后",
};

const FACING_ARROW: Record<string, string> = {
  left: "←",
  right: "→",
  front: "↓",
  back: "↑",
  camera: "◎",
};

function Plan({
  scene,
  data,
}: {
  scene: StagingScene;
  data: StagingData;
}) {
  const location = (data.locations || []).find(
    (l) => (scene.sceneId && l.name && scene.sceneId === l.id) || l.id === scene.sceneId,
  ) || (data.locations || [])[0];
  const items = (location?.items || []).filter((i) => i.on !== false && (i.plan || []).length === 4);
  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        aspectRatio: "16 / 9",
        border: "1px solid var(--line-3)",
        borderRadius: 6,
        background:
          "repeating-linear-gradient(0deg, rgba(255,255,255,.03) 0 1px, transparent 1px 100%), #141210",
        overflow: "hidden",
      }}
    >
      {items.map((item) => {
        const [x, y, w, h] = item.plan as number[];
        return (
          <div
            key={item.name}
            title={`${item.name}${item.alt ? ` · ${item.alt}` : ""}`}
            style={{
              position: "absolute",
              left: `${x * 100}%`,
              top: `${y * 100}%`,
              width: `${w * 100}%`,
              height: `${h * 100}%`,
              border: "1px dashed rgba(224,179,65,.45)",
              borderRadius: 4,
              fontSize: 10,
              color: "#a8987a",
              padding: 2,
              overflow: "hidden",
            }}
          >
            {item.name}
          </div>
        );
      })}
      {/* 人物点位：按 side/along 摆到平面图上（plan 只描述物品，人物只有相对位置描述） */}
      <div style={{ position: "absolute", inset: 0, display: "flex", flexWrap: "wrap", alignContent: "flex-end", gap: 6, padding: 8 }}>
        {(scene.places || [])
          .filter((p) => p.present !== false)
          .map((p) => (
            <span
              key={p.id}
              className="chip"
              title={[p.item, p.posture, p.facing].filter(Boolean).join(" · ")}
              style={{ background: "rgba(224,179,65,.14)", borderColor: "rgba(224,179,65,.35)" }}
            >
              {p.id}
              {p.side ? ` ${SIDE_LABEL[p.side] || p.side}` : ""}
              {p.facing ? ` ${FACING_ARROW[p.facing] || p.facing}` : ""}
            </span>
          ))}
      </div>
      {!items.length && (
        <div className="tiny dim" style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}>
          这一场没有平面图物品（locations.items 为空）
        </div>
      )}
    </div>
  );
}

export default function StagingPage() {
  const navigate = useNavigate();
  const { project, runJob, toast, openLayer, closeLayer } = useHibtv();
  const gate = useCodexGate();
  const [ep, setEp] = useState(1);
  const { data, doc, loading, reload } = usePatch("staging", ep);
  const job = useJob("staging", ep);

  const staging = (data || {}) as StagingData;
  const scenes = useMemo(
    () => (staging.scenes || []).filter((s) => Number(s.ep || ep) === ep),
    [staging.scenes, ep],
  );

  const image = doc?.meta?.report || "";

  const rerun = () => {
    if (!gate.ok) {
      toast(gate.why);
      return;
    }
    void runJob("staging", ep);
  };

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
    <Stage
      side={
        <>
          <div className="side-hd">
            <span className="t">集</span>
          </div>
          <div className="ep-list">
            {Array.from({ length: Math.max(1, project.episodes || 1) }, (_, i) => i + 1).map((n) => (
              <div key={n} className={`ep-item ${n === ep ? "on" : ""}`} onClick={() => setEp(n)}>
                <span className="no">E{String(n).padStart(2, "0")}</span>
                <span className="meta">站位</span>
              </div>
            ))}
          </div>
        </>
      }
    >
      <PageHead
        eyebrow="收敛层 · 站哪 · STAGING"
        title={
          <>
            第 {ep} 集站位{" "}
            <span className="chip" style={{ verticalAlign: "middle" }}>
              {scenes.length} 场
            </span>
          </>
        }
        lede="站位是分镜 blocking 的上游：点位 / 朝向 / 群演定下来，分镜才拍得顺。"
        right={
          <>
            <CodexLight />
            <GateChips gates={doc?.gates} none="站位没有结构化门结果" />
            <button
              className="btn ghost"
              disabled={!doc}
              onClick={() =>
                openLayer(
                  <ArtifactsDrawer projectId={project.id} jobId={job?.id || 0} onClose={closeLayer} />,
                )
              }
            >
              产物
            </button>
            <button className="btn gold" onClick={rerun} disabled={!gate.ok} title={gate.why}>
              {scenes.length ? "重新生成站位" : "生成站位"}
            </button>
          </>
        }
      />

      <GenBar job={job} onCancel={job ? () => void hibtvApi.cancelRemoteJob(project.id, job.id) : undefined} />
      <GateList gates={doc?.gates} />
      {loading && !data ? <Loading /> : null}

      {!scenes.length && !loading ? (
        <div style={{ maxWidth: 720, margin: "34px auto" }}>
          <Empty
            glyph="位"
            title="这一集还没有站位"
            sub={
              job?.status === "failed"
                ? `站位生成失败：${job.error}`
                : "由 codex 跑 novel-staging：先定平面图物品，再给每场排人物点位、朝向与群演。"
            }
          >
            <button className="btn gold" onClick={rerun} disabled={!gate.ok}>
              生成站位
            </button>
            {!gate.ok && <div className="tiny red" style={{ marginTop: 8 }}>{gate.why}</div>}
          </Empty>
        </div>
      ) : (
        <div className="sec">
          {scenes.map((scene, i) => (
            <div className="card" key={`${scene.sceneId}-${i}`} style={{ marginBottom: 12 }}>
              <div className="card-hd">
                <span className="t">
                  {scene.sceneId || `场景 ${scene.scene || i + 1}`}
                </span>
                {scene.lighting && <span className="chip">{scene.lighting}</span>}
                <span className="chip">{scene.cast?.length || 0} 人</span>
                {Boolean(scene.extras) && (
                  <span className="chip ghost">群演 {scene.extras}{scene.extrasWhere ? ` · ${scene.extrasWhere}` : ""}</span>
                )}
              </div>
              <div className="card-bd" style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 12 }}>
                <Plan scene={scene} data={staging} />
                <div className="tiny">
                  {(scene.places || []).map((p) => (
                    <div key={p.id} style={{ padding: "3px 0" }}>
                      <span className="mono">{p.id}</span>
                      {p.item ? <span className="dim"> @ {p.item}</span> : null}
                      {p.side ? <span> · {SIDE_LABEL[p.side] || p.side}</span> : null}
                      {p.along ? <span> · {p.along}</span> : null}
                      {p.posture ? <span> · {p.posture}</span> : null}
                      {p.facing ? <span> · 面向 {FACING_ARROW[p.facing] || p.facing}</span> : null}
                      {p.present === false ? <span className="dim"> · 不在场</span> : null}
                    </div>
                  ))}
                  {scene.why ? <div className="dim" style={{ marginTop: 6 }}>{scene.why}</div> : null}
                </div>
              </div>
            </div>
          ))}

          {image && doc?.meta?.codex_job_id ? (
            <div className="card">
              <div className="card-hd">
                <span className="t">可拖动版站位工具</span>
                <span className="chip ghost">拖动结果不回写 · 要固化请改 docs/staging 后重跑分镜</span>
              </div>
              <div className="card-bd">
                <ReportFrame projectId={project.id} jobId={String(doc?.meta?.codex_job_id || "")} rel={image} />
              </div>
            </div>
          ) : null}

          <div className="tiny dim" style={{ marginTop: 8, lineHeight: 1.9 }}>
            改站位走就地编辑（PATCH docs/staging），改完这一集的分镜要重跑：分镜的 blocking 读的就是这里。
            <br />
            <span className="link" onClick={() => void reload()}>强制刷新</span>
          </div>
        </div>
      )}
    </Stage>
  );
}
