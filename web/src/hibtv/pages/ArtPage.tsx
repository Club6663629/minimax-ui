/** 美术设定：场景 / 道具卡（锚点大图 + 派生视图 + 提示词），三档筛选。 */
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { hibtvApi } from "../api";
import { Stage } from "../HibtvLayout";
import { AssetCard, useAssets } from "../components/AssetCard";
import { Empty, GateChips, GateList, GenBar, Loading, PageHead } from "../components/ui";
import { useHibtv, useJob, usePatch } from "../state";

type Tab = "all" | "scene" | "prop";

export default function ArtPage() {
  const navigate = useNavigate();
  const { project, status, runJob, toast } = useHibtv();
  const { data, doc, loading } = usePatch("art");
  const job = useJob("art");
  const { assets, loading: assetsLoading, reload } = useAssets();
  const [tab, setTab] = useState<Tab>("all");

  const scenes = useMemo(() => assets.filter((a) => a.kind === "scene"), [assets]);
  const props = useMemo(() => assets.filter((a) => a.kind === "prop"), [assets]);
  const shown = tab === "scene" ? scenes : tab === "prop" ? props : [...scenes, ...props];

  const drawAll = async () => {
    if (!project) return;
    const pending = shown.filter((a) => !a.views[a.anchor]?.path);
    if (!pending.length) {
      toast("这一栏的锚点图都已出过");
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
      lede="每个场景一组参考图：正面是锚点，俯视、背面、左侧、右侧都照着它改。鼠标移到图上浮现四个图标——重新生成 / 改图 / 上传 / 删除；点图片直接看多视图大图。"
      right={
        <>
          <GateChips gates={doc?.gates} />
          <button className="btn" onClick={() => void drawAll()}>
            出全部锚点图
          </button>
          <button className="btn gold" onClick={() => void runJob("art")}>
            {data?.scenes?.length || data?.props?.length ? "重新生成美术" : "生成场景与道具"}
          </button>
        </>
      }
    />
  );

  const toolbar = (
    <div className="toolbar">
      <span className="lab">出图设置 · 本剧题目</span>
      <span className="chip">锚点模型 {status?.image?.worker || "qwen21"}</span>
      <span className="chip">派生模型 同锚点模型</span>
      <span className="chip">画幅 {project?.aspectRatio || "9:16"}</span>
      <span
        className="chip i"
        onClick={() => toast(`当前 ${project?.aspectRatio || "9:16"}；改比例请点顶栏的 9:16 / 16:9`)}
      >
        比例切换在顶栏
      </span>
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

  const notReady = !assetsLoading && assets.length === 0 && !doc?.data?.scenes?.length;

  return (
    <Stage>
      {head}
      {toolbar}
      <div className="inline" style={{ marginBottom: 10 }}>
        <span className="lab tiny" style={{ letterSpacing: ".12em", color: "#7d766b" }}>
          画幅
        </span>
        <span className="chip ghost">{project.aspectRatio}</span>
        <span className="tiny dim">场景、道具的形制与纹样都按美术设定原文写</span>
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

      <GenBar job={job} onCancel={job ? () => void hibtvApi.cancelJob(project.id, job.id) : undefined} />
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
                : "从上传的美术设定里解析出场景与道具条目，保留原文案，并补齐出图提示词。缺项不会臆造，交给「对齐检查」提示。"
            }
          >
            <button className="btn gold" onClick={() => void runJob("art")}>
              生成场景与道具
            </button>
          </Empty>
        </div>
      ) : shown.length ? (
        <div className="art-grid">
          {shown.map((a) => (
            <AssetCard key={a.id} asset={a} onChanged={() => void reload()} />
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

      <div className="tiny dim" style={{ marginTop: 12, lineHeight: 1.9 }}>
        提示：场景与道具的锚点图同样是「正面」；俯视 / 背面 / 左侧 / 右侧 由锚点派生，过期会打斜纹。
      </div>
    </Stage>
  );
}
