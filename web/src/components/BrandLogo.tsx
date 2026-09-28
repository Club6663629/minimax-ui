/** 海白菜品牌组合：图形符号 + 中文标准字。 */
import BrandMark from "./BrandMark";

interface BrandLogoProps {
  size?: number;
  showWordmark?: boolean;
  className?: string;
}

export default function BrandLogo({
  size = 32,
  showWordmark = true,
  className = "",
}: BrandLogoProps) {
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <BrandMark size={size} />
      {showWordmark && (
        <span
          className="font-display font-semibold text-foreground-950"
          style={{ fontSize: size * 0.5, letterSpacing: "0.1em", marginRight: "-0.1em" }}
        >
          海白菜
        </span>
      )}
    </div>
  );
}
