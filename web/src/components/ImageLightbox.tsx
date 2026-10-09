/** 图片大图预览浮层：在一组图片间左右切换、下载、ESC / 点击遮罩关闭。
 *  广告片候选图（确认页与历史卡片）复用本组件，从「点击即下载」改为「点击查看大图」。 */
import { ChevronLeft, ChevronRight, Download, X } from "lucide-react";
import { useEffect, useState } from "react";
import { fileUrl } from "../api/client";

export default function ImageLightbox({
  urls,
  initialIndex = 0,
  downloadBase = "image",
  title,
  onClose,
}: {
  urls: string[];
  initialIndex?: number;
  /** 下载文件名前缀：实际文件名为 `${downloadBase}_${序号}.png` */
  downloadBase?: string;
  title?: string;
  onClose: () => void;
}) {
  const n = urls.length;
  const [index, setIndex] = useState(() => (n ? Math.min(Math.max(0, initialIndex), n - 1) : 0));
  const cur = urls[index];

  const prev = () => setIndex((i) => (i - 1 + n) % n);
  const next = () => setIndex((i) => (i + 1) % n);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft" && n > 1) prev();
      else if (e.key === "ArrowRight" && n > 1) next();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [n, onClose]);

  if (!cur) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-foreground-950/80 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-background-200 bg-background-100 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 border-b border-background-200 px-4 py-2.5">
          <span className="truncate text-sm text-foreground-900">
            {title ?? "候选广告图"}
            {n > 1 ? `（${index + 1} / ${n}）` : ""}
          </span>
          <a
            href={fileUrl(cur)}
            download={`${downloadBase}_${index + 1}.png`}
            className="ml-auto inline-flex items-center gap-1 rounded-full border border-background-300 bg-background-50 px-2.5 py-1 text-[11px] text-foreground-600 transition hover:border-primary-400 hover:text-foreground-900"
            title="下载这张图"
          >
            <Download size={12} /> 下载
          </a>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-foreground-500 transition hover:bg-background-200"
            title="关闭"
          >
            <X size={16} />
          </button>
        </div>

        <div className="relative flex flex-1 items-center justify-center bg-foreground-950/90 p-2">
          {n > 1 && (
            <button
              type="button"
              onClick={prev}
              className="absolute left-2 z-10 rounded-full bg-foreground-950/50 p-2 text-background-50 transition hover:bg-foreground-950/80"
              title="上一张"
            >
              <ChevronLeft size={18} />
            </button>
          )}
          <img src={fileUrl(cur)} alt={`${title ?? "候选广告图"} ${index + 1}`} className="max-h-[74vh] w-full object-contain" />
          {n > 1 && (
            <button
              type="button"
              onClick={next}
              className="absolute right-2 z-10 rounded-full bg-foreground-950/50 p-2 text-background-50 transition hover:bg-foreground-950/80"
              title="下一张"
            >
              <ChevronRight size={18} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
