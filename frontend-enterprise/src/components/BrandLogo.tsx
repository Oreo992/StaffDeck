import { cn } from '@/lib/utils';
import logoMark from '../assets/LOGO.svg';
import logoWordmark from '../assets/NEOSPARK_WORDMARK.svg';

export type BrandLogoProps = {
  /** Hide the NeoSpark wordmark and only render the logo mark. */
  markOnly?: boolean;
  /** Size of the square logo mark in pixels. */
  markSize?: number;
  className?: string;
  /** Extra classes applied to the wordmark wrapper (e.g. to hide it responsively). */
  wordmarkClassName?: string;
};

/** Official NeoSpark brand artwork. Product naming lives outside the logo area. */
export default function BrandLogo({
  markOnly = false,
  markSize = 28,
  className,
  wordmarkClassName,
}: BrandLogoProps) {
  return (
    <span className={cn('flex items-center overflow-hidden p-[4px]', className)}>
      {markOnly ? (
        <img
          src={logoMark}
          alt="NeoSpark"
          className="shrink-0"
          style={{ width: markSize, height: markSize }}
        />
      ) : (
        <img
          src={logoWordmark}
          alt="NeoSpark"
          className={cn('h-auto shrink-0', wordmarkClassName)}
          style={{ width: Math.round(markSize * 4.77) }}
        />
      )}
    </span>
  );
}
