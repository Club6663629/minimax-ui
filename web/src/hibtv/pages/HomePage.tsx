/** 首页：项目列表 + 新建项目（双上传：定稿剧本 + 美术设定）。 */
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { hibtvApi } from "../api";
import { Stage } from "../HibtvLayout";
import { Empty, Modal, PageHead } from "../components/ui";
import { errText, useHibtv } from "../state";

interface Draft {
  script: File | null;
  art: File | null;
  name: string;
  genre: string;
  episodes: string;
  minutesMin: string;
  minutesMax: string;
  ratio: string;
  keeps: string;
}

const EMPTY_DRAFT: Draft = {
  script: null,
  art: null,
  name: "",
  genre: "",
  episodes: "",
  minutesMin: "2",
  minutesMax: "5",
  ratio: "9:16",
  keeps: "",
};

function stageText(p: { docs: Record<string, { status: string }> }): string {
  // docs 的 key 形如 outline#0 / storyboard#1；以前用 p.docs["storyboard"] 查不到，
  // 所以大纲/剧本/分镜全好了，项目卡还写着「已导入：待生成大纲」。
  const has = (k: string) =>
    Object.entries(p.docs || {}).some(([key, row]) => key.split("#")[0] === k && row?.status === "done");
  if (has("storyboard")) return "已生成到：分镜";
  if (has("script")) return "已生成到：剧本";
  if (has("art")) return "已生成到：美术";
  if (has("cast")) return "已生成到：角色";
  if (has("outline")) return "已生成到：大纲 · 待生成角色";
  return "已导入：待生成大纲";
}

