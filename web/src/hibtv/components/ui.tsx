/** 短剧工作台通用组件：占位图、图上操作、就地编辑、质量门标签、生成进度条。 */
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { mediaUrl } from "../api";
import type { Gates, HibtvJob } from "../types";

/* ------------------------------------------------------------------ 占位图 */
export function Plate({
  kind = "person",
  label,
  view,
  src,
  extra,
  ratio,
  children,
}: {
  kind?: "person" | "scene" | "prop" | "frame";
  label: string;
  view?: string;
  src?: string;
  extra?: string;
  ratio?: string;
  children?: ReactNode;
}) {
  const cls =
    kind === "scene" ? "k-scene" : kind === "prop" ? "k-prop" : kind === "frame" ? "k-frame" : "k-person";
  return (
    <div className={`plate ${cls} ${extra || ""}`} style={ratio ? { aspectRatio: ratio } : undefined}>
      <span className="ph">
        <b>{label}</b>
        {view && <i>{view}</i>}
      </span>
      {src && <img src={mediaUrl(src)} alt={label} />}
      {children}
    </div>
  );
}

/* ------------------------------------------------------------------ 图上四图标 */
const ACTS = [
  {
    key: "regen",
    tip: "重新生成：",
    desc: "同一套提示词再出一版，保留历史文件",
    path: "M1 4v6h6M3.51 15a9 9 0 1 0 2.13-9.36L1 10",
  },
  {
    key: "reroll",
    tip: "改图：",
    desc: "写一句修改要求，原图当参考重出这一张",
    path: "M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z",
  },
  {
    key: "upload",
    tip: "上传：",
    desc: "换成你自己的图（顶掉主图）",
    path: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12",
  },
  {
    key: "del",
    tip: "删除：",
    desc: "从素材库隐藏，可撤销",
    path: "M4 7h16M10 4h4M6 7v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7",
  },
];

