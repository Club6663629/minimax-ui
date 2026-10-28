/** 角色卡 / 场景卡 / 道具卡：**一资产一张单图** + 提示词面板。
 *
 * 口径（别再退回多视图）：
 * - 提示词直接用 skill 产出的 `image.prompt`（反向词 `image.negativePrompt`），平台不追加约束；
 * - 尺寸按类型固定：cast 896×1152 / scene 1280×720 / prop 1024×1024（后端可配）；
 * - 「重出」只换这一张，永不产生派生视图 / 裁格 / 锚点。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { hibtvApi, mediaUrl } from "../api";
import { errText, useHibtv } from "../state";
import type { HibtvAsset, RefLayerDoc } from "../types";
import { InlineEdit, MediaActs, Plate } from "./ui";

/** 取某一类资产；作业一结束（revision 变化）自动重取。 */
export function useAssets(kind = "") {
  const { project, revision, toast } = useHibtv();
  const [assets, setAssets] = useState<HibtvAsset[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!project) {
      setAssets([]);
      return;
    }
    setLoading(true);
    try {
      setAssets(await hibtvApi.listAssets(project.id, kind));
    } catch (err) {
      toast(errText(err));
    } finally {
      setLoading(false);
    }
  }, [project, kind, toast]);

  useEffect(() => {
    void load();
  }, [load, revision]);

  return { assets, loading, reload: load, setAssets };
}

const PLATE_KIND: Record<string, "person" | "scene" | "prop"> = {
  cast: "person",
  scene: "scene",
  prop: "prop",
};

const KIND_LABEL: Record<string, string> = { cast: "角色", scene: "场景", prop: "道具" };

function sizeText(size?: number[]): string {
  return size && size.length === 2 ? `${size[0]}×${size[1]}` : "";
}

