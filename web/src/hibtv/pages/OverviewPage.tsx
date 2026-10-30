/** 项目概览：一屏看这一部戏走到哪一步（进度由条目状态算出，不是手填）。
 *
 * 十步 = 原著 → 大纲 → 角色 → 美术 → 参考图 → 剧本 → 站位 → 分镜 → 关键帧 → 成片。
 * 文本 7 段全由 codex 跑 skill；平台只负责像素与界面。codex 掉线时顶部红灯且提交被禁。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { hibtvApi } from "../api";
import { Stage } from "../HibtvLayout";
import { CodexLight, useCodexGate } from "../components/CodexLight";
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
  const { project, status, codex, activeJobs, toast } = useHibtv();
  const gate = useCodexGate();
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
    const byKind = (kind: string) => assets.filter((a) => a.kind === kind);
    const withMain = (kind: string) => byKind(kind).filter((a) => a.main?.path).length;
    return {
      script: doneOf("script"),
      staging: doneOf("staging"),
      storyboard: doneOf("storyboard"),
      cast: byKind("cast").length,
      castMain: withMain("cast"),
      scene: byKind("scene").length,
      sceneMain: withMain("scene"),
      prop: byKind("prop").length,
      propMain: withMain("prop"),
      refs: byKind("cast").filter((a) => a.data?.refs).length,
    };
  }, [assets, eps, project?.docs]);

  // 缺口汇总所有文本文档的门结果（reading artifacts，不是平台自己猜的规则）
  const gaps = useMemo(() => {
    const out: string[] = [];
    Object.entries(project?.docs || {}).forEach(([key, docRow]) => {
      if (!/^(outline|cast|art|script|staging|storyboard)/.test(key)) return;
      for (const p of (docRow.gates?.problems || []) as string[]) {
        if (p.startsWith("✗")) continue;
        if (!out.includes(p)) out.push(`${key.split("#")[0]}：${p}`);
      }
    });
    return out;
  }, [project?.docs]);

  const totalSecs = renders.reduce((n, r) => n + (r.durationSec || 0), 0);
  const latest = renders[0];
  const pctOf = (done: number, total: number) => (total ? Math.round((done / total) * 100) : 0);
  const docDone = (key: string) => project?.docs?.[key]?.status === "done";

  const steps: Step[] = project
    ? [
        {
          key: "novel",
          label: "原著",
          status: project.hasNovel ? "完成" : "缺正文",
          detail: project.hasNovel
            ? `${project.novelName || "正文已落盘"} · source.txt · ${eps.length} 集 × ${project.targetSeconds ? `${project.targetSeconds} 秒` : `${project.minutesMin}–${project.minutesMax} 分钟`}`
            : "还没有小说正文：outline 的 input 只给绝对路径，必须先落盘",
          to: "/hibtv/home",
          pct: project.hasNovel ? 100 : 0,
        },
        {
          key: "outline",
          label: "大纲",
          status: docDone("outline#0") ? "完成" : "待生成",
          detail: docDone("outline#0")
            ? `${eps.length} 集 · ${project.docs?.["outline#0"]?.meta?.stage || "novel-outline"} · 门 ${
                project.docs?.["outline#0"]?.gates?.status || "未运行"
              }`
            : "novel-outline：改编方案 + 分集梗概 + 爽点时间轴",
          to: "/hibtv/outline/inner",
          pct: docDone("outline#0") ? (gaps.length ? 92 : 100) : 0,
        },
        {
          key: "cast",
          label: "角色",
          status: docDone("cast#0") ? "完成" : "待生成",
          detail: `${counts.cast} 张角色卡 · 锚点 ${counts.castMain}/${counts.cast}`,
          to: "/hibtv/cast",
          pct: pctOf(counts.castMain, counts.cast),
        },
        {
          key: "art",
          label: "美术",
          status: docDone("art#0") ? "完成" : "待生成",
          detail: `${counts.scene} 场景 + ${counts.prop} 道具 · 锚点 ${counts.sceneMain + counts.propMain}/${
            counts.scene + counts.prop
          }`,
          to: "/hibtv/art",
          pct: pctOf(counts.sceneMain + counts.propMain, counts.scene + counts.prop),
        },
        {
          key: "refs",
          label: "参考图",
          status: counts.refs ? "完成" : "待生成",
          detail: `${counts.refs} / ${counts.cast} 人有分层底稿（asset.json）`,
          to: "/hibtv/cast",
          pct: pctOf(counts.refs, counts.cast),
        },
        {
          key: "script",
          label: "剧本",
          status: counts.script >= eps.length ? "完成" : counts.script ? "进行中" : "待生成",
          detail: `已出 ${counts.script} / ${eps.length} 集（场次 + 节拍 + 台词逐字）`,
          to: "/hibtv/script/1",
          pct: pctOf(counts.script, eps.length),
        },
        {
          key: "staging",
          label: "站位",
          status: counts.staging >= eps.length ? "完成" : counts.staging ? "进行中" : "待生成",
          detail: `${counts.staging} / ${eps.length} 集（平面图物品 + 人物点位 / 朝向 / 群演）`,
          to: "/hibtv/staging/1",
          pct: pctOf(counts.staging, eps.length),
        },
        {
          key: "storyboard",
          label: "分镜",
          status: counts.storyboard >= eps.length ? "完成" : counts.storyboard ? "进行中" : "待生成",
          detail: `${counts.storyboard} / ${eps.length} 集 · ${project.segmentCount.total} 段`,
          to: "/hibtv/storyboard",
          pct: pctOf(counts.storyboard, eps.length),
        },
        {
          key: "frames",
          label: "关键帧",
          status:
            project.frameCount.total && project.frameCount.done >= project.frameCount.total
              ? "完成"
              : project.frameCount.done
                ? "进行中"
                : "待出图",
          detail: `${project.frameCount.done} / ${project.frameCount.total} 张（每切一张，序号 = Picture N）`,
          to: "/hibtv/storyboard/1",
          pct: pctOf(project.frameCount.done, project.frameCount.total),
        },
        {
          key: "video",
          label: "成片",
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
          pct: pctOf(project.segmentCount.done, project.segmentCount.total),
        },
      ]
    : [];

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

  const stageStat = codex?.stats?.byStage || [];
  const tokenSum = stageStat.reduce(
    (acc, row) => ({
      in: acc.in + (row.input_tokens || 0),
      out: acc.out + (row.output_tokens || 0),
    }),
    { in: 0, out: 0 },
  );

  return (
    <Stage>
      <PageHead
        eyebrow="项目 · 进度 · OVERVIEW"
        title={project.name}
        lede="十步一屏：文本归 codex（拼 prompt / 跑 skill / 写产物），像素归平台（资产单图 / 关键帧 / ref2v 出片）。百分比都由条目状态算出，不是手填。"
        right={
          <>
            <CodexLight />
            <span className={`chip ${activeJobs.length ? "gold" : "ghost"}`} title="平台侧在跑 / 排队的作业数">
              队列 {activeJobs.length}
              {activeJobs[0]?.queuePosition ? ` · 位 ${activeJobs[0].queuePosition}` : ""}
            </span>
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
          <span className="k">集数 / 单集</span>
          <span className="v">
            {eps.length} <small>集 · {project.targetSeconds ? `${project.targetSeconds} 秒` : `${project.minutesMin}–${project.minutesMax} 分钟`} · 段 ≤ {status?.segMaxSeconds || 13}s</small>
          </span>
        </div>
        <div className="stat">
          <span className="k">资产 / 关键帧</span>
          <span className="v">
            {counts.cast + counts.scene + counts.prop}{" "}
            <small>
              {counts.cast} 人 / {counts.scene} 场景 / {counts.prop} 道具 · 帧 {project.frameCount.done}/
              {project.frameCount.total}
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

      {!gate.ok ? (
        <div className="gates bad" style={{ marginTop: 12 }}>
          <div>{gate.why}</div>
        </div>
      ) : null}

      {loading && !assets.length ? <Loading /> : null}

      <div className="card" style={{ marginTop: 14 }}>
        <div className="card-hd">
          <span className="t">十步进度</span>
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
            <span className={`chip ${!gate.ok ? "ghost" : gaps.length ? "red" : "green"}`}>
              {!gate.ok ? "无法计算" : `${gaps.length} 项`}
            </span>
            <span className="sp" />
            <span className="tiny dim">读的是网关产物的门结果</span>
          </div>
          <div className="card-bd small mut" style={{ lineHeight: 1.9 }}>
            {!gate.ok
              ? `网关不可用，缺口无法计算：${gate.why}`
              : gaps.length
                ? gaps.slice(0, 4).map((g, i) => <div key={i}>· {g}</div>)
                : "文档的门结果里没有失败项（或该阶段产物没有结构化门结果）。"}
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
            <span className="tiny dim link" style={{ marginLeft: "auto" }} onClick={() => navigate("/hibtv/changes")}>
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

      <div className="inline tiny dim" style={{ marginTop: 14, flexWrap: "wrap", gap: 6 }}>
        <span>段长上限 {status?.segMaxSeconds || 13}s</span>
        <span>·</span>
        <span>请求帧 {status?.segMaxFrames || 311} 帧 = {((status?.segMaxFrames || 311) / 24).toFixed(4)}s</span>
        <span>·</span>
        <span>成片总长 {fmtTime(totalSecs)}（{renders.length} 版）</span>
        <span>·</span>
        <span>codex {codex?.available ? `可用（skills ${codex.healthz?.skills ?? "?"}）` : "不可达"}</span>
        {tokenSum.in > 0 && (
          <>
            <span>·</span>
            <span>
              tokens {tokenSum.in.toLocaleString()} in / {tokenSum.out.toLocaleString()} out
            </span>
          </>
        )}
      </div>
    </Stage>
  );
}
