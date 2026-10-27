/** 素材库：本项目已生成 / 已上传的图与片（按项目隔离，过期会打斜纹）。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { hibtvApi, mediaUrl } from "../api";
import { Stage } from "../HibtvLayout";
import { Empty, Loading, PageHead, Plate, VideoBox } from "../components/ui";
import { errText, useHibtv } from "../state";
import type { LibraryItem } from "../types";

type Tab = "all" | "image" | "video";

const GROUP_LABEL: Record<string, string> = {
  cast: "角色",
  scene: "场景",
  prop: "道具",
  segment: "段成片",
  render: "整集成片",
};

export default function AssetsPage() {
  const { project, toast } = useHibtv();
  const navigate = useNavigate();
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<Tab>("all");

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

  const images = useMemo(() => items.filter((i) => i.kind === "image"), [items]);
  const videos = useMemo(() => items.filter((i) => i.kind === "video"), [items]);
  const shown = tab === "image" ? images : tab === "video" ? videos : items;

  if (!project) {
    return (
      <Stage>
        <Empty glyph="剧" title="还没有项目" sub="先在首页上传定稿剧本与美术设定。">
          <Link className="btn gold" to="/hibtv/home">
            去建项目
          </Link>
        </Empty>
      </Stage>
    );
  }

  return (
    <Stage>
      <PageHead
        eyebrow="资产 · 素材库 · ASSETS"
        title={
          <>
            素材库{" "}
            <span className="chip" style={{ verticalAlign: "middle" }}>
              {items.length} 项
            </span>
          </>
        }
        lede="本项目生成与上传的全部图与片都收在这里：角色 / 场景 / 道具的每一版视图、每一段的每一版成片、每一版整集成片。换素材请回对应的卡片或段页改，这里只做总览。"
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
        {(
          [
            ["all", "全部", items.length],
            ["image", "图", images.length],
            ["video", "片", videos.length],
          ] as [Tab, string, number][]
        ).map(([k, label, n]) => (
          <span key={k} className={`tab ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>
            {label} <span className="n">{n}</span>
          </span>
        ))}
      </div>

      {loading && !items.length ? <Loading /> : null}

      {shown.length ? (
        <div className="art-grid">
          {shown.map((it) => (
            <div className="art-card" key={it.id}>
              {it.kind === "image" ? (
                <Plate
                  kind={it.group === "cast" ? "person" : it.group === "prop" ? "prop" : "scene"}
                  label={it.name}
                  view={`v${it.version}`}
                  src={it.url}
                  extra={it.stale ? "stale" : ""}
                />
              ) : (
                <VideoBox
                  src={it.url}
                  label={it.name}
                  view={`v${it.version}`}
                  ratio={project.aspectRatio === "16:9" ? "16 / 9" : "9 / 16"}
                  maxHeight={260}
                />
              )}
              <div className="art-body">
                <div className="cast-name">
                  <span className="mid">{it.code}</span>
                  <span>{it.name}</span>
                  <span className="tag">{GROUP_LABEL[it.group] || it.group}</span>
                  <span className="cast-alias">v{it.version}</span>
                </div>
                <div className="art-foot">
                  {it.stale ? (
                    <span className="chip red">已过期</span>
                  ) : (
                    <span className="chip green">可用</span>
                  )}
                  <span className="tiny dim">{it.at}</span>
                  <span className="sp" style={{ marginLeft: "auto" }} />
                  <a className="link" href={mediaUrl(it.url)} target="_blank" rel="noreferrer">
                    看原图
                  </a>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ marginTop: 18 }}>
          <Empty
            glyph="库"
            title="素材库还是空的"
            sub="角色 / 场景 / 道具出图后，图和段成片都会自动收进这里。"
          >
            <Link className="btn gold" to="/hibtv/cast">
              去角色页出图
            </Link>
          </Empty>
        </div>
      )}

      <div className="tiny dim" style={{ marginTop: 12, lineHeight: 1.9 }}>
        「已过期」= 上游锚点或提示词变过，这张图还没重出；过期素材不会被自动删除，仍可切回使用。
      </div>
    </Stage>
  );
}