function ratioOf(size?: number[]): string | undefined {
  if (!size || size.length !== 2 || !size[0] || !size[1]) return undefined;
  return `${size[0]} / ${size[1]}`;
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const { toast } = useHibtv();
  if (!value) return null;
  return (
    <div className="tiny" style={{ marginTop: 6 }}>
      <span className="dim">{label}：</span>
      <span className="mono" style={{ wordBreak: "break-word" }}>
        {value}
      </span>
      <span
        className="link"
        style={{ marginLeft: 8 }}
        onClick={() => {
          void navigator.clipboard?.writeText(value).then(
            () => toast(`已复制${label}`),
            () => toast("浏览器不允许复制，请手动选中"),
          );
        }}
      >
        复制
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ 改图抽屉 */
function RerollDrawer({
  asset,
  onClose,
  onSubmit,
}: {
  asset: HibtvAsset;
  onClose: () => void;
  onSubmit: (hint: string) => void;
}) {
  const [hint, setHint] = useState("");
  return (
    <div className="drawer">
      <div className="drawer-hd">
        <div>
          <div className="tiny dim">
            改图 · {asset.name} #{asset.code}
          </div>
          <div className="t">给一句修改要求</div>
        </div>
        <span className="x" onClick={onClose}>
          ✕
        </span>
      </div>
      <div className="drawer-bd">
        <Plate
          kind={PLATE_KIND[asset.kind] || "person"}
          label={asset.name}
          view={`当前主图 · v${asset.version || 1}`}
          src={asset.main?.url}
          ratio={ratioOf(asset.size)}
        />
        <div className="tiny dim" style={{ margin: "10px 0 14px" }}>
          出图提示词不改，只把这句话追加成额外要求；旧主图当参考图（只重出这一张）。
        </div>
        <div className="field">
          <div className="label">要改的地方</div>
          <textarea
            className="textarea"
            value={hint}
            placeholder="例：换成深蓝色外套；脸不要动"
            onChange={(e) => setHint(e.target.value)}
          />
        </div>
        <CopyRow label="出图提示词" value={asset.prompt} />
      </div>
      <div className="drawer-ft">
        <button className="btn ghost" onClick={onClose}>
          取消
        </button>
        <span style={{ flex: 1 }} />
        <button className="btn gold" disabled={!hint.trim()} onClick={() => onSubmit(hint.trim())}>
          重出这一张
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ 分层抽屉（角色） */
function LayersDrawer({ asset, onClose }: { asset: HibtvAsset; onClose: () => void }) {
  const refs: RefLayerDoc | undefined = asset.data?.refs;
  const layers = Object.entries(refs?.layers || {});
  const outfits = Object.entries(refs?.outfits || {});
  return (
    <div className="drawer">
      <div className="drawer-hd">
        <div>
          <div className="tiny dim">分层底稿 · {asset.code}</div>
          <div className="t">{asset.name} · 中英对照</div>
        </div>
        <span className="x" onClick={onClose}>
          ✕
        </span>
      </div>
      <div className="drawer-bd">
        {!refs && <div className="tiny dim">这一版还没跑「参考图」阶段（asset.json 才会有分层）。</div>}
        {layers.map(([key, row]) => (
          <div key={key} className="card" style={{ marginBottom: 8 }}>
            <div className="card-hd">
              <span className="t">{key}</span>
              <span className={`chip ${row.source === "stated" ? "green" : "ghost"}`}>
                {row.source === "stated" ? "原文明写" : "推断"}
              </span>
            </div>
            <div className="card-bd tiny">
              <div>{row.en || "—"}</div>
              <div className="dim">{row.zh || "—"}</div>
            </div>
          </div>
        ))}
        {outfits.map(([key, outfit]) => (
          <div key={key} className="card" style={{ marginBottom: 8 }}>
            <div className="card-hd">
              <span className="t">outfit · {key}</span>
            </div>
            <div className="card-bd tiny">
              {["top", "bottom", "details"].map((part) => {
                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                const row = (outfit as any)?.[part];
                if (!row) return null;
                return (
                  <div key={part}>
                    <span className="dim">{part}：</span>
                    {row.en || "—"} <span className="dim">{row.zh || ""}</span>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
        <div className="note">
          分层只作核对与改提示词的底稿；改完要重新出这一张图（平台不会拿分层再拼一遍提示词）。
        </div>
      </div>
      <div className="drawer-ft">
        <span style={{ flex: 1 }} />
        <button className="btn" onClick={onClose}>
          关闭
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ 卡片 */
export function AssetCard({ asset, onChanged }: { asset: HibtvAsset; onChanged: () => void }) {
  const { project, toast, openLayer, closeLayer } = useHibtv();
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const plateKind = PLATE_KIND[asset.kind] || "person";
  const kindLabel = KIND_LABEL[asset.kind] || asset.kind;
  const mainSrc = asset.main?.url || "";
  const drawing = asset.status === "drawing";
  const review = asset.status === "review";
  const ratio = ratioOf(asset.size);
  const prompt = asset.prompt || "";
  const image = asset.data?.image || {};
  const tag =
    asset.kind === "cast"
      ? asset.data?.importance || "角色"
      : asset.kind === "prop"
        ? "道具"
        : asset.data?.primary
          ? "主场景"
          : "场景";

  const patch = async (body: { name?: string; hidden?: boolean; data?: Record<string, unknown> }) => {
    if (!project) return;
    try {
      await hibtvApi.patchAsset(project.id, asset.id, body);
      onChanged();
    } catch (err) {
      toast(errText(err));
    }
  };

  const draw = async (action: "draw" | "regen" | "confirm", hint?: string) => {
    if (!project) return;
    setBusy(true);
    try {
      const res = await hibtvApi.assetImage(project.id, asset.id, { action, hint });
      if (action === "confirm") toast(`已接受 v${asset.version}`);
      else if ("kind" in res && res.kind === "images") {
        toast(`已排队出图：${asset.name}（1 张 ${sizeText(asset.size)}）`);
      }
      onChanged();
    } catch (err) {
      toast(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const upload = async (file: File) => {
    if (!project) return;
    setBusy(true);
    try {
      await hibtvApi.uploadAsset(project.id, asset.id, file);
      toast(`已替换主图：${asset.name}`);
      onChanged();
    } catch (err) {
      toast(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const bigView = () => {
    openLayer(
      <div className="modal" style={{ width: "min(980px,96vw)", background: "#0d0b09" }}>
        <div className="modal-hd">
          <div>
            <div className="eyebrow">
              {asset.kind.toUpperCase()} · {asset.code} · {sizeText(asset.size)}
            </div>
            <div className="h1" style={{ fontSize: 18 }}>
              {asset.name}
            </div>
            <div className="tiny mut" style={{ marginTop: 4 }}>
              {mainSrc ? "一资产一张图；要换就重出这一张或上传。" : "还没有图，点「出图」生成这一张。"}
            </div>
          </div>
          <span className="x" onClick={closeLayer}>
            ✕
          </span>
        </div>
        <div className="modal-bd">
          {mainSrc ? (
            <img
              src={mediaUrl(mainSrc)}
              alt={asset.name}
              style={{ width: "100%", borderRadius: 6, border: "1px solid var(--line-3)" }}
            />
          ) : (
            <div className="tiny dim" style={{ padding: "32px 0", textAlign: "center" }}>
              这张还没有图。
            </div>
          )}
          <CopyRow label="出图提示词" value={prompt} />
          <CopyRow label="反向词" value={asset.negative} />
        </div>
        <div className="modal-ft">
          <span className="tiny dim">{asset.code}</span>
          <span className="sp" />
          <button className="btn ghost" onClick={closeLayer}>
            关闭
          </button>
        </div>
      </div>,
    );
  };

  const onAct = (act: string) => {
    if (act === "regen") void draw(mainSrc ? "regen" : "draw");
    if (act === "upload") fileRef.current?.click();
    if (act === "reroll") {
      openLayer(
        <RerollDrawer
          asset={asset}
          onClose={closeLayer}
          onSubmit={(hint) => {
            closeLayer();
            void draw("regen", hint);
          }}
        />,
      );
    }
    if (act === "layers") {
      openLayer(<LayersDrawer asset={asset} onClose={closeLayer} />);
    }
    if (act === "del") {
      void patch({ hidden: true }).then(() => {
        toast(`已隐藏：${asset.name}（可在素材库恢复）`, {
          label: "撤销",
          fn: () => void patch({ hidden: false }),
        });
      });
    }
  };

  const status = drawing ? (
    <span className="chip gold">出图中…</span>
  ) : review ? (
    <>
      <span className="chip red">v{asset.version} 待确认</span>
      <button className="btn xs" disabled={busy} onClick={() => void draw("confirm")}>
        接受 v{asset.version}
      </button>
    </>
  ) : mainSrc ? (
    <>
      <span className="chip green">v{asset.version} · 已出图</span>
      <span className="chip">{sizeText(asset.size)}</span>
    </>
  ) : (
    <>
      <span className="chip ghost">还没有图</span>
      <button className="btn xs" disabled={busy} onClick={() => void draw("draw")}>
        出图
      </button>
    </>
  );

  const fileInput = (
    <input
      ref={fileRef}
      type="file"
      accept=".png,.jpg,.jpeg,.webp"
      style={{ display: "none" }}
      onChange={(e) => {
        const file = e.target.files?.[0];
        e.target.value = "";
        if (file) void upload(file);
      }}
    />
  );

  if (asset.kind === "cast") {
    return (
      <div className="cast-card">
        <div className="cast-media">
          <div className="cast-anchor" onClick={bigView} aria-label={`点击看 ${asset.name} 大图`}>
            <Plate
              kind="person"
              label={asset.name}
              view={mainSrc ? `${kindLabel} · ${sizeText(asset.size)}` : "还没有图"}
              src={mainSrc}
              ratio={ratio || "896 / 1152"}
            />
            {fileInput}
            <MediaActs onAct={onAct} />
          </div>
        </div>
        <div className="cast-body">
          <div className="cast-name">
            <span
              onClick={(e) => e.stopPropagation()}
              title="双击改名"
              style={{ display: "inline-flex", gap: 6, alignItems: "baseline" }}
            >
              <InlineEdit value={asset.name} onSave={(v) => void patch({ name: v })} />
            </span>
            <span className="tag">{tag}</span>
            <span className="cast-alias">{asset.code}</span>
            <span className="pin" title="看大图" onClick={bigView}>
              ⤢
            </span>
          </div>
          <div className="cast-desc">{asset.data?.oneLiner || ""}</div>
          <div className="tiny dim">出图提示词（image.prompt 原文）</div>
          <div className="promptbox">
            <InlineEdit
              multiline
              value={prompt}
              placeholder="（skill 没给提示词；双击补一句）"
              onSave={(v) => void patch({ data: { image: { ...image, prompt: v } } })}
            />
          </div>
          <CopyRow label="反向词" value={asset.negative} />
          <div className="inline" style={{ gap: 6, flexWrap: "wrap", marginTop: 8 }}>
            {status}
          </div>
          <div className="cast-voice">
            <span className="up">◍ 音色参考</span>
            <span className="ref">{asset.data?.voice?.timbre || asset.data?.voice?.gender || "未设"}</span>
          </div>
          <div className="art-foot" style={{ borderTop: "none", paddingTop: 0 }}>
            <span className="tiny dim">{prompt.split(/\s+/).filter(Boolean).length} 词</span>
            <span className="link" onClick={(e) => { e.stopPropagation(); onAct("layers"); }}>
              分层底稿
            </span>
            <span className="link" onClick={(e) => { e.stopPropagation(); void draw(mainSrc ? "regen" : "draw"); }}>
              {mainSrc ? "重出这一张" : "出图"}
            </span>
          </div>
          {asset.legacyViews?.length ? (
            <div className="tiny dim" style={{ marginTop: 4 }}>
              旧版多视图 {asset.legacyViews.length} 张（只读保留，不再参与流程）
            </div>
          ) : null}
          <div className="cast-foot" onClick={bigView}>
            画像 · 音色 · 提示词 · 分层 →
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="art-card">
      <div className="art-anchor" onClick={bigView} aria-label={`点击看 ${asset.name} 大图`}>
        <Plate
          kind={plateKind}
          label={asset.name}
          view={mainSrc ? `${kindLabel} · ${sizeText(asset.size)}` : "还没有图"}
          src={mainSrc}
          ratio={ratio}
        />
        {fileInput}
        <MediaActs onAct={onAct} />
      </div>
      <div className="art-body">
        <div className="cast-name">
          <span className="mid">{asset.code}</span>
          <InlineEdit value={asset.name} onSave={(v) => void patch({ name: v })} />
          <span className="tag">{tag}</span>
          <span className="pin" title="看大图" onClick={bigView}>
            ⤢
          </span>
        </div>
        <div className="cast-desc">{asset.data?.summary || asset.data?.function || ""}</div>
        <div className="tiny dim">出图提示词</div>
        <div className="promptbox">
          <InlineEdit
            multiline
            value={prompt}
            placeholder="（skill 没给提示词；双击补一句）"
            onSave={(v) => void patch({ data: { image: { ...image, prompt: v } } })}
          />
        </div>
        <div className="art-foot">
          {status}
          <span className="link" onClick={() => void draw(mainSrc ? "regen" : "draw")}>
            {mainSrc ? "重出这一张" : "出图"}
          </span>
        </div>
      </div>
    </div>
  );
}
