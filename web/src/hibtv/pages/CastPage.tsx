/** 角色：每人一张卡（锚点大图 + 三视图 + 提示词 / 音色），出图与版本都在卡上。 */
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { hibtvApi, maskHost } from "../api";
import { Stage } from "../HibtvLayout";
import { AssetCard, useAssets } from "../components/AssetCard";
import { Empty, GateChips, GateList, GenBar, Loading, PageHead } from "../components/ui";
import { useHibtv, useJob, usePatch } from "../state";

export default function CastPage() {
  const navigate = useNavigate();
  const { project, status, runJob, toast } = useHibtv();
  const { data, doc, loading } = usePatch("cast");
  const job = useJob("cast");
  const { assets, loading: assetsLoading, reload } = useAssets("cast");

  const characters = useMemo(
    () => ((data?.characters || []) as { id: string; importance?: string }[]) || [],
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
    // cast 文档的 importance 实际取值是 protagonist / major（novel-characters schema）；
    // 以前只列 protagonist/supporting/minor，major 会被整个丢掉（页头写 4 人、页面只有 2 张卡）。
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

  const missingDerived = useMemo(
    () => assets.reduce((n, a) => {
      if (!a.views[a.anchor]?.path) return n;
      return n + a.defs.slice(1).filter(([v]) => !a.views[v]?.path || a.views[v]?.stale).length;
    }, 0),
    [assets],
  );

  const drawAll = async () => {
    if (!project) return;
    const pending = assets.filter((a) => !a.views[a.anchor]?.path);
    if (!pending.length) {
      toast("全部角色都已有锚点图");
      return;
    }
    for (const asset of pending) {
      try {
        await hibtvApi.assetImage(project.id, asset.id, { action: "anchor" });
      } catch {
        /* 单张失败不影响其余 */
      }
    }
    toast(`已排入出图队列：${pending.length} 张锚点图`);
    reload();
  };

  const drawDerivedAll = async () => {
    if (!project) return;
    const pending = assets.filter(
      (a) => a.views[a.anchor]?.path
        && a.defs.slice(1).some(([v]) => !a.views[v]?.path || a.views[v]?.stale),
    );
    if (!pending.length) {
      toast("所有派生视图都已就绪");
      return;
    }
    for (const asset of pending) {
      try {
        await hibtvApi.assetImage(project.id, asset.id, {
          action: "views",
          views: asset.defs.slice(1).map(([v]) => v),
        });
      } catch {
        /* 单张失败不影响其余 */
      }
    }
    toast(`已排入派生队列：${pending.length} 人，共 ${missingDerived} 张图，按张扣积分`);
    reload();
  };

  const head = (
    <PageHead
      eyebrow="收敛层 · 谁 · CAST"
      title={<>角色 <span className="chip" style={{ verticalAlign: "middle" }}>{assets.length} 人</span></>}
      lede="每个人一张卡：左边是正面全身锚点，右边是大头、侧面、背面。鼠标移到图上浮现四个图标——重新生成 / 改图 / 上传 / 删除；点图片直接看四视图大图。"
      right={
        <>
          <GateChips gates={doc?.gates} none="角色卡走 skill 的 cast schema，但没有接线 node 确定性校验；逐项检修看下方提示词与对齐检查" />
          <button className="btn" onClick={() => void drawAll()}>
            出全部锚点图
          </button>
          <button className="btn ghost" onClick={() => void drawDerivedAll()} disabled={!missingDerived}>
            补齐派生{missingDerived ? `（${missingDerived} 张）` : " · 已齐"}
          </button>
          <button className="btn gold" onClick={() => void runJob("cast")}>
            {characters.length ? "重新生成角色卡" : "基于大纲生成全部"}
          </button>
        </>
      }
    />
  );

  const toolbar = (
    <div className="toolbar">
      <span className="lab">出图设置 · 本剧题目</span>
      <span className="chip">锚点模型 {maskHost(status?.image?.worker) || "qwen21"}</span>
      <span className="chip">派生模型 同锚点模型</span>
      <span className={`chip ${status?.anchorConfirm ? "gold" : "ghost"}`}>
        锚点出完先确认{status?.anchorConfirm ? " · 开" : " · 关"}
      </span>
      <span className="chip">画幅 {project?.aspectRatio || "9:16"}</span>
      <span className="sp" />
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

  const notReady = !doc?.data?.characters?.length && !assetsLoading && assets.length === 0;

  return (
    <Stage>
      {head}
      {toolbar}
      <GenBar job={job} onCancel={job ? () => void hibtvApi.cancelJob(project.id, job.id) : undefined} />
      <GateList gates={doc?.gates} />

      {loading && !data ? <Loading /> : null}

      {notReady ? (
        <div style={{ maxWidth: 720, margin: "34px auto" }}>
          <Empty
            glyph="生"
            title="当前还没有角色数据"
            sub={
              job?.status === "failed"
                ? `角色卡生成失败：${job.error}`
                : "点一次会基于现有大纲同时生成角色卡、角色提示词与音色建议；已经存在的角色数据不会被覆盖。"
            }
          >
            <div className="toolbar" style={{ justifyContent: "center", margin: "0 0 12px" }}>
              <span className="lab">本次生成</span>
              <span className="chip">画面 {project.aspectRatio}</span>
              <span className="chip">模型 {status?.llm?.model || "deepseek"}</span>
            </div>
            <button className="btn gold" onClick={() => void runJob("cast")}>
              基于大纲生成全部
            </button>
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
                  <AssetCard key={a.id} asset={a} onChanged={() => void reload()} />
                ))}
              </div>
            </div>
          ))}
          <div className="tiny dim" style={{ marginTop: 16, lineHeight: 1.9 }}>
            提示：锚点图是「正面全身」，其余视图都照着它派生；换掉锚点后派生图标会打斜纹，点「接受 v2」即恢复。
            <br />
            出图按张扣积分（{status?.cost?.image ?? 0} 积分 / 张），系统失败全额退。
          </div>
        </>
      )}
    </Stage>
  );
}
