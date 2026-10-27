/** 角色卡 / 场景卡 / 道具卡：同一套语言（锚点大图 + 派生视图 + 图上四图标 + 提示词区）。 */
import { useCallback, useEffect, useRef, useState } from "react";
import { hibtvApi } from "../api";
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
          view={`当前版本 · v${asset.version || 1}`}
          src={anchor.url}
          ratio={asset.kind === "cast" ? "3 / 4" : "16 / 9"}
        />
        <div className="tiny dim" style={{ margin: "10px 0 14px" }}>
          原图作为参考，其余 {Math.max(0, asset.defs.length - 1)} 张会标成过期（可随时切回历史版本）。
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
          只会重画这张锚点并派生其余视图；下游引用它的镜头会标「素材已更新」，但不自动重跑视频。
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
  const derived = asset.defs.slice(1);
  const stale = derived.some(([v]) => asset.views[v]?.stale);
  const review = asset.status === "review";
  const drawing = asset.status === "drawing";
  const prompt: string = asset.data?.image?.prompt || "";
  const voice: string = asset.data?.voice?.timbre || "";
  const tag =
    asset.kind === "cast"
      ? asset.data?.persona?.identity || asset.data?.importance || "角色"
      : asset.data?.primary
        ? "主场景"
        : asset.data?.summary || (asset.kind === "prop" ? "道具" : "场景");

  const patch = async (body: { name?: string; hidden?: boolean; data?: Record<string, unknown> }) => {
    if (!project) return;
    try {
      await hibtvApi.patchAsset(project.id, asset.id, body);
      onChanged();
    } catch (err) {
      toast(errText(err));
    }
  };

  const draw = async (action: "anchor" | "views" | "all" | "confirm", hint?: string) => {
    if (!project) return;
    setBusy(true);
    try {
      const res = await hibtvApi.assetImage(project.id, asset.id, {
        action,
        hint,
        views: action === "views" ? derived.map(([v]) => v) : undefined,
      });
      if ("kind" in res && res.kind === "images") {
        toast(`已排队出图：${asset.name}（${action === "anchor" ? "锚点" : `${derived.length} 张派生`}）`);
      } else if (action === "confirm") {
        toast(`已接受 v${asset.version}：派生视图恢复`);
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
      toast(`已替换：${asset.name} · ${view === anchor ? "锚点" : view}`);
      onChanged();
    } catch (err) {
      toast(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const bigView = () => {
    openLayer(
      <div className="modal" style={{ width: "min(860px,94vw)", background: "#0d0b09" }}>
        <div className="modal-hd">
          <div>
            <div className="eyebrow">
              {asset.kind.toUpperCase()} · {asset.code} · 全部视图
            </div>
            <div className="h1" style={{ fontSize: 18 }}>
              {asset.name}
            </div>
            <div className="tiny mut" style={{ marginTop: 4 }}>
              {asset.defs.length} 张视图来自同一个锚点；点卡片上的「重新生成」可重出这一组。
            </div>
          </div>
          <span className="x" onClick={closeLayer}>
            ✕
          </span>
        </div>
        <div className="modal-bd">
          <div className={`quad ${asset.kind === "cast" ? "" : "land"}`}>
            {asset.defs.map(([v, label], i) => (
              <div
                key={v}
                className={`q ${i === 0 ? "main" : ""} ${
                  asset.kind !== "cast" && asset.defs.length % 2 && i === asset.defs.length - 1 ? "wide" : ""
                }`}
              >
                <Plate
                  kind={plateKind}
                  label={asset.name}
                  view={label}
                  src={asset.views[v]?.url}
                  extra={asset.views[v]?.stale ? "stale" : ""}
                />
                <span className="tag">
                  {label}
                  {i === 0 ? " · 锚点" : ""}
                </span>
              </div>
            ))}
          </div>
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
    if (act === "regen") void draw("anchor");
    if (act === "upload") fileRef.current?.click();
    if (act === "reroll") {
      openLayer(
        <RerollDrawer
          asset={asset}
          onClose={closeLayer}
          onSubmit={(hint) => {
            closeLayer();
            void draw("anchor", hint);
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
      <span className="chip red">v{asset.version} 待确认 · 派生图已过期</span>
      <button className="btn xs" disabled={busy} onClick={() => void draw("confirm")}>
        接受 v{asset.version}
      </button>
    </>
  ) : anchorMeta.path ? (
    <>
      <span className="chip green">v{asset.version} · 已出图</span>
      <span className="chip">
        {asset.kind === "cast" ? `派生 ${derived.length} 张` : `派生 ${derived.filter(([v]) => asset.views[v]?.path).length}/${derived.length}`}
      </span>
    </>
  ) : (
    <>
      <span className="chip ghost">还没有锚点图</span>
      <button className="btn xs" disabled={busy} onClick={() => void draw("anchor")}>
        出锚点图
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
        if (file) void upload(file, anchor);
      }}
    />
  );
  const staleMark = (review || stale) && <div className="stale" />;

  if (asset.kind === "cast") {
    return (
      <div className="cast-card">
        <div className="cast-media">
          <div className="cast-anchor" onClick={bigView} aria-label="点击看四视图大图">
            <Plate
              kind="person"
              label={asset.name}
              view="正面全身 · 锚点"
              src={anchorMeta.url}
              extra={anchorMeta.stale ? "stale" : ""}
            >
              {staleMark}
            </Plate>
            {fileInput}
            <MediaActs onAct={onAct} />
          </div>
          <div className="cast-viewcol">
            {derived.map(([v, label]) => (
              <div key={v} className="vthumb" onClick={bigView} title={`${label}（点开看大图）`}>
                <span className="ph" style={{ position: "absolute", inset: 0, fontSize: 9 }} />
                {asset.views[v]?.url && <img src={asset.views[v].url} alt={label} />}
                <span className="lab">{label}</span>
                {asset.views[v]?.stale && <span className="stale" />}
              </div>
            ))}
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
            <span className="pin" title="看全部视图" onClick={bigView}>
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
          <div className="cast-foot" onClick={bigView}>
            画像 · 弧光 · 关系 · 音色 · 全部视图 →
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="art-card">
      <div className="art-anchor" onClick={bigView} aria-label="点击看全部视图大图">
        <Plate
          kind={plateKind}
          label={asset.name}
          view="正面 · 锚点"
          src={anchorMeta.url}
          extra={anchorMeta.stale ? "stale" : ""}
        >
          {staleMark}
        </Plate>
        {fileInput}
        <MediaActs onAct={onAct} />
      </div>
      <div className="art-thumbs">
        {derived.map(([v, label]) => (
          <div key={v} className="vthumb" onClick={bigView} title={`${label}（点开看大图）`}>
            <span className="ph" style={{ position: "absolute", inset: 0, fontSize: 9 }} />
            {asset.views[v]?.url && <img src={asset.views[v].url} alt={label} />}
            <span className="lab">{label}</span>
            {asset.views[v]?.stale && <span className="stale" />}
          </div>
        ))}
      </div>
      <div className="art-body">
        <div className="cast-name">
          <span className="mid">{asset.code}</span>
          <InlineEdit value={asset.name} onSave={(v) => void patch({ name: v })} />
          <span className="tag">{tag}</span>
          <span className="pin" title="看全部视图" onClick={bigView}>
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
          <span className="link" onClick={() => void draw("views")}>
            补齐派生视图
          </span>
        </div>
      </div>
    </div>
  );
}
