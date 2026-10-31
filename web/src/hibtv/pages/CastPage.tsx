/** 角色：每人一张卡（锚点图 + 派生多视图 + 提示词 / 音色 / 分层），出图与重出都在卡上。
 *
 * 文本来自 codex 的 `novel-characters`（cast.json），像素由平台按 `image.prompt`
 * 先出锚点（832×1248 竖版），确认锚点后再派生大头/侧面/背面（只供审阅与出分镜图参考）。
 */
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { hibtvApi } from "../api";
import { Stage } from "../HibtvLayout";
import { AddAssetButton, AssetCard, useAssets } from "../components/AssetCard";
import { useCodexGate } from "../components/CodexLight";
import { ArtifactsDrawer } from "../components/StageArtifacts";
import { Empty, GateList, GenBar, Loading, PageHead } from "../components/ui";
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
      toast(`已排入出图队列：${pending.length} 张角色锚点图（832×1248）`);
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
      lede="每个人一张卡：左边是正面全身锚点，右边是大头、侧面、背面。鼠标移到图片上可重新生成、改图、上传和删除；点击图片查看四视图。"
      title="角色"
      right={
        <>
          <AddAssetButton kind="cast" onChanged={()=>void reload().then(()=>reloadDoc())}/>
          <button className="btn gold" onClick={() => void drawAll()} disabled={!missing}>出全部锚点图</button>
          <span className="chip">{assets.length} 人</span>
        </>
      }
    />
  );

  const toolbar = (
    <div className="toolbar">
      <span className="lab">出图设置 · {project?.name || "当前项目"}</span>
      <span className="chip">锚点模型 Qwen-Image</span>
      <span className="chip">派生模型 同锚点模型</span>
      <span className="chip">锚点出完先确认</span>
      <span className="chip">角色图 {(status?.assetSizes?.cast || [832, 1248]).join("×")}</span>
      <span className="chip">提示词语言 {status?.promptLang === "zh" ? "中文" : "英文"}</span>
      <span className="chip">视频画幅 {project?.aspectRatio || "9:16"}</span>
      <span className="sp" />
      <button className="btn sm ghost" onClick={rerun} disabled={!gate.ok}>{characters.length?"重新解析角色卡":"基于大纲生成全部"}</button>
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
        <Empty glyph="剧" title="还没有项目" sub="先在首页上传定稿剧本与美术设定。">
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
              <div className="cast-grid">
                {assets.map((a) => (
                  <AssetCard key={a.id} asset={a} onChanged={() => void reload().then(() => reloadDoc())} />
                ))}
              </div>
          <div className="tiny dim" style={{ marginTop: 16, lineHeight: 1.9 }}>
            确认正面全身锚点后，可生成大头、侧面、背面。修改提示词或重生成锚点后，需要重新确认并更新派生视图。
            <br />
            出图按张扣积分（{status?.cost?.image ?? 0} 积分 / 张），系统失败全额退。
          </div>
        </>
      )}
    </Stage>
  );
}
