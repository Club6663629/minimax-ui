/** 项目设置：基本参数（就改、就记变更）+ 链路可用性 + 导出 / 删除。 */
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { hibtvApi, maskHost } from "../api";
import { Stage } from "../HibtvLayout";
import { Empty, PageHead } from "../components/ui";
import { errText, useHibtv } from "../state";

export default function SettingsPage() {
  const { project, status, toast, refreshProject, refreshProjects, selectProject } = useHibtv();
  const navigate = useNavigate();
  const [form, setForm] = useState({
    name: "",
    genre: "",
    episodes: "1",
    minutesMin: "2",
    minutesMax: "5",
    ratio: "9:16",
    keeps: "",
  });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!project) return;
    setForm({
      name: project.name,
      genre: project.genre,
      episodes: String(project.episodes),
      minutesMin: String(project.minutesMin),
      minutesMax: String(project.minutesMax),
      ratio: project.aspectRatio,
      keeps: project.keeps || "",
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
      await hibtvApi.patchProject(project.id, {
        name: form.name.trim() || project.name,
        genre: form.genre,
        episodes: Number(form.episodes) || project.episodes,
        minutes_min: Number(form.minutesMin) || project.minutesMin,
        minutes_max: Number(form.minutesMax) || project.minutesMax,
        aspect_ratio: form.ratio,
        keeps: form.keeps,
      });
      await refreshProject();
      await refreshProjects();
      toast("项目设置已保存（改动记在「变更」里）");
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
    if (!window.confirm("删除该项目？文档、资产与成片记录都会一并删除，不可恢复。")) return;
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

  const row = (label: string, value: string, hint?: string) => (
    <div className="inline" style={{ padding: "6px 0", flexWrap: "wrap" }}>
      <span className="lab" style={{ minWidth: 96 }}>
        {label}
      </span>
      <span className="mono small">{value}</span>
      {hint && <span className="tiny dim">{hint}</span>}
    </div>
  );

  return (
    <Stage>
      <PageHead
        eyebrow="PROJECT · 设置"
        title="项目设置"
        lede="项目级参数只在这里改一次，之后写进所有锚点图、分镜与成片。改完不会自动重跑已出的图与片——需要的那几段单独重跑。"
        right={
          <>
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
                placeholder="都市情感 / 逆袭"
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
                这一项会写进所有锚点图、分镜图与成片；已出过的图不追改
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
            </div>
          </div>
        </div>

        <div>
          <div className="card">
            <div className="card-hd">
              <span className="t">链路可用性</span>
              <span className={`chip ${status?.enabled ? "green" : "red"}`}>
                {status?.enabled ? "短剧已启用" : "短剧未启用"}
              </span>
            </div>
            <div className="card-bd">
              {row(
                "文本 LLM",
                `${status?.llm?.provider || "deepseek"} · ${status?.llm?.model || "—"}`,
                `模式 ${status?.llm?.mode || "—"} · LLM 步骤不计积分`,
              )}
              {row(
                "生图节点",
                `${maskHost(status?.image?.worker) || "qwen21"}${status?.image?.available ? " · 可用" : " · 未探活"}`,
                status?.image?.degraded ? "已降级（纯文本 + 空白参考图）" : status?.image?.note || "",
              )}
              {row(
                "质量门",
                status?.gates?.available ? "node 已就绪" : "未运行（缺 node）",
                status?.gates?.available
                  ? `runner: ${status.gates.node || status.gates.bin} · 确定性校验通过才亮绿灯`
                  : "页面会显式标注「质量门未运行」",
              )}
              {row(
                "整集拼接",
                status?.compose?.available ? "ffmpeg 可用" : "未检测到 ffmpeg",
                status?.compose?.note || (status?.compose?.available ? `ffmpeg: ${status.compose.ffmpeg}` : ""),
              )}
              {row("段长上限", `${status?.segMaxSeconds || 10}s`, "与平台 5 / 8 / 10 / 15 档吸附")}
              {row(
                "锚点先确认",
                status?.anchorConfirm ? "开" : "关",
                status?.anchorConfirm ? "锚点出完要你确认才派生其余视图" : "锚点出完直接派生",
              )}
            </div>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <div className="card-hd">
              <span className="t">计费</span>
              <span className="chip">{status?.credits ?? 0} 积分</span>
            </div>
            <div className="card-bd small mut" style={{ lineHeight: 1.9 }}>
              生图：每张 {status?.cost?.image ?? 0} 积分，系统失败全额退。
              <br />
              段视频：按秒计（5s {status?.cost?.video?.["5"] ?? 0} / 8s{" "}
              {status?.cost?.video?.["8"] ?? 0} / 10s {status?.cost?.video?.["10"] ?? 0} 积分）。
              <br />
              LLM 步骤（大纲 / 角色 / 美术 / 剧本 / 分镜）不计积分，成本由平台承担。
            </div>
          </div>

          <div className="card" style={{ marginTop: 14 }}>
            <div className="card-hd">
              <span className="t">危险操作</span>
              <span className="chip red">不可恢复</span>
            </div>
            <div className="card-bd small mut" style={{ lineHeight: 1.9 }}>
              删除项目会一并删掉文档、资产卡、段与成片记录；已上传的源剧本文件也一起清理。
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
        源文件：剧本 <em>{project.scriptName || "未上传"}</em> · 美术设定{" "}
        <em>{project.artName || "未上传"}</em>。输入是定稿剧本，不做改编；台词逐字保留。
        <br />
        要换剧本或美术设定，请新建一个项目——已有的大纲、角色卡与分镜都按旧稿锚定。
      </div>
    </Stage>
  );
}
