/** 短剧页面共用的轻量提示：固定底部居中，3.2s 自动消失。 */
import { useCallback, useEffect, useRef, useState } from "react";

export function useToast() {
  const [message, setMessage] = useState("");
  const [visible, setVisible] = useState(false);
  const timer = useRef<number | null>(null);

  const show = useCallback((next: string) => {
    setMessage(next);
    setVisible(true);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setVisible(false), 3200);
  }, []);

  useEffect(
    () => () => {
      if (timer.current) window.clearTimeout(timer.current);
    },
    [],
  );

  return { message, visible, show };
}

export default function Toast({ message, visible }: { message: string; visible: boolean }) {
  if (!visible) return null;
  return (
    <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-full bg-foreground-950/90 px-5 py-2.5 text-sm text-background-50 shadow-lg">
      {message}
    </div>
  );
}
