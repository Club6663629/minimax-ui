/** 角色卡 / 场景卡 / 道具卡：一张多视图设定稿直接上卡（无分格展示）+ 图上四图标 + 提示词区。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { hibtvApi, mediaUrl } from "../api";
import { errText, useHibtv } from "../state";
import type { HibtvAsset } from "../types";
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
  const anchor = asset.views[asset.anchor] || {};
  const sheet = asset.sheet || {};
  return (
    <div className="drawer">
      <div className="drawer-hd">
        <div>
          <div className="tiny dim">
            改图 · {asset.name} #{asset.code}
          </div>
          <div className="t">给一句话修改要求</div>
        </div>
        <span className="x" onClick={onClose}>
          ✕
        </span>
      </div>
      <div className="drawer-bd">
        <Plate
          kind={PLATE_KIND[asset.kind] || "person"}
          label={asset.name}
          view={`当前设定稿 · v${asset.version || 1}`}
          src={sheet.url || anchor.url}
          ratio="16 / 9"
        />
        <div className="tiny dim" style={{ margin: "10px 0 14px" }}>
          整张设定稿作为参考重画：{asset.defs.length} 格一起更新，不会出现「只改一格」。
        </div>
        <div className="field">
          <div className="label">要改的地方</div>
          <textarea
            className="textarea"
            value={hint}
            placeholder="例：把外套换成深蓝色，领口敲开一颗扣子；脸不要动"
            onChange={(e) => setHint(e.target.value)}
          />
        </div>
        <div className="note">
          会把整张设定稿重画一遍（{asset.defs.length} 格），下游引用它的镜头标记「素材已更新」，但不自动重跑视频。
        </div>
      </div>
      <div className="drawer-ft">
        <button className="btn ghost" onClick={onClose}>
          取消
        </button>
        <span style={{ flex: 1 }} />
        <button className="btn gold" disabled={!hint.trim()} onClick={() => onSubmit(hint.trim())}>
          生成新版本
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ 卡片 */
export function AssetCard({
  asset,
  onChanged,
}: {
  asset: HibtvAsset;
  onChanged: () => void;
}) {
  const { project, toast, openLayer, closeLayer } = useHibtv();
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const plateKind = PLATE_KIND[asset.kind] || "person";
  const anchor = asset.anchor;
  const anchorMeta = asset.views[anchor] || {};
  const sheet = asset.sheet || {};
  // 一张设定稿就是唯一产物；没有它时才回退到旧数据里的锚点图
  const sheetSrc = sheet.url || anchorMeta.url;
  const hasSheet = Boolean(sheet.url || sheet.path);
  const gridLabel = asset.kind === "cast" ? "四视图" : asset.kind === "scene" ? "四宫格" : asset.kind === "prop" ? "三视图" : "多视图";
  // 出图画幅由后端给（默认 2752×1536 = Qwen-Image-2.1 官方 16:9 2K）
  const sheetSize = (asset.sheetSpec?.size || []).join("×");
  // 版式描述以后端格子表（从左到右/从上到下）为准，避免和 defs 陈列顺序不一致
  const sheetBrief = asset.sheetSpec?.kinds?.[asset.kind]?.brief;
  const review = asset.status === "review";
  const drawing = asset.status === "drawing";
  const prompt: string = asset.data?.image?.prompt || "";
  const voice: string = asset.data?.voice?.timbre || "";
  // 道具/场景的 tag 不再复用 summary（正文已经显示过一句，标题行再重复一次）。
  const tag =
    asset.kind === "cast"
      ? asset.data?.persona?.identity || asset.data?.importance || "角色"
      : asset.data?.primary
        ? "主场景"
        : asset.kind === "prop" ? "道具" : "场景";

  const patch = async (body: { name?: string; hidden?: boolean; data?: Record<string, unknown> }) => {
    if (!project) return;
    try {
      await hibtvApi.patchAsset(project.id, asset.id, body);
      onChanged();
    } catch (err) {
      toast(errText(err));
    }
  };

  const draw = async (action: "sheet" | "anchor" | "views" | "all" | "confirm", hint?: string) => {
    if (!project) return;
    setBusy(true);
    try {
      const res = await hibtvApi.assetImage(project.id, asset.id, {
        action,
        hint,
        views: action === "views" ? asset.defs.slice(1).map(([v]) => v) : undefined,
      });
      if ("kind" in res && res.kind === "images") {
        const what = action === "sheet" ? `多视图设定稿（${asset.defs.length} 格自动裁好）`
          : action === "anchor" ? "锚点" : `${asset.defs.length - 1} 张派生`;
        toast(`已排队出图：${asset.name}（${what}）`);
      } else if (action === "confirm") {
        toast(`已接受 v${asset.version}`);
      }
      onChanged();
    } catch (err) {
      toast(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const upload = async (file: File, view: string) => {
    if (!project) return;
    setBusy(true);
    try {
      await hibtvApi.uploadAsset(project.id, asset.id, file, view);
      toast(view === "__sheet"
        ? `已替换整张设定稿：${asset.name}（格子已重裁）`
        : `已替换：${asset.name} · ${view === anchor ? "锚点" : view}`);
      onChanged();
    } catch (err) {
      toast(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const bigView = () => {
    openLayer(
      <div className="modal" style={{ width: "min(1180px,96vw)", background: "#0d0b09" }}>
        <div className="modal-hd">
          <div>
            <div className="eyebrow">
              {asset.kind.toUpperCase()} · {asset.code} · {gridLabel}设定稿
            </div>
            <div className="h1" style={{ fontSize: 18 }}>
              {asset.name}
            </div>
            <div className="tiny mut" style={{ marginTop: 4 }}>
              {hasSheet
                ? `一张 16:9 设定稿${sheetSize ? ` · ${sheetSize}px` : ""} · ${
                    sheetBrief ||
                    `${asset.defs.length} 个视角：${asset.defs.map(([, label]) => label).join(" / ")}`
                  }。改这张即同步全部。`
                : "还没有设定稿；下面是旧数据里的单张锚点图。"}
            </div>
          </div>
          <span className="x" onClick={closeLayer}>
            ✕
          </span>
        </div>
        <div className="modal-bd">
          {sheetSrc ? (
            <img
              src={mediaUrl(sheetSrc)}
              alt={`${asset.name} ${gridLabel}设定稿`}
              style={{ width: "100%", borderRadius: 6, border: "1px solid var(--line-3)" }}
            />
          ) : (
            <div className="tiny dim" style={{ padding: "32px 0", textAlign: "center" }}>
              这张还没有图；点卡片上的「出{gridLabel}」就能一次生成整张设定稿。
            </div>
          )}
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
    if (act === "regen") void draw("sheet");
    if (act === "upload") fileRef.current?.click();
    if (act === "reroll") {
      openLayer(
        <RerollDrawer
          asset={asset}
          onClose={closeLayer}
          onSubmit={(hint) => {
            closeLayer();
            void draw("sheet", hint);
          }}
        />,
      );
    }
    if (act === "del") {
      void patch({ hidden: true }).then(() => {
        toast(`已删除：${asset.name}`, {
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
  ) : sheetSrc ? (
    <>
      <span className="chip green">v{asset.version} · 已出图</span>
      <span className="chip">
        {hasSheet ? `${asset.defs.length} 格 · 一张设定稿` : "单张锚点（旧数据）"}
      </span>
    </>
  ) : (
    <>
      <span className="chip ghost">还没有图</span>
      <button className="btn xs" disabled={busy} onClick={() => void draw("sheet")}>
        出{gridLabel}
      </button>
    </>
  );

  // 四图标必须是 .plate 的兄弟节点（原型结构）：.plate 有 overflow:hidden，
  // 放进去会把图标的文字浮层裁掉。
  const fileInput = (
    <input
      ref={fileRef}
      type="file"
      accept=".png,.jpg,.jpeg,.webp"
      style={{ display: "none" }}
      onChange={(e) => {
        const file = e.target.files?.[0];
        e.target.value = "";
        if (file) void upload(file, "__sheet");
      }}
    />
  );
  // 斜纹只标「这张图本身过期」：待确认（review）用红色 chip 表达。
  // 设定稿是唯一产物，它有图就永远不会“整张过期”；旧数据的锚点才看 stale。
  const staleMark = !hasSheet && anchorMeta.stale && <div className="stale" />;

  if (asset.kind === "cast") {
    return (
      <div className="cast-card">
        <div className="cast-media">
          <div
            className={`cast-anchor ${hasSheet ? "sheet" : ""}`}
            onClick={bigView}
            aria-label={`点击看${gridLabel}设定稿大图`}
          >
            <Plate
              kind="person"
              label={asset.name}
              view={hasSheet ? `多视图设定稿 · ${asset.defs.length} 格` : "正面全身 · 锚点"}
              src={sheetSrc}
              extra={!hasSheet && anchorMeta.stale ? "stale" : ""}
            >
              {staleMark}
            </Plate>
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
            <span className="cast-alias">
              {asset.code}
              {asset.data?.aliases?.length ? ` · ${asset.data.aliases.join(" / ")}` : ""}
            </span>
            <span className="pin" title={`看${gridLabel}设定稿`} onClick={bigView}>
              ⤢
            </span>
          </div>
          <div className="cast-desc">{asset.data?.oneLiner || ""}</div>
          <div className="tiny dim">角色提示词</div>
          <div className="promptbox">
            <InlineEdit
              multiline
              value={prompt}
              placeholder="（还没有提示词；双击补一句）"
              onSave={(v) => void patch({ data: { image: { ...(asset.data?.image || {}), prompt: v } } })}
            />
          </div>
          <div className="inline" style={{ gap: 6, flexWrap: "wrap" }}>
            {status}
          </div>
          <div className="cast-voice">
            <span className="up">◍ 音色参考</span>
            <span className="ref">{voice || "未设"}</span>
          </div>
          <div className="art-foot" style={{ borderTop: "none", paddingTop: 0 }}>
            <span className="tiny dim">
              {hasSheet ? `${gridLabel} · ${asset.defs.length} 格一张` : "还没有设定稿"}
            </span>
            <span className="link" onClick={(e) => { e.stopPropagation(); void draw("sheet"); }}>
              重出{gridLabel}
            </span>
          </div>
          <div className="cast-foot" onClick={bigView}>
            画像 · 弧光 · 关系 · 音色 · {gridLabel}设定稿 →
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="art-card">
      <div
        className={`art-anchor ${hasSheet ? "sheet" : ""}`}
        onClick={bigView}
        aria-label={`点击看${gridLabel}设定稿大图`}
      >
        <Plate
          kind={plateKind}
          label={asset.name}
          view={hasSheet ? `多视图设定稿 · ${asset.defs.length} 格` : "正面 · 锚点"}
          src={sheetSrc}
          extra={!hasSheet && anchorMeta.stale ? "stale" : ""}
        >
          {staleMark}
        </Plate>
        {fileInput}
        <MediaActs onAct={onAct} />
      </div>
      <div className="art-body">
        <div className="cast-name">
          <span className="mid">{asset.code}</span>
          <InlineEdit value={asset.name} onSave={(v) => void patch({ name: v })} />
          <span className="tag">{tag}</span>
          <span className="pin" title={`看${gridLabel}设定稿`} onClick={bigView}>
            ⤢
          </span>
        </div>
        <div className="cast-desc">{asset.data?.summary || asset.data?.function || ""}</div>
        <div className="tiny dim">出图提示词</div>
        <div className="promptbox">
          <InlineEdit
            multiline
            value={prompt}
            placeholder="（还没有提示词；双击补一句）"
            onSave={(v) => void patch({ data: { image: { ...(asset.data?.image || {}), prompt: v } } })}
          />
        </div>
        <div className="art-foot">
          {status}
          <span className="link" onClick={() => void draw("sheet")}>
            {hasSheet ? `重出${gridLabel}` : `出${gridLabel}设定稿`}
          </span>
        </div>
      </div>
    </div>
  );
}
