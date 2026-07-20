import { cn } from '@/lib/utils';
import logoMark from '../assets/LOGO.svg';

export type BrandLogoProps = {
  /** Hide the NeoSpark wordmark and only render the logo mark. */
  markOnly?: boolean;
  /** Size of the square logo mark in pixels. */
  markSize?: number;
  className?: string;
  /** Extra classes applied to the wordmark wrapper (e.g. to hide it responsively). */
  wordmarkClassName?: string;
};

/** NeoSpark brand mark. Product naming lives outside the logo area. */
export default function BrandLogo({
  markOnly = false,
  markSize = 28,
  className,
  wordmarkClassName,
}: BrandLogoProps) {
  return (
    <span className={cn('flex items-center gap-[8px] overflow-hidden p-[4px]', className)}>
      <img
        src={logoMark}
        alt="NeoSpark"
        className="shrink-0"
        style={{ width: markSize, height: markSize }}
      />
      {!markOnly && (
        <strong
          className={cn(
            'whitespace-nowrap text-[13px] font-semibold tracking-[0.14em] text-[#373b49]',
            wordmarkClassName,
          )}
        >
          NEOSPARK
        </strong>
      )}
    </span>
  );
}