export default function HibtvHomePage() {
  const { projects, selectProject, refreshProjects, refreshProject, toast, project: cur } = useHibtv();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);

  const detectedLine = useMemo(() => {
    if (!draft.script || !draft.art) return "两个文件都上传后才会自动识别集数、场次与资产条目";
    return "已选择文件；创建后立即识别集 / 场 / 台词 / 角色 / 场景 / 道具";
  }, [draft.script, draft.art]);

  const create = async () => {
    if (!draft.script) {
      toast("请先上传定稿剧本（.md / .txt）");
      return;
    }
    setBusy(true);
    try {
      const form = new FormData();
      form.append("script", draft.script);
      if (draft.art) form.append("art", draft.art);
      form.append("name", draft.name);
      form.append("genre", draft.genre);
      if (draft.episodes) form.append("episodes", draft.episodes);
      form.append("minutes_min", draft.minutesMin || "2");
      form.append("minutes_max", draft.minutesMax || "5");
      form.append("ratio", draft.ratio);
      form.append("keeps", draft.keeps);
      const created = await hibtvApi.createProject(form);
      selectProject(created.id);
      await refreshProjects();
      setOpen(false);
      setDraft(EMPTY_DRAFT);
      const d = created.detected || {};
      toast(
        `已识别：${d.episodes || 0} 集 · ${d.scenes || 0} 场 · ${d.lines || 0} 条台词 · ` +
          `${d.cast || 0} 角色 / ${d.scene || 0} 场景 / ${d.prop || 0} 道具`,
      );
      navigate("/hibtv/outline/inner");
    } catch (err) {
      toast(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    if (!window.confirm("删除该项目？文档、资产与成片记录都会一并删除。")) return;
    try {
      await hibtvApi.deleteProject(id);
      await refreshProjects();
      toast("项目已删除");
    } catch (err) {
      toast(errText(err));
    }
  };

  return (
    <Stage>
      <div className="pagewrap">
        <PageHead
          eyebrow="PROJECTS · 短剧项目"
          title="项目"
          lede="这里只做两件事：看项目、建项目。第一版只支持一种建项方式——上传已经定稿的剧本与美术设定；hibtv 不改编、不生成剧本，只把它拆成能开工的资产。"
          right={
            <button className="btn gold" onClick={() => setOpen(true)}>
              ＋ 新建短剧项目
            </button>
          }
        />

        <div className="toolbar" style={{ marginTop: 20 }}>
          <span className="lab">共 {projects.length} 个项目</span>
          <span className="chip">按最近编辑</span>
          <span className="sp" />
          <span className="tiny dim">
            模型固定：文本 deepseek · 图片 Qwen-Image · 视频 Minimax-h3
          </span>
        </div>

        <div className="plist">
          {projects.map((p) => (
            <div
              key={p.id}
              className={`prow ${cur?.id === p.id ? "cur" : ""}`}
              onClick={() => {
                selectProject(p.id);
                void refreshProject();
                navigate("/hibtv/overview");
              }}
            >
              <span className="th">剧</span>
              <span>
                <span className="nm">{p.name}</span>
                <span className="meta">
                  {p.genre || "未设题材"} · {p.episodes} 集 · {p.aspectRatio} · {p.updatedAt || p.createdAt} 编辑
                </span>
              </span>
              <span className="stage">{stageText(p)}</span>
              <span className="num">
                {(p.assetCount.cast || 0)} 角色 · {(p.assetCount.scene || 0) + (p.assetCount.prop || 0)} 场景道具
              </span>
              <span className="go">打开 ›</span>
              <span
                className="link tiny dim"
                onClick={(e) => {
                  e.stopPropagation();
                  void remove(p.id);
                }}
              >
                删除
              </span>
            </div>
          ))}
          <div className="pnew" onClick={() => setOpen(true)}>
            <span className="th">＋</span>
            <span>
              <span className="nm">新建短剧项目</span>
              <span className="meta">
                上传剧本 + 美术设定 → 自动识别集 / 场 / 角色 / 场景 / 道具 → 先出大纲
              </span>
            </span>
            <span className="go">选择文件 ›</span>
          </div>
        </div>

        {projects.length === 0 && (
          <div style={{ marginTop: 18 }}>
            <Empty
              glyph="剧"
              title="还没有短剧项目"
              sub="上传已经定稿的剧本（.md / .txt）与美术设定，hibtv 会把它拆成大纲、角色卡、场景与道具、节拍表、分镜。"
            >
              <button className="btn gold" onClick={() => setOpen(true)}>
                上传剧本开始
              </button>
            </Empty>
          </div>
        )}

        <div className="tiny dim" style={{ marginTop: 16, lineHeight: 1.9 }}>
          口径：输入是定稿剧本，不做改编；台词逐字保留，只按标点切句与拆拍。
          <br />
          生图按张计积分、段视频按秒计积分，LLM 步骤（大纲 / 角色 / 美术 / 剧本 / 分镜）不计积分。
        </div>
      </div>

      {open && (
        <Modal
          eyebrow="NEW PROJECT · SCRIPT + ART"
          title="新建短剧项目"
          sub="上传已经定稿的剧本与美术设定。hibtv 不做改编：不拆书、不砍支线，只把已有的字变成能开工的资产——大纲骨架、角色卡、场景与道具、节拍表、分镜。解析后先出大纲，不上游提前生成下游数据。"
          onClose={() => setOpen(false)}
          footer={
            <>
              <span className="tiny dim">创建后自动投递「大纲」作业</span>
              <span className="sp" />
              <button className="btn ghost" onClick={() => setOpen(false)}>
                取消
              </button>
              <button className="btn gold" disabled={busy} onClick={() => void create()}>
                {busy ? "创建中…" : "初始化并创建大纲"}
              </button>
            </>
          }
        >
          <div className="row2" style={{ marginBottom: 14 }}>
            <label className={`upbox ${draft.script ? "filled" : ""}`}>
              {draft.script ? (
                <>
                  <div className="fn">
                    剧本 <span className="chip gold tiny" style={{ height: 18 }}>必填</span>
                  </div>
                  <div className="st">
                    {draft.script.name}
                    <br />
                    {Math.max(1, Math.round(draft.script.size / 1024))} KB
                  </div>
                  <div className="chg">· 点击更换</div>
                </>
              ) : (
                <>
                  <div className="t">上传剧本</div>
                  <div className="s">建议 .md / .txt；按「第 N 集 / 场号 / 角色：台词」识别</div>
                </>
              )}
              <input
                type="file"
                accept=".md,.markdown,.txt"
                style={{ display: "none" }}
                onChange={(e) => setDraft({ ...draft, script: e.target.files?.[0] || null })}
              />
            </label>
            <label className={`upbox ${draft.art ? "filled" : ""}`}>
              {draft.art ? (
                <>
                  <div className="fn">
                    美术设定 <span className="chip gold tiny" style={{ height: 18 }}>必填</span>
                  </div>
                  <div className="st">
                    {draft.art.name}
                    <br />
                    {Math.max(1, Math.round(draft.art.size / 1024))} KB
                  </div>
                  <div className="chg">· 点击更换</div>
                </>
              ) : (
                <>
                  <div className="t">上传美术设定</div>
                  <div className="s">人物 / 服装 / 场景 / 道具 / 特效设定集；按标题层级识别条目</div>
                </>
              )}
              <input
                type="file"
                accept=".md,.markdown,.txt"
                style={{ display: "none" }}
                onChange={(e) => setDraft({ ...draft, art: e.target.files?.[0] || null })}
              />
            </label>
          </div>
          <div className={`tiny ${draft.script && draft.art ? "same" : "dim"}`} style={{ marginTop: -6, marginBottom: 12 }}>
            {detectedLine}
          </div>

          <div className="row2">
            <div className="field">
              <div className="label">项目名</div>
              <input
                className="input"
                value={draft.name}
                placeholder="留空则用剧本文件名"
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </div>
            <div className="field">
              <div className="label">题材</div>
              <input
                className="input"
                value={draft.genre}
                placeholder="都市情感 / 逆袭"
                onChange={(e) => setDraft({ ...draft, genre: e.target.value })}
              />
            </div>
          </div>
          <div className="row2">
            <div className="field">
              <div className="label">总集数 <span className="auto">留空按剧本识别</span></div>
              <input
                className="input"
                value={draft.episodes}
                placeholder="自动"
                onChange={(e) => setDraft({ ...draft, episodes: e.target.value })}
              />
            </div>
            <div className="field">
              <div className="label">单集时长范围 <span className="dim">默认 2–5 分钟</span></div>
              <div className="inline">
                <input
                  className="input"
                  style={{ width: 64 }}
                  value={draft.minutesMin}
                  onChange={(e) => setDraft({ ...draft, minutesMin: e.target.value })}
                />
                <span className="dim">—</span>
                <input
                  className="input"
                  style={{ width: 64 }}
                  value={draft.minutesMax}
                  onChange={(e) => setDraft({ ...draft, minutesMax: e.target.value })}
                />
                <span className="tiny dim">分钟</span>
              </div>
            </div>
          </div>
          <div className="field">
            <div className="label">画面比例</div>
            <div className="inline">
              <span
                className={`chip i ${draft.ratio === "9:16" ? "gold" : ""}`}
                onClick={() => setDraft({ ...draft, ratio: "9:16" })}
              >
                9:16 竖屏 · 短剧默认
              </span>
              <span
                className={`chip i ${draft.ratio === "16:9" ? "gold" : ""}`}
                onClick={() => setDraft({ ...draft, ratio: "16:9" })}
              >
                16:9 横屏
              </span>
            </div>
            <div className="tiny dim" style={{ marginTop: 6 }}>
              这一项会写进所有锚点图、分镜图与成片，后期不再单张改
            </div>
          </div>
          <div className="field">
            <div className="label">必须保留的名场面 / 场次 <span className="dim">可选</span></div>
            <textarea
              className="textarea"
              value={draft.keeps}
              placeholder="例：寿宴上叶辰开口借一百万、被萧老太太当众羞辱的那场戏"
              onChange={(e) => setDraft({ ...draft, keeps: e.target.value })}
            />
            <div className="tiny dim" style={{ marginTop: 5 }}>
              写在这里的场次不会被合场、不会被删减；剧本里没有的场次不会凭空生出来
            </div>
          </div>
          <div className="toolbar" style={{ margin: 0 }}>
            <span className="lab">本次解析</span>
            <span className="chip">剧本解析</span>
            <span className="chip">美术设定解析</span>
            <span className="sp">
              <span className="tiny dim">模型服务固定，不可更改</span>
            </span>
          </div>
        </Modal>
      )}
    </Stage>
  );
}
