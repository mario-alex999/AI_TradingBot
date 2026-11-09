import { cn } from '@/utils/cn';
import { usage, usageTone } from '@/utils/riskLabels';

const BAR: Record<string, string> = { up: 'bg-up', warn: 'bg-warn', down: 'bg-down', neutral: 'bg-line-control' };

interface Props {
  label: string;
  value: number | null | undefined;
  limit: number | null | undefined;
  /** Formats both value and limit. */
  format: (v: number | null | undefined) => string;
  note?: string;
}

/** A figure against its limit. Unknown values show an empty track rather than a guessed zero. */
export function UsageBar({ label, value, limit, format, note }: Props) {
  const share = usage(value, limit);
  const tone = usageTone(share);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3 text-xs">
        <span className="text-ink-muted">{label}</span>
        <span className="num text-ink">
          {format(value)} <span className="text-neutral">/ {format(limit)}</span>
        </span>
      </div>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-base-raised"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={limit ?? undefined}
        aria-valuenow={value ?? undefined}
        aria-valuetext={value == null ? 'Unknown' : `${format(value)} of ${format(limit)}`}
      >
        {share != null && <div className={cn('h-full rounded-full transition-[width]', BAR[tone])} style={{ width: `${Math.max(share * 100, 2)}%` }} />}
      </div>
      {note && <span className="text-2xs text-neutral">{note}</span>}
    </div>
  );
}
