/** 后端报错 → 用户看得懂的一句话（原始技术细节放到折叠的「查看详情」里）。 */
export function humanizeError(raw: string, fallback = "生成未成功，请稍后重试。"): string {
  const s = (raw || "").trim();
  if (!s) return fallback;
  if (/积分不足|402/.test(s)) return "积分不足，请先充值。";
  if (/Invalid image file|无法读取|decode|PIL/i.test(s)) return "商品图无法识别，请更换图片后重试。";
  if (/prompt_outputs_failed_validation|node_errors|validation|校验/i.test(s))
    return "生成未通过内容校验，请调整文字描述后重试。";
  if (/超时|timeout|Timeout/i.test(s)) return "生成超时，请稍后重试。";
  if (/广告图阶段失败|广告图/.test(s)) return "候选广告图生成失败，请重新生成或更换商品图。";
  if (/未就绪|503/.test(s)) return "生成服务暂时不可用，请稍后再试。";
  return "生成未成功，请稍后重试。";
}
