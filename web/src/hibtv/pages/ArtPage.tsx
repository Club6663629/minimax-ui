/** 美术设定：场景卡（1280×720 空景）+ 道具卡（1024×1024 白底单件）。
 *
 * 文本来自 codex 的 `novel-art`（art.json），像素由平台按 `image.prompt` 一次一张；
 * `anchors` 只做人工核对清单，`states[]` 只作文本说明（本期每个道具仍只 1 张主图）。
 */
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { hibtvApi, maskHost } from "../api";
import { Stage } from "../HibtvLayout";
import { AssetCard, useAssets } from "../components/AssetCard";
import { CodexLight, useCodexGate } from "../components/CodexLight";
import { ArtifactsDrawer } from "../components/StageArtifacts";
import { Empty, GateChips, GateList, GenBar, Loading, PageHead } from "../components/ui";
import { useHibtv, useJob, usePatch } from "../state";
import type { ArtData } from "../types";

type Tab = "all" | "scene" | "prop";

export default function ArtPage() {
  const navigate = useNavigate();
  const { project, status, runJob, toast, openLayer, closeLayer } = useHibtv();
  const { data, doc, loading, reload: reloadDoc } = usePatch("art");
  const job = useJob("art");
  const { assets, loading: assetsLoading, reload } = useAssets();
  const gate = useCodexGate();
  const [tab, setTab] = useState<Tab>("all");

  const art = (data || {}) as ArtData;
  const scenes = useMemo(() => assets.filter((a) => a.kind === "scene"), [assets]);
  const props = useMemo(() => assets.filter((a) => a.kind === "prop"), [assets]);
  const shown = tab === "scene" ? scenes : tab === "prop" ? props : [...scenes, ...props];
  // 一致性锚点：纯文本，供人核对与改图（不做自动校验）
  // 必须放在 `if (!project)` 之前：首帧 project 还没到时早退，钩子数量会变 → React #310
  const anchors = useMemo(
    () =>
      (art.scenes || [])
        .filter((s) => (s.anchors || []).length)
        .map((s) => ({ id: s.id, name: s.name, anchors: s.anchors || [] })),
    [art.scenes],
  );

  const drawAll = async () => {
    if (!project) return;
    const pending = shown.filter((a) => !a.main?.path);
    if (!pending.length) {
      toast("这一栏的图都出过了；要换某一张，点卡片上的「重出这一张」");
      return;
    }
    try {
      await hibtvApi.createJob(project.id, {
        kind: "images",
        payload: { mode: "assets", kind: tab === "all" ? "" : tab },
      });
      toast(`已排入出图队列：${pending.length} 张（场景 1280×720 / 道具 1024×1024）`);
      reload();
    } catch {
      toast("排队失败，请稍后重试");
    }
  };

  const rerun = () => {
    if (!gate.ok) {
      toast(gate.why);
      return;
    }
    void runJob("art");
  };

  const head = (
    <PageHead
      eyebrow="收敛层 · 在哪 + 拿什么 · ART"
      title={
        <>
          美术设定{" "}
          <span className="chip" style={{ verticalAlign: "middle" }}>
            {scenes.length} 场景 · {props.length} 道具
          </span>
        </>
      }
      right={
        <>
          <CodexLight />
          <GateChips gates={doc?.gates} none="美术设定没有结构化门结果（skill 的门只写在产物里）" />
          <button className="btn" onClick={() => void drawAll()}>
            出全部单图
          </button>
          <button className="btn gold" onClick={rerun} disabled={!gate.ok} title={gate.why}>
            {art.scenes?.length || art.props?.length ? "重新生成美术" : "生成场景与道具"}
          </button>
        </>
      }
    />
  );

  const toolbar = (
    <div className="toolbar">
      <span className="lab">出图设置 · 本剧题目</span>
      <span className="chip">出图节点 {maskHost(status?.image?.worker) || "qwen21"}</span>
      <span className="chip">场景 {(status?.assetSizes?.scene || [1280, 720]).join("×")}</span>
      <span className="chip">道具 {(status?.assetSizes?.prop || [1024, 1024]).join("×")}</span>
      <span className="chip">视频画幅 {project?.aspectRatio || "9:16"}</span>
      <span className="sp" />
      <button
        className="btn sm ghost"
        onClick={() =>
          openLayer(<ArtifactsDrawer projectId={project?.id || 0} jobId={job?.id || 0} onClose={closeLayer} />)
        }
      >
        产物 / 评审报告
      </button>
      <span className="chip i" onClick={() => navigate("/hibtv/settings")}>
        项目设置 →
      </span>
    </div>
  );

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

  const notReady = !assetsLoading && assets.length === 0 && !art.scenes?.length;

  return (
    <Stage>
      {head}
      {toolbar}
      <div className="inline" style={{ marginBottom: 10 }}>
        <span className="lab tiny" style={{ letterSpacing: ".12em", color: "#7d766b" }}>
          画幅
        </span>
        <span className="chip ghost">{project.aspectRatio}</span>
        <span className="tiny dim">场景出空景、道具出白底单件；形制与纹样按美术设定原文写</span>
      </div>

      <div className="tabs">
        {(
          [
            ["all", "全部", scenes.length + props.length],
            ["scene", "场景", scenes.length],
            ["prop", "道具", props.length],
          ] as [Tab, string, number][]
        ).map(([k, label, n]) => (
          <span key={k} className={`tab ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>
            {label} <span className="n">{n}</span>
          </span>
        ))}
      </div>

      <GenBar job={job} onCancel={job ? () => void hibtvApi.cancelRemoteJob(project.id, job.id) : undefined} />
      <GateList gates={doc?.gates} />
      {loading && !data ? <Loading /> : null}

      {notReady ? (
        <div style={{ maxWidth: 720, margin: "34px auto" }}>
          <Empty
            glyph="美"
            title="当前还没有美术条目"
            sub={
              job?.status === "failed"
                ? `美术设定生成失败：${job.error}`
                : "由 codex 跑 novel-art：出场景（含一致性锚点）与道具（含形制说明），并给每个条目一段出图提示词。"
            }
          >
            <button className="btn gold" onClick={rerun} disabled={!gate.ok}>
              生成场景与道具
            </button>
            {!gate.ok && <div className="tiny red" style={{ marginTop: 8 }}>{gate.why}</div>}
          </Empty>
        </div>
      ) : shown.length ? (
        <div className="art-grid">
          {shown.map((a) => (
            <AssetCard key={a.id} asset={a} onChanged={() => void reload().then(() => reloadDoc())} />
          ))}
        </div>
      ) : (
        <div className="pnew" style={{ marginTop: 14 }}>
          <span className="th">＋</span>
          <span>
            <span className="nm">这一栏没有条目</span>
            <span className="meta">从大纲重新生成美术设定，或换一个筛选</span>
          </span>
        </div>
      )}

      {anchors.length ? (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-hd">
            <span className="t">一致性锚点（人工核对清单）</span>
            <span className="chip">{anchors.length} 个场景</span>
          </div>
          <div className="card-bd tiny">
            {anchors.map((row) => (
              <div key={row.id} style={{ padding: "3px 0" }}>
                <span className="mono dim">{row.id}</span> {row.name}：
                {row.anchors.map((a) => (
                  <span key={a} className="chip ghost" style={{ marginLeft: 4 }}>
                    {a}
                  </span>
                ))}
              </div>
            ))}
            <div className="dim" style={{ marginTop: 6 }}>
              锚点是跨集一致性的文本口径：出图没体现就改提示词重出这一张。
            </div>
          </div>
        </div>
      ) : null}
    </Stage>
  );
}
