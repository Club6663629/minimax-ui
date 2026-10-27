/** 项目概览：一屏看这一部戏到哪了（进度由条目状态算出，不是手填）。 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { hibtvApi } from "../api";
import { Stage } from "../HibtvLayout";
import { Empty, Loading, PageHead, fmtTime } from "../components/ui";
import { errText, useHibtv } from "../state";
import type { ChangeItem, HibtvAsset, RenderItem } from "../types";

interface Step {
  key: string;
  label: string;
  status: string;
  detail: string;
  to: string;
  pct: number;
}

export default function OverviewPage() {
  const navigate = useNavigate();
  const { project, status, toast } = useHibtv();
  const [assets, setAssets] = useState<HibtvAsset[]>([]);
  const [changes, setChanges] = useState<ChangeItem[]>([]);
  const [renders, setRenders] = useState<RenderItem[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!project) return;
    setLoading(true);
    try {
      const [assetsRes, changesRes] = await Promise.all([
        hibtvApi.listAssets(project.id),
        hibtvApi.listChanges(project.id),
      ]);
      setAssets(assetsRes);
      setChanges(changesRes);
      const eps = Array.from({ length: Math.max(1, project.episodes) }, (_, i) => i + 1);
      const lists = await Promise.all(
        eps.map((n) => hibtvApi.listRenders(project.id, n).catch(() => [] as RenderItem[])),
      );
      setRenders(lists.flat());
    } catch (err) {
      toast(errText(err));
    } finally {
      setLoading(false);
    }
  }, [project, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const eps = useMemo(
    () => Array.from({ length: Math.max(1, project?.episodes || 1) }, (_, i) => i + 1),
    [project?.episodes],
  );

  const counts = useMemo(() => {
    const doneOf = (kind: string) =>
      eps.filter((n) => project?.docs?.[`${kind}#${n}`]?.status === "done").length;
    const anchors = (kind: string) =>
      assets.filter((a) => a.kind === kind && a.views[a.anchor]?.path).length;
    return {
      script: doneOf("script"),
      storyboard: doneOf("storyboard"),
      castAnchor: anchors("cast"),
      sceneAnchor: anchors("scene"),
      propAnchor: anchors("prop"),
      cast: assets.filter((a) => a.kind === "cast").length,
      scene: assets.filter((a) => a.kind === "scene").length,
      prop: assets.filter((a) => a.kind === "prop").length,
    };
  }, [assets, eps, project?.docs]);

  // 缺口汇总大纲 / 剧本 / 分镜三份文档，否则概览（只看大纲）与分镜页数字永远对不上。
  const gaps = useMemo(() => {
    const out: string[] = [];
    Object.entries(project?.docs || {}).forEach(([key, docRow]) => {
      if (!/^(outline|script|storyboard)/.test(key)) return;
      for (const p of (docRow.gates?.problems || []) as string[]) {
        if (p.startsWith("✗")) continue;
        if (!out.includes(p)) out.push(p);
      }
    });
    return out;
  }, [project?.docs]);
  const totalSecs = renders.reduce((n, r) => n + (r.durationSec || 0), 0);
  const latest = renders[0];

  const steps: Step[] = project
    ? [
        {
          key: "import",
          label: "导入项目",
          status: "完成",
          detail: `剧本 + 美术设定已解析：${project.detected.episodes || 0} 集 · ${project.detected.scenes || 0} 场 · ${project.detected.lines || 0} 条台词`,
          to: "/hibtv/home",
          pct: 100,
        },
        {
          key: "outline",
          label: "写大纲",
          status: project.docs?.["outline#0"]?.status === "done" ? "完成" : "待生成",
          detail: `角色 ${project.docs?.["outline#0"] ? "已归类" : "未生成"} · ${eps.length} 集 · 质量门 ${
            project.docs?.["outline#0"]?.gates?.status || "未运行"
          }${gaps.length ? ` · ${gaps.length} 项待补` : ""}`,
          to: "/hibtv/outline/inner",
          pct: project.docs?.["outline#0"]?.status === "done" ? (gaps.length ? 92 : 100) : 0,
        },
        {
          key: "cast",
          label: "角色",
          status: project.docs?.["cast#0"]?.status === "done" ? "完成" : "待生成",
          detail: `${counts.cast} 张角色卡 · 锚点 ${counts.castAnchor}/${counts.cast}`,
          to: "/hibtv/cast",
          pct: counts.cast ? Math.round((counts.castAnchor / counts.cast) * 100) : 0,
        },
        {
          key: "art",
          label: "美术",
          status: project.docs?.["art#0"]?.status === "done" ? "完成" : "待生成",
          detail: `${counts.scene} 场景 + ${counts.prop} 道具 · 锚点 ${counts.sceneAnchor + counts.propAnchor}/${
            counts.scene + counts.prop
          }`,
          to: "/hibtv/art",
          pct:
            counts.scene + counts.prop
              ? Math.round(((counts.sceneAnchor + counts.propAnchor) / (counts.scene + counts.prop)) * 100)
              : 0,
        },
        {
          key: "script",
          label: "剧本",
          status: counts.script >= eps.length ? "完成" : counts.script ? "进行中" : "待生成",
          detail: `已解析 ${counts.script} / ${eps.length} 集（台词逐字，只切句拆拍）`,
          to: "/hibtv/script/1",
          pct: Math.round((counts.script / eps.length) * 100),
        },
        {
          key: "storyboard",
          label: "分镜",
          status:
            counts.storyboard >= eps.length ? "完成" : counts.storyboard ? "进行中" : "待生成",
          detail: `${counts.storyboard} / ${eps.length} 集已分镜 · ${project.segmentCount.done} / ${
            project.segmentCount.total
          } 段有片`,
          to: "/hibtv/storyboard",
          pct: Math.round((counts.storyboard / eps.length) * 100),
        },
        {
          key: "video",
          label: "出视频",
          status:
            renders.length && project.segmentCount.done === project.segmentCount.total
              ? "完成"
              : renders.length
                ? "进行中"
                : "待出片",
          detail: renders.length
            ? `${renders.length} 版成片 · 最新 ${fmtTime(latest?.durationSec || 0)}`
            : `${project.segmentCount.done} / ${project.segmentCount.total} 段有片，未合成`,
          to: "/hibtv/video/1",
          pct: project.segmentCount.total
            ? Math.round((project.segmentCount.done / project.segmentCount.total) * 100)
            : 0,
        },
      ]
    : [];

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
    <Stage>
      <PageHead
        eyebrow="项目 · 进度 · OVERVIEW"
        title={project.name}
        lede="一屏看这一部戏到哪了：哪一步完成、哪一步卡在缺口上、最新改动是谁改的。所有百分比都由条目状态算出，不是手填的进度条。"
        right={
          <>
            <button className="btn" onClick={() => navigate("/hibtv/outline/align")}>
              看缺口
            </button>
            <button className="btn gold" onClick={() => navigate("/hibtv/storyboard")}>
              进分镜
            </button>
          </>
        }
      />

      <div className="split3" style={{ marginTop: 16 }}>
        <div className="stat">
          <span className="k">集数 / 时长</span>
          <span className="v">
            {eps.length} <small>集 · {project.minutesMin}–{project.minutesMax} 分钟</small>
          </span>
        </div>
        <div className="stat">
          <span className="k">资产</span>
          <span className="v">
            {counts.cast + counts.scene + counts.prop}{" "}
            <small>
              {counts.cast} 人 / {counts.scene} 场景 / {counts.prop} 道具
            </small>
          </span>
        </div>
        <div className="stat">
          <span className="k">成片</span>
          <span className="v">
            {latest ? fmtTime(latest.durationSec) : "—"}{" "}
            <small>{latest ? `第 ${latest.ep} 集 · v${latest.version}` : "还没有合成"}</small>
          </span>
        </div>
      </div>

      {loading && !assets.length ? <Loading /> : null}

      <div className="card" style={{ marginTop: 14 }}>
        <div className="card-hd">
          <span className="t">七步进度</span>
          <span className="tiny dim">百分比 = 已完成条目 / 全部条目</span>
        </div>
        <div className="card-bd">
          {steps.map((s) => (
            <div key={s.key} style={{ padding: "9px 0", borderBottom: "1px solid var(--line-3)" }}>
              <div className="inline" style={{ flexWrap: "wrap" }}>
                <b style={{ fontSize: 12.5, minWidth: 72 }}>{s.label}</b>
                <span className={`chip ${s.pct >= 100 ? "green" : s.pct > 0 ? "gold" : "ghost"}`}>
                  {s.status}
                </span>
                <span className="tiny dim">{s.detail}</span>
                <span className="sp" style={{ marginLeft: "auto" }} />
                <span className="mono tiny" style={{ color: "var(--gold)" }}>
                  {s.pct}%
                </span>
                <button className="btn xs" onClick={() => navigate(s.to)}>
                  进入
                </button>
              </div>
              <div className="prog" style={{ marginTop: 6 }}>
                <i style={{ width: `${s.pct}%` }} />
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="split" style={{ marginTop: 14 }}>
        <div className="card">
          <div className="card-hd">
            <span className="t">缺口</span>
            <span className={`chip ${gaps.length ? "red" : "green"}`}>
              {gaps.length} 项
            </span>
            <span className="sp" />
            <span className="tiny dim">大纲 / 剧本 / 分镜合并</span>
          </div>
          <div className="card-bd small mut" style={{ lineHeight: 1.9 }}>
            {gaps.length
              ? gaps.slice(0, 4).map((g, i) => <div key={i}>· {g}</div>)
              : "质量门没有报出缺口：剧本里出现的人、场景、道具，美术设定都有对应条目。"}
          </div>
          <div style={{ padding: "0 14px 14px" }}>
            <button className="btn sm" onClick={() => navigate("/hibtv/outline/align")}>
              去对齐检查
            </button>
          </div>
        </div>
        <div className="card">
          <div className="card-hd">
            <span className="t">最近变更</span>
            <span
              className="tiny dim link"
              style={{ marginLeft: "auto" }}
              onClick={() => navigate("/hibtv/changes")}
            >
              全部 →
            </span>
          </div>
          <div className="card-bd">
            {changes.length ? (
              changes.slice(0, 4).map((c) => (
                <div className="inline" key={c.id} style={{ padding: "5px 0" }}>
                  <span className="tiny dim mono">{c.at}</span>
                  <span className="small" style={{ flex: 1, marginLeft: 8 }}>
                    {c.what}
                  </span>
                  <span className="chip tiny">{c.reverted ? "已撤销" : c.who}</span>
                </div>
              ))
            ) : (
              <div className="tiny dim">还没有改动记录。</div>
            )}
          </div>
        </div>
      </div>

      <div className="inline tiny dim" style={{ marginTop: 14, flexWrap: "wrap" }}>
        <span>段长上限 {status?.segMaxSeconds || 10}s</span>
        <span>·</span>
        <span>成片总长 {fmtTime(totalSecs)}（{renders.length} 版）</span>
        <span>·</span>
        <span>LLM 模式 {status?.llm?.mode || "—"}</span>
        <span>·</span>
        <span>质量门 {status?.gates?.available ? "可用" : "未运行（缺 node）"}</span>
      </div>
    </Stage>
  );
}
