/** 海白菜品牌图形符号：水滴 + 播放三角（透明底，可单独作为 App 图标）。 */
export const BRAND_LOGO_SRC =
  "https://public.readdy.ai/ai/img_res/afdcfe71-8285-4e0d-94cf-b483f4f19c14.png";

interface BrandMarkProps {
  size?: number;
  className?: string;
}

export default function BrandMark({ size = 44, className = "" }: BrandMarkProps) {
  return (
    <img
      src={BRAND_LOGO_SRC}
      alt="海白菜 Logo"
      width={size}
      height={size}
      draggable={false}
      className={`shrink-0 select-none object-contain ${className}`}
      style={{ width: size, height: size }}
    />
  );
}