export function MediaActs({ onAct }: { onAct: (act: string) => void }) {
  return (
    <div className="media-acts">
      {ACTS.map((a, i) => (
        <button
          key={a.key}
          className={`ic${a.key === "del" ? " del" : ""}`}
          aria-label={a.tip.replace("：", "")}
          onClick={(e) => {
            e.stopPropagation();
            onAct(a.key);
          }}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round">
            <path d={a.path} />
          </svg>
          <span className="tip" style={{ ["--k" as string]: i + 1 }}>
            <b>{a.tip}</b>
            {a.desc}
          </span>
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ 就地编辑 */
export function InlineEdit({
  value,
  onSave,
  className,
  style,
  multiline,
  placeholder,
}: {
  value: string;
  onSave: (next: string) => void;
  className?: string;
  style?: CSSProperties;
  multiline?: boolean;
  placeholder?: string;
}) {
  const ref = useRef<HTMLSpanElement | null>(null);
  const [editing, setEditing] = useState(false);
  const old = useRef(value);

  useEffect(() => {
    if (!editing && ref.current) ref.current.innerText = value;
  }, [value, editing]);

  const finish = (save: boolean) => {
    const el = ref.current;
    if (!el) return;
    const next = (el.innerText || "").trim();
    setEditing(false);
    el.contentEditable = "false";
    if (save && next && next !== old.current) {
      old.current = next;
      onSave(next);
    } else {
      el.innerText = old.current;
    }
  };

  return (
    <span
      ref={ref}
      className={`${className || ""} ${editing ? "editing" : ""}`}
      style={style}
      contentEditable={editing}
      suppressContentEditableWarning
      title="双击编辑"
      onDoubleClick={() => {
        old.current = ref.current?.innerText || value;
        setEditing(true);
        const el = ref.current;
        if (el) {
          el.contentEditable = "true";
          el.focus();
          const range = document.createRange();
          range.selectNodeContents(el);
          const sel = window.getSelection();
          sel?.removeAllRanges();
          sel?.addRange(range);
        }
      }}
      onBlur={() => finish(true)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !multiline) {
          e.preventDefault();
          finish(true);
        }
        if (e.key === "Escape") {
          const el = ref.current;
          if (el) el.innerText = old.current;
          finish(false);
        }
        e.stopPropagation();
      }}
    >
      {value || placeholder || ""}
    </span>
  );
}

/* ------------------------------------------------------------------ 质量门 */
export function GateChips({ gates, none }: { gates?: Gates; none?: string }) {
  if (!gates || !gates.status || gates.status === "empty") {
    // 门结果只来自网关产物（.gates.jsonl / report.html）；这一类没有门就如实说没有。
    if (none) {
      return (
        <span className="chip ghost" title={none}>
          产物里没有门结果
        </span>
      );
    }
    return <span className="chip ghost">质量门未运行</span>;
  }
  if (gates.status === "stale") {
    // 就地改稿后门没复跑：如实标，不伪装成通过
    return (
      <span className="chip gold" title={(gates.notes || []).join("\n")}>
        改动后门未复跑
      </span>
    );
  }
  if (gates.status === "skipped") {
    return (
      <span className="chip ghost" title={(gates.notes || gates.problems || []).join("\n")}>
        产物里没有门结果
      </span>
    );
  }
  const passed = gates.passed ?? 0;
  const total = gates.total ?? 0;
  if (gates.status === "ok") {
    const notes = gates.notes || [];
    return (
      <span className="chip green" title={[...notes, gates.source ? `来源 ${gates.source}` : ""].filter(Boolean).join("\n")}>
        质量门 {total ? `${passed}/${total}` : "通过"}
      </span>
    );
  }
  return (
    <span className="chip red" title={(gates.problems || []).join("\n")}>
      质量门 {(gates.failed || (gates.problems || []).length) || 1} 项待修
    </span>
  );
}

export function GateList({ gates }: { gates?: Gates }) {
  const problems = gates?.problems || [];
  const notes = gates?.notes || [];
  if (!problems.length && !notes.length) return null;
  const ok = gates?.status === "ok";
  return (
    <div className={`gates ${ok ? "ok" : "bad"}`}>
      {problems.map((p, i) => (
        <div key={`p${i}`}>{p}</div>
      ))}
      {notes.map((n, i) => (
        <div key={`n${i}`} className="tiny dim">
          {n}
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ 生成进度 */
export function GenBar({ job, onCancel }: { job: HibtvJob | null; onCancel?: () => void }) {
  if (!job || !["queued", "running"].includes(job.status)) return null;
  const pct = Math.max(3, Math.min(100, Math.round(job.progress || 0)));
  return (
    <div className="genbar" style={{ margin: "6px 0 14px" }}>
      <div style={{ flex: 1 }}>
        <div className="t">
          {job.stage || "生成中"} · {pct}%
        </div>
        <div className="prog" style={{ marginTop: 8 }}>
          <i style={{ width: `${pct}%` }} />
        </div>
      </div>
      {onCancel && (
        <button className="btn sm" onClick={onCancel}>
          取消生成
        </button>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ 页头 / 空态 */
export function PageHead({
  eyebrow,
  title,
  lede,
  right,
}: {
  eyebrow: string;
  title: ReactNode;
  lede?: ReactNode;
  right?: ReactNode;
}) {
  return (
    <>
      <div className="eyebrow">{eyebrow}</div>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 14, flexWrap: "wrap" }}>
        <div style={{ minWidth: 240, flex: "1 1 420px" }}>
          <div className="h1">{title}</div>
          {lede ? <p className="lede">{lede}</p> : null}
        </div>
        <div
          style={{
            marginLeft: "auto", display: "flex", gap: 8, rowGap: 8,
            alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end",
          }}
        >
          {right}
        </div>
      </div>
    </>
  );
}

export function Empty({
  glyph,
  title,
  sub,
  children,
}: {
  glyph: string;
  title: string;
  sub: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <div className="glyph">{glyph}</div>
      <div className="t">{title}</div>
      <div className="s">{sub}</div>
      {children}
    </div>
  );
}

export function Loading({ text = "读取中…" }: { text?: string }) {
  return <div className="empty"><div className="t">{text}</div></div>;
}

export function Modal({
  title,
  eyebrow,
  sub,
  onClose,
  footer,
  children,
  width,
}: {
  title: string;
  eyebrow?: string;
  sub?: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
  children: ReactNode;
  width?: string;
}) {
  return (
    <div className="modal" style={width ? { width } : undefined}>
      <div className="modal-hd">
        <div>
          {eyebrow && <div className="eyebrow">{eyebrow}</div>}
          <div className="h1" style={{ fontSize: 20, marginBottom: 6 }}>
            {title}
          </div>
          {sub && <div className="tiny mut" style={{ maxWidth: 600, lineHeight: 1.8 }}>{sub}</div>}
        </div>
        <span className="x" onClick={onClose}>
          ✕
        </span>
      </div>
      <div className="modal-bd">{children}</div>
      {footer && <div className="modal-ft">{footer}</div>}
    </div>
  );
}

export function fmtTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** 真实 <video> 播放器；没有成片时退回占位图。 */
export function VideoBox({
  src,
  label,
  view,
  ratio,
  maxHeight,
}: {
  src?: string;
  label: string;
  view?: string;
  ratio?: string;
  maxHeight?: number;
}) {
  if (!src) {
    return (
      <Plate
        kind="frame"
        label={label}
        view={view || "还没有成片"}
        ratio={ratio || "9 / 16"}
        extra=""
      />
    );
  }
  return (
    <video
      className="thumb-video"
      style={{ maxHeight: maxHeight || 360, width: "100%", borderRadius: 8, background: "#000" }}
      src={mediaUrl(src)}
      controls
      playsInline
    />
  );
}
