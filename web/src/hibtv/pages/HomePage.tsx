/** 首页：项目列表 + 新建项目（输入是**小说/梗概**，不是定稿剧本）。
 *
 * 口径（plan §3.2）：平台只提交「业务意图 + 上游产物引用」，正文先落盘再给绝对路径，
 * 拼 prompt / 跑 skill / 写产物全部由 codex 网关做。所以这里只收：
 * 正文（粘贴或上传）+ 集数 / 单集秒 / 比例 / 题材 / 必须保留。
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { hibtvApi } from "../api";
import { Stage } from "../HibtvLayout";
import { CodexLight, useCodexGate } from "../components/CodexLight";
import { Empty, Modal, PageHead } from "../components/ui";
import { errText, useHibtv } from "../state";

interface Draft {
  novel: File | null;
  text: string;
  name: string;
  genre: string;
  episodes: string;
  seconds: string;
  ratio: string;
  keeps: string;
  autoOutline: boolean;
}

const EMPTY_DRAFT: Draft = {
  novel: null,
  text: "",
  name: "",
  genre: "",
  episodes: "3",
  seconds: "90",
  ratio: "9:16",
  keeps: "",
  autoOutline: true,
};

function stageText(p: { docs: Record<string, { status: string }> }): string {
  // docs 的 key 形如 outline#0 / storyboard#1；按前缀判断，别整串比较
  const has = (k: string) =>
    Object.entries(p.docs || {}).some(([key, row]) => key.split("#")[0] === k && row?.status === "done");
  if (has("storyboard")) return "已生成到：分镜";
  if (has("staging")) return "已生成到：站位";
  if (has("script")) return "已生成到：剧本";
  if (has("art")) return "已生成到：美术";
  if (has("cast")) return "已生成到：角色";
  if (has("outline")) return "已生成到：大纲 · 待生成角色";
  return "已导入：待生成大纲";
}

/** 单集秒 → 后端的分钟档（模型里 minutes 是整数分钟，取下界 / 上界各一个）。 */
function minutesOf(seconds: number): [number, number] {
  const s = Math.max(10, Math.round(seconds || 90));
  return [Math.max(1, Math.floor(s / 60)), Math.max(1, Math.ceil(s / 60))];
}

