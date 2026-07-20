import { cn } from '@/lib/utils';
import logoMark from '../assets/LOGO.svg';

export type BrandLogoProps = {
  /** Hide the Agent Team wordmark and only render the NeoSpark logo mark. */
  markOnly?: boolean;
  /** Size of the square logo mark in pixels. */
  markSize?: number;
  className?: string;
  /** Extra classes applied to the wordmark wrapper (e.g. to hide it responsively). */
  wordmarkClassName?: string;
};

/** NeoSpark logo mark with the Agent Team product wordmark. */
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
        <span className={cn('flex min-w-0 flex-col items-start gap-[2px] leading-none', wordmarkClassName)}>
          <span className="text-[8px] font-semibold tracking-[0.14em] text-[#757f9c]">
            NEOSPARK
          </span>
          <strong className="whitespace-nowrap text-[16px] font-semibold leading-none text-[#18181a]">
            Agent Team
          </strong>
        </span>
      )}
    </span>
  );
}
