/** 新建短剧项目：立项即创建第 1 集（草稿）。 */
import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { api } from "../../api/client";
import type { DramaEpisode, DramaProject } from "../../types";
import { errText } from "./labels";

const GENRES = ["都市情感", "悬疑推理", "古装宫斗", "科幻脑洞", "家庭伦理", "甜宠"];

export default function ProjectCreateDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (project: DramaProject, episode: DramaEpisode) => void;
}) {
  const [title, setTitle] = useState("");
  const [logline, setLogline] = useState("");
  const [genre, setGenre] = useState(GENRES[0]);
  const [styleBible, setStyleBible] = useState(
    "写实电影感，冷暖对比，浅景深，手持轻微呼吸感；角色造型与服装保持全剧一致。",
  );
  const [aspect, setAspect] = useState("16:9");
  const [target, setTarget] = useState(6);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setError("");
    setBusy(false);
  }, [open]);

  if (!open) return null;

  const submit = async () => {
    if (!title.trim() || !logline.trim()) {
      setError("请填写剧名与一句话梗概");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api.dramaCreateProject({
        title: title.trim(),
        logline: logline.trim(),
        genre,
        style_bible: styleBible.trim(),
        aspect_ratio: aspect,
        target_episodes: Math.max(1, Math.min(60, target)),
      });
      setTitle("");
      setLogline("");
      onCreated(result.project, result.episode);
    } catch (exc) {
      setError(errText(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-foreground-950/40 px-4">
      <div className="panel w-full max-w-lg space-y-4 p-5">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-foreground-950">新建短剧</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-1 text-foreground-500 hover:bg-background-200"
          >
            <X size={16} />
          </button>
        </div>

        <label className="block space-y-1 text-xs text-foreground-600">
          <span>剧名</span>
          <input
            className="input"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="例如：雨夜来电"
            maxLength={40}
          />
        </label>

        <label className="block space-y-1 text-xs text-foreground-600">
          <span>一句话梗概</span>
          <textarea
            className="input min-h-[72px] resize-y"
            value={logline}
            onChange={(event) => setLogline(event.target.value)}
            placeholder="例如：一通来自三年前的电话，让女主在雨夜重遇失踪的恋人。"
            maxLength={200}
          />
        </label>

        <div className="grid grid-cols-3 gap-3">
          <label className="block space-y-1 text-xs text-foreground-600">
            <span>题材</span>
            <select className="input" value={genre} onChange={(event) => setGenre(event.target.value)}>
              {GENRES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="block space-y-1 text-xs text-foreground-600">
            <span>画幅</span>
            <select className="input" value={aspect} onChange={(event) => setAspect(event.target.value)}>
              <option value="16:9">16:9 横屏</option>
              <option value="9:16">9:16 竖屏</option>
            </select>
          </label>
          <label className="block space-y-1 text-xs text-foreground-600">
            <span>目标集数</span>
            <input
              type="number"
              min={1}
              max={60}
              className="input"
              value={target}
              onChange={(event) => setTarget(Number(event.target.value) || 1)}
            />
          </label>
        </div>

        <label className="block space-y-1 text-xs text-foreground-600">
          <span>风格圣经（全剧统一画风、光线与造型）</span>
          <textarea
            className="input min-h-[84px] resize-y"
            value={styleBible}
            onChange={(event) => setStyleBible(event.target.value)}
            maxLength={600}
          />
        </label>

        {error && <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</p>}

        <div className="flex justify-end gap-2 pt-1">
          <button type="button" className="btn-ghost" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button type="button" className="btn-primary" onClick={submit} disabled={busy}>
            {busy ? "创建中…" : "创建并进入第 1 集"}
          </button>
        </div>
      </div>
    </div>
  );
}
