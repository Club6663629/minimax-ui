/** 项目设置：基本参数（改就记变更）+ codex 网关区块 + 计费 + 导出 / 删除。
 *
 * codex 网关是文本 7 段的唯一入口：这里能看到 /healthz 自检、skills 数量、queue_position、
 * 最近 job 与 /v1/stats 的 tokens/耗时汇总，并能取消跑在网关上的作业（不调 DELETE，产物仍可下载）。
 * 本地 LLM / node 门已经删干净：这一块只留一句只读说明，避免下次又长出手搓。
 */
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { hibtvApi, maskHost } from "../api";
import { Stage } from "../HibtvLayout";
import { CodexLight } from "../components/CodexLight";
import { Empty, GenBar, PageHead } from "../components/ui";
import { errText, labelOfKind, useHibtv } from "../state";

const TEXT_KINDS = ["outline", "cast", "art", "refs", "script", "staging", "storyboard"];

export default function SettingsPage() {
  const {
    project,
    status,
    codex,
    codexError,
    jobs,
    toast,
    refreshProject,
    refreshProjects,
    refreshCodex,
    selectProject,
  } = useHibtv();
  const navigate = useNavigate();
  const [checking, setChecking] = useState(false);
  const [form, setForm] = useState({
    name: "",
    genre: "",
    episodes: "1",
    minutesMin: "2",
    minutesMax: "5",
    ratio: "9:16",
    style: "写实",
    keeps: "",
    novelName: "",
    novelText: "",
  });
  const [busy, setBusy] = useState(false);
  const [sourceFiles,setSourceFiles] = useState<{script:File|null;art:File|null}>({script:null,art:null});

  useEffect(() => {
    if (!project) return;
    setForm({
      name: project.name,
      genre: project.genre,
      episodes: String(project.episodes),
      minutesMin: String(project.minutesMin),
      minutesMax: String(project.minutesMax),
      ratio: project.aspectRatio,
      style: project.style || "写实",
      keeps: project.keeps || "",
      novelName: project.novelName || "",
      novelText: "",
    });
  }, [project]);

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

  const save = async () => {
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        name: form.name.trim() || project.name,
        genre: form.genre,
        episodes: Number(form.episodes) || project.episodes,
        minutes_min: Number(form.minutesMin) || project.minutesMin,
        minutes_max: Number(form.minutesMax) || project.minutesMax,
        aspect_ratio: form.ratio,
        style: form.style.trim() || "写实",
        keeps: form.keeps,
      };
      // 只有真的粘了新正文才提交：novel_text 一变更后端会重写 source.txt
      await hibtvApi.patchProject(project.id, body);
      await refreshProject();
      await refreshProjects();
      toast(
        form.novelText.trim()
          ? "已保存，正文已重写 source.txt；大纲需重跑才会用新正文"
          : "项目设置已保存（改动记在「变更」里）",
      );
      setForm({ ...form, novelText: "" });
    } catch (err) {
      toast(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const exportJson = async () => {
    try {
      const data = await hibtvApi.exportProject(project.id);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${project.name || "hibtv"}-export.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast("已导出项目 JSON（文档 + 成片记录）");
    } catch (err) {
      toast(errText(err));
    }
  };

  const remove = async () => {
    if (!window.confirm("删除该项目？文档、资产、关键帧与成片记录都会一并删除，不可恢复。")) return;
    try {
      await hibtvApi.deleteProject(project.id);
      await refreshProjects();
      selectProject(0);
      toast("项目已删除");
      navigate("/hibtv/home");
    } catch (err) {
      toast(errText(err));
    }
  };

  const cancel = async (jobId: number, kind: string) => {
    try {
      if (TEXT_KINDS.includes(kind)) await hibtvApi.cancelRemoteJob(project.id, jobId);
      else await hibtvApi.cancelJob(project.id, jobId);
      toast("已取消");
    } catch (err) {
      toast(errText(err));
    }
  };

  const row = (label: string, value: string, hint?: string) => (
    <div className="inline" style={{ padding: "6px 0", flexWrap: "wrap" }}>
      <span className="lab" style={{ minWidth: 96 }}>
        {label}
      </span>
      <span className="mono small">{value}</span>
      {hint && <span className="tiny dim">{hint}</span>}
    </div>
  );

  const active = jobs.filter((j) => j.status === "queued" || j.status === "running");
  const stageStats = codex?.stats?.byStage || [];
  const health = codex?.healthz || {};
  // healthz.codex 是对象（{ok, version}），别直接 String() 成 [object Object]
  const codexVersion =
    health.codex && typeof health.codex === "object"
      ? String((health.codex as Record<string, unknown>).version || (health.codex as Record<string, unknown>).ok || "")
      : String(health.codex || "");

  return (
    <Stage>
      <PageHead
        eyebrow="PROJECT · 设置"
        title="项目设置"
        lede="项目级参数只在这里改一次，之后写进所有资产图、关键帧与成片。改完不会自动重跑已出的图与片——需要的那几段单独重跑。"
        right={
          <>
            <CodexLight />
            <button className="btn" onClick={() => void exportJson()}>
              导出项目 JSON
            </button>
            <button className="btn gold" disabled={busy} onClick={() => void save()}>
              {busy ? "保存中…" : "保存"}
            </button>
          </>
        }
      />

      <div className="split" style={{ marginTop: 16 }}>
        <div>
          <div className="card">
            <div className="card-hd">
              <span className="t">基本参数</span>
              <span className="chip">模型服务固定</span>
            </div>
            <div className="card-bd">
              <div className="field">
                <div className="label">项目名</div>
                <input
                  className="input"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </div>
              <div className="field">
                <div className="label">题材</div>
                <input
                  className="input"
                  value={form.genre}
                  placeholder="男频逆袭 / 都市情感"
                  onChange={(e) => setForm({ ...form, genre: e.target.value })}
                />
              </div>
              <div className="row2">
                <div className="field">
                  <div className="label">总集数</div>
                  <input
                    className="input"
                    value={form.episodes}
                    onChange={(e) => setForm({ ...form, episodes: e.target.value })}
                  />
                </div>
                <div className="field">
                  <div className="label">单集时长范围（分钟）</div>
                  <div className="inline">
                    <input
                      className="input"
                      style={{ width: 64 }}
                      value={form.minutesMin}
                      onChange={(e) => setForm({ ...form, minutesMin: e.target.value })}
                    />
                    <span className="dim">—</span>
                    <input
                      className="input"
                      style={{ width: 64 }}
                      value={form.minutesMax}
                      onChange={(e) => setForm({ ...form, minutesMax: e.target.value })}
                    />
                  </div>
                </div>
              </div>
              <div className="field">
                <div className="label">画面比例</div>
                <div className="inline">
                  {["9:16", "16:9"].map((r) => (
                    <span
                      key={r}
                      className={`chip i ${form.ratio === r ? "gold" : ""}`}
                      onClick={() => setForm({ ...form, ratio: r })}
                    >
                      {r}
                      {r === "9:16" ? " 竖屏 · 短剧默认" : " 横屏"}
                    </span>
                  ))}
                </div>
                <div className="tiny dim" style={{ marginTop: 6 }}>
                  关键帧落 {form.ratio === "16:9" ? "1344×768" : "768×1344"}；资产图按类型固定尺寸。
                  已出过的图不追改。
                </div>
              </div>
              <div className="field">
                <div className="label">画风</div>
                <div className="inline" style={{ flexWrap: "wrap" }}>
                  {["写实", "电影质感", "强对比港风"].map((s) => (
                    <span
                      key={s}
                      className={`chip i ${form.style === s ? "gold" : ""}`}
                      onClick={() => setForm({ ...form, style: s })}
                    >
                      {s}
                    </span>
                  ))}
                </div>
                <input
                  className="input"
                  style={{ marginTop: 6 }}
                  value={form.style}
                  placeholder="写实 / 电影质感 / 强对比港风，也可自定义"
                  onChange={(e) => setForm({ ...form, style: e.target.value })}
                />
                <div className="tiny dim" style={{ marginTop: 6 }}>
                  画风会写进角色、场景、道具与关键帧的出图提示词；本剧为写实真人短剧，禁止卡通/动漫/插画。已出过的图不追改，需重跑对应阶段。
                </div>
              </div>
              <div className="field">
                <div className="label">必须保留的名场面 / 场次</div>
                <textarea
                  className="textarea"
                  value={form.keeps}
                  placeholder="例：寿宴上叶辰开口借一百万、被萧老太太当众羞辱的那场戏"
                  onChange={(e) => setForm({ ...form, keeps: e.target.value })}
                />
                <div className="tiny dim" style={{ marginTop: 5 }}>
                  这一项会作为「必须保留」写进 outline 的 input。
                </div>
              </div>
            </div>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <div className="card-hd">
              <span className="t">剧本与美术设定</span>
              <span className="chip">{project.scriptName || "尚未上传剧本"}</span>
              <span className={`chip ${project.hasSources ? "green" : "red"}`}>
                {project.hasSources ? "两份资料已保存" : "请补传双资料"}
              </span>
            </div>
            <div className="card-bd">
              <div className="tiny dim">美术设定：{project.artName || "尚未上传"}</div>
              {(["script","art"] as const).map(key=><label key={key} className="field"><span className="label">{key==="script"?"替换剧本":"替换美术设定"}</span>
                <input type="file" accept=".md,.txt,.markdown,.docx" onChange={e=>setSourceFiles({...sourceFiles,[key]:e.target.files?.[0]||null})}/></label>)}
              <button className="btn" disabled={busy||!sourceFiles.script||!sourceFiles.art} onClick={async()=>{
                if(!sourceFiles.script||!sourceFiles.art)return;setBusy(true);
                try{await hibtvApi.replaceSources(project.id,sourceFiles.script,sourceFiles.art);await refreshProject();await refreshProjects();toast("双资料已更新，请重新解析大纲及下游");}
                catch(e){toast(errText(e));}finally{setBusy(false);}
              }}>替换两份资料</button>
            </div>
          </div>
        </div>

        <div>
          <div className="card">
            <div className="card-hd">
              <span className="t">codex 网关</span>
              <span className={`chip ${codex?.available ? "green" : "red"}`}>
                {codex?.available ? "可用" : "不可达"}
              </span>
              <span style={{ marginLeft: "auto" }} />
              <button
                className="btn xs"
                disabled={checking}
                onClick={async () => {
                  setChecking(true);
                  const ok = await refreshCodex();
                  setChecking(false);
                  toast(ok ? "网关可用" : "网关不可达：原因见下方红字");
                }}
              >
                {checking ? "自检中…" : "重新自检"}
              </button>
            </div>
            <div className="card-bd">
              {row("地址", maskHost(codex?.url) || status?.codex?.url || "—", "同机回环，公网已 404")}
              {row(
                "token",
                codex?.hasToken ? "已配置（只存文件路径）" : "未配置",
                maskHost(codex?.tokenPath) || "",
              )}
              {row("project_id 前缀", codex?.projectPrefix || `hibtv-${project.id}`, "网关侧产物根目录")}
              {row(
                "codex 版本",
                codexVersion || "—",
                health.deepseek ? "deepseek present" : health.deepseek === false ? "deepseek 缺失" : "",
              )}
              {row(
                "skills",
                `${health.skills ?? (codex?.skills || []).length} 个`,
                (codex?.skills || [])
                  .slice(0, 7)
                  .map((s) => s.name)
                  .join(" · "),
              )}
              {row("磁盘", String(health.disk || "—"))}
              {codexError ? <div className="tiny red" style={{ marginTop: 6 }}>{codexError}</div> : null}
              <div className="inline" style={{ marginTop: 6, flexWrap: "wrap" }}>
                <span className={`chip ${active.length ? "gold" : "ghost"}`}>队列 {active.length}</span>
                {active[0]?.queuePosition ? (
                  <span className="chip">queue_position {active[0].queuePosition}</span>
                ) : null}
                <span className="chip ghost">单并发 · 全局 FIFO</span>
              </div>
            </div>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <div className="card-hd">
              <span className="t">最近作业</span>
              <span className="chip">{jobs.length}</span>
            </div>
            <div className="card-bd" style={{ maxHeight: 300, overflow: "auto" }}>
              {jobs.slice(0, 10).map((j) => (
                <div key={j.id} className="inline" style={{ padding: "4px 0", flexWrap: "wrap" }}>
                  <span className="mono tiny" style={{ color: "var(--gold-2)" }}>
                    {labelOfKind(j.kind)}
                    {j.ep ? ` E${String(j.ep).padStart(2, "0")}` : ""}
                  </span>
                  <span className={`chip tiny ${j.status === "failed" ? "red" : j.status === "done" ? "green" : ""}`}>
                    {j.status}
                  </span>
                  {j.tokens?.in ? (
                    <span className="tiny dim">
                      {j.tokens.in.toLocaleString()} in / {j.tokens.out.toLocaleString()} out
                    </span>
                  ) : null}
                  {j.elapsedSec ? <span className="tiny dim">{Math.round(j.elapsedSec)}s</span> : null}
                  {j.timeoutKind ? <span className="tiny red">{j.timeoutKind}</span> : null}
                  <span className="sp" style={{ marginLeft: "auto" }} />
                  {["queued", "running"].includes(j.status) ? (
                    <button className="btn xs" onClick={() => void cancel(j.id, j.kind)}>
                      取消
                    </button>
                  ) : null}
                </div>
              ))}
              {!jobs.length && <div className="tiny dim">本项目还没有作业记录。</div>}
              {active[0] ? (
                <GenBar
                  job={active[0]}
                  onCancel={() => void cancel(active[0].id, active[0].kind)}
                />
              ) : null}
            </div>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <div className="card-hd">
              <span className="t">网关 tokens / 耗时</span>
              <span className="chip ghost">/v1/stats 按 stage 汇总</span>
            </div>
            <div className="card-bd">
              {stageStats.length ? (
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>stage</th>
                      <th>job</th>
                      <th>in</th>
                      <th>out</th>
                      <th>耗时</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stageStats.map((s) => (
                      <tr key={s.stage}>
                        <td className="mono tiny">{s.stage}</td>
                        <td className="mono tiny">
                          {s.done ?? 0}/{s.jobs ?? 0}
                        </td>
                        <td className="mono tiny">{(s.input_tokens || 0).toLocaleString()}</td>
                        <td className="mono tiny">{(s.output_tokens || 0).toLocaleString()}</td>
                        <td className="mono tiny">{Math.round(s.elapsed_sec || 0)}s</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="tiny dim">
                  {codex?.available
                    ? "网关还没有统计记录（或 /v1/stats 未返回 byStage）。"
                    : "网关不可达，统计读不到。"}
                </div>
              )}
              <div className="tiny dim" style={{ marginTop: 8, lineHeight: 1.9 }}>
                文本 7 段本期不扣积分，但成本要看得见：一集 7 个 stage 的 input tokens 通常在百万级。
                <br />
                幂等键 = project + kind + ep + hint + 上游指纹：同键且已 done 会直接复用，不重复烧 token。
              </div>
            </div>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <div className="card-hd">
              <span className="t">链路可用性</span>
              <span className={`chip ${status?.enabled ? "green" : "red"}`}>
                {status?.enabled ? "短剧已启用" : "短剧未启用"}
              </span>
            </div>
            <div className="card-bd">
              {row(
                "生图节点",
                `${maskHost(status?.image?.worker) || "qwen21"}${status?.image?.available ? " · 可用" : " · 未探活"}`,
                status?.image?.degraded ? "已降级（纯文本 + 空白参考图）" : status?.image?.note || "",
              )}
              {row(
                "质量门",
                "读网关产物",
                "优先 .gates.jsonl，缺则退回 *-report.html 的 gatepill；都没有就标「产物里没有门结果」",
              )}
              {row(
                "整集拼接",
                status?.compose?.available ? "ffmpeg 可用" : "未检测到 ffmpeg",
                status?.compose?.note || (status?.compose?.available ? `ffmpeg: ${status.compose.ffmpeg}` : ""),
              )}
              {row("段长上限", `${status?.segMaxSeconds || 13}s`, "H3 只出 5+17n 帧")}
              {row(
                "段请求帧数",
                `${status?.segMaxFrames || 311} 帧`,
                `${((status?.segMaxFrames || 311) / 24).toFixed(4)} 秒（传 13 会吸附成 13.67s）`,
              )}
              {row("关键帧尺寸", (status?.frameSize?.[form.ratio] || [768, 1344]).join("×"), "竖屏原生画布")}
              {row(
                "本地 LLM",
                "已下线",
                "文本阶段全部走 codex 网关：没有 node 门、没有本地骨架、没有兜底",
              )}
            </div>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <div className="card-hd">
              <span className="t">计费</span>
              <span className="chip">{status?.credits ?? 0} 积分</span>
            </div>
            <div className="card-bd small mut" style={{ lineHeight: 1.9 }}>
              资产图：每张 {status?.cost?.image ?? 0} 积分（一资产一张，系统失败全额退）。
              <br />
              关键帧：每张 {status?.cost?.image ?? 0} 积分（每切一张）。
              <br />
              段视频：按秒计（5s {status?.cost?.video?.["5"] ?? 0} / 8s {status?.cost?.video?.["8"] ?? 0} / 10s{" "}
              {status?.cost?.video?.["10"] ?? 0} 积分）；请求值按 `frames_for(Σ切秒)` 对齐。
              <br />
              文本 7 段不计积分，成本由平台承担，但 tokens 与耗时在上面的网关区块可见。
            </div>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <div className="card-hd">
              <span className="t">危险操作</span>
              <span className="chip red">不可恢复</span>
            </div>
            <div className="card-bd small mut" style={{ lineHeight: 1.9 }}>
              删除项目会一并删掉文档、资产卡、关键帧、段与成片记录。
            </div>
            <div style={{ padding: "0 14px 14px" }}>
              <button className="btn danger" onClick={() => void remove()}>
                删除这个项目
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="tiny dim" style={{ marginTop: 14, lineHeight: 1.9 }}>
        源文件：剧本 <em>{project.scriptName || "未上传"}</em>，美术设定 <em>{project.artName || "未上传"}</em>。
        <br />
        更换资料后重新解析大纲及下游，原剧本的台词与场次保持不变。
      </div>
    </Stage>
  );
}
