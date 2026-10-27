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

  // 设定稿是唯一产物：没有设定稿也没有旧锚点图，才算“缺”
  const missing = useMemo(
    () => assets.filter((a) => !a.sheet?.path && !a.views[a.anchor]?.path).length,
    [assets],
  );

  const drawAll = async () => {
    if (!project) return;
    const pending = assets.filter((a) => !a.sheet?.path && !a.views[a.anchor]?.path);
    if (!pending.length) {
      toast("全部角色都已有四视图设定稿；要换某一人的版式，点他卡片上的「重出四视图」");
      return;
    }
    for (const asset of pending) {
      try {
        await hibtvApi.assetImage(project.id, asset.id, { action: "sheet" });
      } catch {
        /* 单张失败不影响其余 */
      }
    }
    toast(`已排入出图队列：${pending.length} 张四视图设定稿`);
    reload();
  };

  const head = (
    <PageHead
      eyebrow="收敛层 · 谁 · CAST"
      title={<>角色 <span className="chip" style={{ verticalAlign: "middle" }}>{assets.length} 人</span></>}
      lede="每个人一张 16:9 四视图设定稿：①头肩特写 ②正面全身 ③3/4 侧全身（约 45°，双眼可见）④背面全身，一次出图、格子自动裁好。卡片上直接显示整张（不再分格陈列），点图看大图；鼠标移到图上浮现四个图标——重出设定稿 / 改图 / 上传整张 / 删除。改这张即同步全部视角。"
      right={
        <>
          <GateChips gates={doc?.gates} none="角色卡走 skill 的 cast schema，但没有接线 node 确定性校验；逐项检修看下方提示词与对齐检查" />
          <button className="btn ghost" onClick={() => void drawAll()} disabled={!missing}>
            {missing ? `出四视图（缺 ${missing} 人）` : "四视图已齐"}
          </button>
          <button className="btn gold" onClick={() => void runJob("cast")}>
            {characters.length ? "重新生成角色卡" : "基于大纲生成全部"}
          </button>
        </>
      }
    />
  );

  // 设定稿固定 16:9（与视频横竖版无关），画幅由后端给（默认 2752×1536 = Qwen-Image-2.1 2K）
  const sheetSize = (assets[0]?.sheetSpec?.size || []).join("×") || "2752×1536";

  const toolbar = (
    <div className="toolbar">
      <span className="lab">出图设置 · 本剧题目</span>
      <span className="chip">锚点模型 {maskHost(status?.image?.worker) || "qwen21"}</span>
      <span className="chip">派生模型 同锚点模型</span>
      <span className={`chip ${status?.anchorConfirm ? "gold" : "ghost"}`}>
        锚点出完先确认{status?.anchorConfirm ? " · 开" : " · 关"}
      </span>
      <span className="chip">设定稿 16:9 · {sheetSize}</span>
      <span className="chip">视频画幅 {project?.aspectRatio || "9:16"}</span>
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
