/** 用户协议 V2（体验优先版）：前端单一来源的法律文本版本与链接。
 *  正文页为静态 HTML（web/public/legal/*.html → 构建后 /legal/*.html）。 */
export const LEGAL_VERSION = "1.0";
export const LEGAL_UPDATED = "2026-09-30";
export const LEGAL_EFFECTIVE = "2026-09-30";

/** localStorage 记录"本机已同意版本"的 key（注册成功写入 / 关闭横幅写入） */
export const LEGAL_SEEN_KEY = "h3_legal_seen";

export interface LegalLink {
  key: string;
  title: string;
  href: string;
}

export const LEGAL_LINKS: Record<string, LegalLink> = {
  ua: { key: "ua", title: "用户协议", href: "/legal/user-agreement.html" },
  pp: { key: "pp", title: "隐私政策", href: "/legal/privacy-policy.html" },
  ai: { key: "ai", title: "AI 标识说明", href: "/legal/ai-labeling.html" },
  credits: { key: "credits", title: "积分与计费规则", href: "/legal/credits-rules.html" },
  api: { key: "api", title: "API 服务条款", href: "/legal/api-terms.html" },
};

/** 页脚常驻四入口 */
export const FOOTER_LINKS: LegalLink[] = [
  LEGAL_LINKS.ua,
  LEGAL_LINKS.pp,
  LEGAL_LINKS.ai,
  LEGAL_LINKS.credits,
];

/** 注册页那 1 个合并勾选框覆盖的文档 */
export const REGISTER_AGREED: LegalLink[] = [LEGAL_LINKS.ua, LEGAL_LINKS.pp, LEGAL_LINKS.ai];
