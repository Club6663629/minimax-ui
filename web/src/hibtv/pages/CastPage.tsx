/** 角色：每人一张卡（锚点图 + 派生多视图 + 提示词 / 音色 / 分层），出图与重出都在卡上。
 *
 * 文本来自 codex 的 `novel-characters`（cast.json），像素由平台按 `image.prompt`
 * 先出锚点（896×1152 竖版），确认锚点后再派生大头/侧面/背面（只供审阅与出分镜图参考）。
 */
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { hibtvApi, maskHost } from "../api";
import { Stage } from "../HibtvLayout";
import { AssetCard, useAssets } from "../components/AssetCard";
import { CodexLight, useCodexGate } from "../components/CodexLight";
import { ArtifactsDrawer } from "../components/StageArtifacts";
import { Empty, GateChips, GateList, GenBar, Loading, PageHead } from "../components/ui";
import { useHibtv, useJob, usePatch } from "../state";
import type { CastCharacter } from "../types";

export default function CastPage() {
  const navigate = useNavigate();
  const { project, status, runJob, toast, openLayer, closeLayer } = useHibtv();
  const { data, doc, loading, reload: reloadDoc } = usePatch("cast");
  const job = useJob("cast");
  const { assets, loading: assetsLoading, reload } = useAssets("cast");
  const gate = useCodexGate();

  const characters = useMemo<CastCharacter[]>(
    () => (data?.characters || []) as CastCharacter[],
    [data],
  );

  const groups = useMemo(() => {
    const order = ["protagonist", "supporting", "minor", "other"];
    const label: Record<string, string> = {
      protagonist: "主演",
      supporting: "配角",
      minor: "功能角色",
      other: "其他",
    };
    // cast 文档的 importance 实际取值是 protagonist / major（novel-characters schema）
    const bucket = (imp?: string): string => {
      if (imp === "protagonist") return "protagonist";
      if (imp === "minor") return "minor";
      if (imp === "supporting" || imp === "major") return "supporting";
      return imp ? "other" : "supporting";
    };
    return order
      .map((k) => ({
        key: k,
        label: label[k],
        rows: assets.filter((a) => bucket(a.data?.importance) === k),
      }))
      .filter((g) => g.rows.length);
  }, [assets]);

  const missing = useMemo(() => assets.filter((a) => !a.main?.path).length, [assets]);

  const drawAll = async () => {
    if (!project) return;
    const pending = assets.filter((a) => !a.main?.path);
    if (!pending.length) {
      toast("全部角色都已有锚点图；要换某人的图，点他卡片上的「重出这一张」，确认后再出派生视图");
      return;
    }
    try {
      // 一次作业出全部缺锚点（派生视图需在卡片上确认锚点后逐张出）
      await hibtvApi.createJob(project.id, { kind: "images", payload: { mode: "assets", kind: "cast" } });
      toast(`已排入出图队列：${pending.length} 张角色锚点图（896×1152）`);
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
    void runJob("cast");
  };

  const head = (
    <PageHead
      eyebrow="收敛层 · 谁 · CAST"
      title={<>角色 <span className="chip" style={{ verticalAlign: "middle" }}>{assets.length} 人</span></>}
      right={
        <>
          <CodexLight />
          <GateChips gates={doc?.gates} none="角色表没有结构化门结果（skill 的门只写在产物里）" />
          <button className="btn ghost" onClick={() => void drawAll()} disabled={!missing}>
            {missing ? `出锚点（缺 ${missing} 人）` : "锚点已齐"}
          </button>
          <button className="btn gold" onClick={rerun} disabled={!gate.ok} title={gate.why}>
            {characters.length ? "重新生成角色表" : "基于大纲生成全部"}
          </button>
        </>
      }
    />
  );

  const toolbar = (
    <div className="toolbar">
      <span className="lab">出图设置 · 本剧题目</span>
      <span className="chip">出图节点 {maskHost(status?.image?.worker) || "qwen21"}</span>
      <span className="chip">角色图 {(status?.assetSizes?.cast || [896, 1152]).join("×")}</span>
      <span className="chip">提示词语言 {status?.promptLang === "zh" ? "中文" : "英文"}</span>
      <span className="chip">视频画幅 {project?.aspectRatio || "9:16"}</span>
      <span className="sp" />
      <button className="btn sm ghost" onClick={() => openLayer(<ArtifactsDrawer projectId={project?.id || 0} jobId={job?.id || 0} onClose={closeLayer} />)}>
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

  const notReady = !characters.length && !assetsLoading && assets.length === 0;

  return (
    <Stage>
      {head}
      {toolbar}
      <GenBar job={job} onCancel={job ? () => void hibtvApi.cancelRemoteJob(project.id, job.id) : undefined} />
      <GateList gates={doc?.gates} />

      {loading && !data ? <Loading /> : null}

      {notReady ? (
        <div style={{ maxWidth: 720, margin: "34px auto" }}>
          <Empty
            glyph="生"
            title="当前还没有角色数据"
            sub={
              job?.status === "failed"
                ? `角色表生成失败：${job.error}`
                : "点一次会由 codex 跑 novel-characters：出角色表 + 形象提示词 + 音色建议；已存在的角色数据不会被覆盖。"
            }
          >
            <button className="btn gold" onClick={rerun} disabled={!gate.ok}>
              基于大纲生成全部
            </button>
            {!gate.ok && <div className="tiny red" style={{ marginTop: 8 }}>{gate.why}</div>}
          </Empty>
        </div>
      ) : (
        <>
          {groups.map((g) => (
            <div className="sec" key={g.key}>
              <div className="inline" style={{ marginBottom: 4 }}>
                <span className="h2" style={{ margin: 0 }}>
                  {g.label}
                </span>
                <span className="chip">{g.rows.length} 人</span>
              </div>
              <div className="cast-grid">
                {g.rows.map((a) => (
                  <AssetCard key={a.id} asset={a} onChanged={() => void reload().then(() => reloadDoc())} />
                ))}
              </div>
            </div>
          ))}
          <div className="tiny dim" style={{ marginTop: 16, lineHeight: 1.9 }}>
            一角色一张图：提示词直接用 skill 的 <span className="mono">image.prompt</span>
            （反向词 <span className="mono">negativePrompt</span>），改提示词后点「重出这一张」。
            <br />
            出图按张扣积分（{status?.cost?.image ?? 0} 积分 / 张），系统失败全额退。
          </div>
        </>
      )}
    </Stage>
  );
}
