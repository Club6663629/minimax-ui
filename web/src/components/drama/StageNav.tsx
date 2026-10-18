/** 七阶段导航：已完成可回跳，未解锁置灰并说明原因。 */
import { Check } from "lucide-react";

export type DramaStageKey =
  | "script"
  | "cast"
  | "shots"
  | "checklist"
  | "render"
  | "review"
  | "deliver";

export const DRAMA_STAGES: { key: DramaStageKey; label: string; hint: string }[] = [
  { key: "script", label: "剧本", hint: "生成或编辑本集节拍" },
  { key: "cast", label: "角色", hint: "定妆图与场景锚图" },
  { key: "shots", label: "分镜", hint: "镜头表与引用" },
  { key: "checklist", label: "就绪", hint: "清单、成本与提交预览" },
  { key: "render", label: "生成", hint: "排队、生成与后处理" },
  { key: "review", label: "审片", hint: "逐镜通过、返修与接缝" },
  { key: "deliver", label: "交付", hint: "成片下载与确认交付" },
];

export function stageIndex(key: DramaStageKey): number {
  return DRAMA_STAGES.findIndex((stage) => stage.key === key);
}

export default function StageNav({
  current,
  maxUnlocked,
  reasons,
  onJump,
}: {
  current: DramaStageKey;
  maxUnlocked: number;
  reasons?: Partial<Record<DramaStageKey, string>>;
  onJump: (key: DramaStageKey) => void;
}) {
  return (
    <nav className="panel space-y-1 p-2">
      {DRAMA_STAGES.map((stage, index) => {
        const locked = index > maxUnlocked;
        const active = stage.key === current;
        const done = index < maxUnlocked;
        const reason = locked ? reasons?.[stage.key] : "";
        return (
          <button
            key={stage.key}
            type="button"
            disabled={locked}
            title={locked ? reason || "请先完成上一步" : stage.hint}
            onClick={() => onJump(stage.key)}
            className={`flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-sm transition ${
              active
                ? "bg-primary-100 text-primary-700"
                : locked
                  ? "cursor-not-allowed text-foreground-400"
                  : "text-foreground-700 hover:bg-background-200"
            }`}
          >
            <span
              className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] ${
                active
                  ? "bg-primary-500 text-background-50"
                  : done
                    ? "bg-emerald-100 text-emerald-700"
                    : "bg-background-200 text-foreground-500"
              }`}
            >
              {done && !active ? <Check size={11} /> : index + 1}
            </span>
            <span className="flex-1">
              <span className="block">{stage.label}</span>
              {locked && reason && (
                <span className="mt-0.5 block text-[10px] leading-snug text-foreground-400">
                  {reason}
                </span>
              )}
            </span>
          </button>
        );
      })}
    </nav>
  );
}
