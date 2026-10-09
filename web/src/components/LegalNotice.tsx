/** 协议更新横幅：页顶一行、可关闭、仅提示一次，不阻断任何操作。
 *  只对"本机同意过旧版本"的存量用户显示；新注册/新设备用户不显示（注册时已勾选即同意当版）。
 *  口径＝海螺 8.5 / 剪映 16.1：「更新版本公示后，继续使用即视为接受更新后的协议」。 */
import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { api } from "../api/client";
import { LEGAL_LINKS, LEGAL_SEEN_KEY, LEGAL_VERSION } from "../legal";

const SEEN_KEY = LEGAL_SEEN_KEY;

export default function LegalNotice() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    // 只提示"本机此前同意过旧版本"的存量用户：新用户/新设备没有历史版本记录 → 不打扰（体验优先）。
    const seen = localStorage.getItem(SEEN_KEY);
    if (!seen || seen === LEGAL_VERSION) return;
    api
      .legalCurrent()
      .then((r) => {
        if (localStorage.getItem(SEEN_KEY) !== r.version) setShow(true);
      })
      .catch(() => {
        /* 静默失败：横幅只是提示，绝不阻断使用 */
      });
  }, []);

  function dismiss() {
    setShow(false);
    localStorage.setItem(SEEN_KEY, LEGAL_VERSION);
    api.legalAck("update_notice").catch(() => {});
  }

  if (!show) return null;

  return (
    <div className="border-b border-primary-300/40 bg-primary-100 px-4 py-2">
      <div className="mx-auto flex max-w-7xl items-center gap-3 text-xs text-primary-700">
        <span className="flex-1 leading-relaxed">
          我们更新了
          <a className="mx-1 underline" href={LEGAL_LINKS.ua.href} target="_blank" rel="noopener">
            《用户协议》
          </a>
          <a className="mr-1 underline" href={LEGAL_LINKS.pp.href} target="_blank" rel="noopener">
            《隐私政策》
          </a>
          等文本（版本 {LEGAL_VERSION}）。继续使用本服务即视为接受更新后的版本。
        </span>
        <button
          type="button"
          onClick={dismiss}
          className="shrink-0 rounded-lg p-1 transition hover:bg-primary-200"
          title="知道了"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}
