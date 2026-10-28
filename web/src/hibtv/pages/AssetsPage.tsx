/**
 * 素材库：本项目已生成 / 已上传的图与片（按项目隔离）。
 *
 * 一资产一张主图之后，这里**没有「视图」维度**：角色 / 场景 / 道具各一张，关键帧一段若干张，
 * 段成片按版本列，整集成片按集列。换素材回对应的资产卡或段页改，这里只做总览与下载。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { hibtvApi, mediaUrl } from "../api";
import { Stage } from "../HibtvLayout";
import { Empty, Loading, PageHead, Plate, VideoBox } from "../components/ui";
import { errText, useHibtv } from "../state";
import type { LibraryItem } from "../types";

type Tab = "asset" | "frame" | "segment" | "render";

const TABS: { k: Tab; label: string }[] = [
  { k: "asset", label: "资产主图" },
  { k: "frame", label: "关键帧" },
  { k: "segment", label: "段成片" },
  { k: "render", label: "整集成片" },
];

const ASSET_KINDS = ["cast", "scene", "prop"] as const;
const KIND_LABEL: Record<string, string> = {
  cast: "角色 · 896×1152",
  scene: "场景 · 1280×720",
  prop: "道具 · 1024×1024",
};

export default function AssetsPage() {
  const { project, toast } = useHibtv();
  const navigate = useNavigate();
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<Tab>("asset");

  const load = useCallback(async () => {
    if (!project) {
      setItems([]);
      return;
    }
    setLoading(true);
    try {
      const res = await hibtvApi.library(project.id);
      setItems(res.items);
    } catch (err) {
      toast(errText(err));
    } finally {
      setLoading(false);
    }
  }, [project, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const buckets = useMemo(() => {
    const by = (g: string) => items.filter((i) => i.group === g);
    const cast = by("cast");
    const scene = by("scene");
    const prop = by("prop");
    return {
      cast,
      scene,
      prop,
      asset: [...cast, ...scene, ...prop],
      frame: by("frame"),
      segment: by("segment"),
      render: by("render"),
    };
  }, [items]);

  const shown = buckets[tab];
  const total = items.length;

  if (!project) {
    return (
      <Stage>
        <Empty glyph="剧" title="还没有项目" sub="先在首页粘贴或上传小说正文。">
          <Link className="btn gold" to="/hibtv/home">
            去建项目
          </Link>
        </Empty>
      </Stage>
    );
  }

  const imageCard = (it: LibraryItem) => (
    <div className="art-card" key={it.id}>
      <Plate
        kind={it.group === "cast" ? "person" : it.group === "prop" ? "prop" : it.group === "frame" ? "frame" : "scene"}
        label={String(it.code)}
        view={it.version ? `v${it.version}` : undefined}
        src={it.url}
        ratio={
          it.group === "cast" ? "896 / 1152" : it.group === "prop" ? "1 / 1" : it.group === "scene" ? "16 / 9" : "9 / 16"
        }
      />
      <div className="art-body">
        <div className="cast-name">
          <span className="mid">{it.code}</span>
          <span>{it.name}</span>
        </div>
        <div className="art-foot">
          <span className="chip green">可用</span>
          <span className="tiny dim">{it.at}</span>
          <span className="sp" style={{ marginLeft: "auto" }} />
          <a className="link" href={mediaUrl(it.url)} target="_blank" rel="noreferrer">
            看原图
          </a>
        </div>
      </div>
    </div>
  );

  const videoCard = (it: LibraryItem) => (
    <div className="art-card" key={it.id}>
      <VideoBox
        src={it.url}
        label={it.name}
        view={`v${it.version}`}
        ratio={project.aspectRatio === "16:9" ? "16 / 9" : "9 / 16"}
        maxHeight={260}
      />
      <div className="art-body">
        <div className="cast-name">
          <span className="mid">{it.code}</span>
          <span>{it.name}</span>
          <span className="cast-alias">v{it.version}</span>
        </div>
        <div className="art-foot">
          <span className="chip green">可用</span>
          <span className="tiny dim">{it.at}</span>
          <span className="sp" style={{ marginLeft: "auto" }} />
          <a className="link" href={mediaUrl(it.url)} target="_blank" rel="noreferrer">
            下载
          </a>
        </div>
      </div>
    </div>
  );

  return (
    <Stage>
      <PageHead
        eyebrow="资产 · 素材库 · ASSETS"
        title={
          <>
            素材库{" "}
            <span className="chip" style={{ verticalAlign: "middle" }}>
              {total} 项
            </span>
          </>
        }
        lede="本项目产出的全部图与片：资产主图（一资产一张）、关键帧（每切一张）、段成片（按版本）、整集成片。这里只做总览与下载，换素材请回对应的资产卡或段页。"
        right={
          <>
            <button className="btn" onClick={() => void load()}>
              刷新
            </button>
            <button className="btn gold" onClick={() => navigate("/hibtv/cast")}>
              去角色出图
            </button>
          </>
        }
      />

      <div className="tabs">
        {TABS.map((t) => (
          <span key={t.k} className={`tab ${tab === t.k ? "on" : ""}`} onClick={() => setTab(t.k)}>
            {t.label} <span className="n">{buckets[t.k].length}</span>
          </span>
        ))}
      </div>

      {loading && !total ? <Loading /> : null}

      {tab === "asset" ? (
        shown.length || buckets.cast.length || buckets.scene.length || buckets.prop.length ? (
          ASSET_KINDS.map((kind) =>
            buckets[kind].length ? (
              <div className="sec" key={kind}>
                <div className="h2">
                  {KIND_LABEL[kind]} <span className="chip">{buckets[kind].length} 张</span>
                </div>
                <div className="art-grid">{buckets[kind].map(imageCard)}</div>
              </div>
            ) : null,
          )
        ) : (
          <div style={{ marginTop: 18 }}>
            <Empty
              glyph="库"
              title="还没有资产主图"
              sub="角色 / 场景 / 道具每个条目只出一张图：角色 896×1152、场景 1280×720、道具 1024×1024。"
            >
              <Link className="btn gold" to="/hibtv/cast">
                去角色页出图
              </Link>
            </Empty>
          </div>
        )
      ) : shown.length ? (
        <div className="art-grid">{shown.map((it) => (it.kind === "image" ? imageCard(it) : videoCard(it)))}</div>
      ) : (
        <div style={{ marginTop: 18 }}>
          <Empty
            glyph="库"
            title={tab === "frame" ? "还没有关键帧" : tab === "segment" ? "还没有段成片" : "还没有整集成片"}
            sub={
              tab === "frame"
                ? "关键帧由分镜的图位清单驱动：每切一张，f1..fN 齐全才能 ref2v 出片。"
                : tab === "segment"
                  ? "段出片走 ref2v：关键帧齐了才能出，缺一张就会被拦下。"
                  : "整集成片由段成片按 cutlist 拼接得到。"
            }
          >
            <Link className="btn gold" to={tab === "frame" || tab === "segment" ? "/hibtv/board" : "/hibtv/video"}>
              {tab === "frame" || tab === "segment" ? "去分镜页" : "去成片页"}
            </Link>
          </Empty>
        </div>
      )}

      <div className="tiny dim" style={{ marginTop: 12, lineHeight: 1.9 }}>
        多视图设定稿 / 4 格裁格 / 派生视图已永久下线：每个资产只保留一张主图，要换就是「重出这一张」或「上传替换」。
        历史项目里旧的 <span className="mono">asset.views</span> 只作只读兼容，不再产生新视图。
      </div>
    </Stage>
  );
}