export default function HibtvHomePage() {
  const { projects, selectProject, refreshProjects, toast, project: cur } = useHibtv();
  const navigate = useNavigate();
  const gate = useCodexGate();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);

  const bodyLen = draft.text.trim().length;
  const hasNovel = bodyLen > 0 || Boolean(draft.novel);

  // 打开表单时按网关真实状态决定默认值：网关掉线还默认勾「立刻排大纲」= 默认点一个必然失败的动作
  const openForm = () => {
    setDraft({ ...EMPTY_DRAFT, autoOutline: gate.ok });
    setOpen(true);
  };

  const create = async () => {
    if (!hasNovel) {
      toast("请粘贴小说/梗概正文，或上传一个 .md / .txt 文件");
      return;
    }
    const [mMin, mMax] = minutesOf(Number(draft.seconds) || 90);
    setBusy(true);
    try {
      const created = await hibtvApi.createProject({
        novel: draft.novel,
        name: draft.name,
        text: draft.text,
        genre: draft.genre,
        episodes: Number(draft.episodes) || 3,
        minutesMin: mMin,
        minutesMax: mMax,
        ratio: draft.ratio,
        keeps: draft.keeps,
      });
      selectProject(created.id);
      await refreshProjects();
      setOpen(false);
      setDraft(EMPTY_DRAFT);
      let queued = false;
      if (draft.autoOutline) {
        if (!gate.ok) {
          toast(`项目已建；大纲没排上：${gate.why}`);
        } else {
          try {
            await hibtvApi.createJob(created.id, { kind: "outline" });
            queued = true;
          } catch (err) {
            toast(`项目已建；大纲排队失败：${errText(err)}`);
          }
        }
      }
      if (!queued) {
        toast(`项目已建：正文 ${Math.max(1, Math.round((bodyLen || (draft.novel?.size || 0)) / 1024))} KB`);
      } else {
        toast(`项目已建，大纲已排队（novel-outline 约 5–6 分钟，可在概览看 tokens 与耗时）`);
      }
      navigate("/hibtv/outline/inner");
    } catch (err) {
      toast(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    if (!window.confirm("删除该项目？文档、资产、关键帧与成片记录都会一并删除。")) return;
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
          lede="这里只做两件事：看项目、建项目。输入是小说或梗概——改编、拆集、角色/美术/剧本/站位/分镜全部由 codex 跑 skill 产出，平台只搬字段、出像素。"
          right={
            <>
              <CodexLight />
              <button className="btn gold" onClick={() => openForm()}>
                ＋ 新建短剧项目
              </button>
            </>
          }
        />

        <div className="toolbar" style={{ marginTop: 20 }}>
          <span className="lab">共 {projects.length} 个项目</span>
          <span className="chip">按最近编辑</span>
          <span className="sp" />
          <span className="tiny dim">
            文本 codex（deepseek）· 图片 Qwen-Image · 视频 Minimax-h3（ref2v，段 ≤ 13s）
          </span>
        </div>

        <div className="plist">
          {projects.map((p) => (
            <div
              key={p.id}
              className={`prow ${cur?.id === p.id ? "cur" : ""}`}
              onClick={() => {
                selectProject(p.id);
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
                {p.frameCount?.total ? ` · 关键帧 ${p.frameCount.done}/${p.frameCount.total}` : ""}
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
          <div className="pnew" onClick={() => openForm()}>
            <span className="th">＋</span>
            <span>
              <span className="nm">新建短剧项目</span>
              <span className="meta">
                粘贴 / 上传小说正文 → 大纲 → 角色 → 美术 → 参考图 → 剧本 → 站位 → 分镜 → 关键帧 → 成片
              </span>
            </span>
            <span className="go">填小说 ›</span>
          </div>
        </div>

        {projects.length === 0 && (
          <div style={{ marginTop: 18 }}>
            <Empty
              glyph="剧"
              title="还没有短剧项目"
              sub="粘贴小说正文或上传 .md / .txt，填集数与单集秒数；大纲由 codex 的 novel-outline 产出，回来自动回填项目参数。"
            >
              <button className="btn gold" onClick={() => openForm()} disabled={!gate.ok} title={gate.why}>
                输入小说开始
              </button>
              {!gate.ok && (
                <div className="tiny red" style={{ marginTop: 8 }}>
                  {gate.why}
                </div>
              )}
            </Empty>
          </div>
        )}

        <div className="tiny dim" style={{ marginTop: 16, lineHeight: 1.9 }}>
          口径：文本 7 段（大纲 / 角色 / 美术 / 参考图 / 剧本 / 站位 / 分镜）由 codex 网关跑 skill，
          平台**不拼提示词、不写骨架、不跑 node 门**，只把产物字段搬回文档。
          <br />
          像素按张 / 按秒计积分（资产单图 一资产一张；关键帧 一切一张；段视频按帧对齐后计费）；
          文本阶段本期不扣积分，但会在概览里显示 tokens 与耗时。
        </div>
      </div>

      {open && (
        <div className="layer open">
          <div className="scrim" onClick={() => setOpen(false)} />
        <Modal
          eyebrow="NEW PROJECT · novel"
          title="新建短剧项目"
          sub="输入小说或梗概。改编方向（保留什么 / 砍什么 / 合并什么）由 codex 的 novel-outline 给方案，你也可以在「必须保留」里钉死几场戏——写了就不会被合场或删减。"
          onClose={() => setOpen(false)}
          footer={
            <>
              <label className="tiny dim inline" style={{ gap: 6 }}>
                <input
                  type="checkbox"
                  checked={draft.autoOutline}
                  onChange={(e) => setDraft({ ...draft, autoOutline: e.target.checked })}
                />
                创建后立刻排「大纲」（{gate.ok ? "网关可用" : "网关不可用"}）
              </label>
              <span className="sp" />
              <button className="btn ghost" onClick={() => setOpen(false)}>
                取消
              </button>
              <button className="btn gold" disabled={busy || !hasNovel} onClick={() => void create()}>
                {busy ? "创建中…" : "创建项目"}
              </button>
            </>
          }
        >
          <div className="field" style={{ marginBottom: 12 }}>
            <div className="label">
              小说 / 梗概正文 <span className="chip gold tiny" style={{ height: 18 }}>必填</span>
              <span className="dim">粘贴或上传二选一；正文会先落盘，input 里只给绝对路径</span>
            </div>
            <textarea
              className="textarea"
              style={{ minHeight: 150 }}
              value={draft.text}
              placeholder="把小说正文或分集梗概贴在这里（几十万字也行，不会内联进 prompt）"
              onChange={(e) => setDraft({ ...draft, text: e.target.value, novel: null })}
            />
            <div className="inline" style={{ marginTop: 6, gap: 8 }}>
              <label className={`upbox ${draft.novel ? "filled" : ""}`} style={{ padding: "8px 10px" }}>
                <div className="t" style={{ fontSize: 12 }}>
                  {draft.novel ? `已选：${draft.novel.name}` : "或上传 .md / .txt"}
                </div>
                <div className="s">
                  {draft.novel
                    ? `${Math.max(1, Math.round(draft.novel.size / 1024))} KB · 点击更换`
                    : "上传会顶掉上面的粘贴内容"}
                </div>
                <input
                  type="file"
                  accept=".md,.markdown,.txt"
                  style={{ display: "none" }}
                  onChange={(e) => {
                    const file = e.target.files?.[0] || null;
                    setDraft({ ...draft, novel: file, text: file ? "" : draft.text });
                  }}
                />
              </label>
              <span className="tiny dim">
                已输入 {bodyLen ? `${Math.max(1, Math.round(bodyLen / 1024))} KB（${bodyLen} 字）` : "0 字"}
              </span>
            </div>
          </div>

          <div className="row2">
            <div className="field">
              <div className="label">项目名</div>
              <input
                className="input"
                value={draft.name}
                placeholder="留空则用文件名 / 未命名短剧"
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </div>
            <div className="field">
              <div className="label">题材</div>
              <input
                className="input"
                value={draft.genre}
                placeholder="男频逆袭 / 都市情感"
                onChange={(e) => setDraft({ ...draft, genre: e.target.value })}
              />
            </div>
          </div>
          <div className="row2">
            <div className="field">
              <div className="label">集数</div>
              <input
                className="input"
                value={draft.episodes}
                placeholder="3"
                onChange={(e) => setDraft({ ...draft, episodes: e.target.value })}
              />
            </div>
            <div className="field">
              <div className="label">
                单集秒数 <span className="dim">写进大纲 task；段长上限仍是 13s</span>
              </div>
              <input
                className="input"
                value={draft.seconds}
                placeholder="90"
                onChange={(e) => setDraft({ ...draft, seconds: e.target.value })}
              />
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
              写进所有资产图、关键帧与成片；关键帧落 768×1344（竖屏）/ 1344×768（横屏）
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
              写在这里的场次不会被合场、不会被删减；也会作为「必须保留」写进大纲 task
            </div>
          </div>
          <div className="toolbar" style={{ margin: 0 }}>
            <span className="lab">建完会发生什么</span>
            <span className="chip">正文落盘 source.txt</span>
            <span className="chip">novel-outline 跑 skill</span>
            <span className="sp">
              <span className="tiny dim">网关不可达就是不可用，没有本地兜底</span>
            </span>
          </div>
        </Modal>
        </div>
      )}
    </Stage>
  );
}
