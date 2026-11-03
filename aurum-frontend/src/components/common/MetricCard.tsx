import type { ReactNode } from 'react';
import { cn } from '@/utils/cn';
import { Card } from './Card';
import { InfoTip } from './InfoTip';

interface Props {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  /** @deprecated KPIs carry no icon; accepted for call-site compatibility and ignored. */
  icon?: ReactNode;
  tone?: 'up' | 'down' | 'flat' | 'muted';
  /** Plain-language explanation shown in an info tip next to the label. */
  info?: string;
  className?: string;
}

/** A label over a figure. No icon chip: the label alone names the figure. */
export function MetricCard({ label, value, hint, tone = 'flat', info, className }: Props) {
  return (
    <Card className={cn('flex flex-col gap-2 p-4', className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="eyebrow flex items-center gap-1">
          {label}
          {info && <InfoTip label={`What is ${label.toLowerCase()}?`}>{info}</InfoTip>}
        </span>
      </div>
      <div
        className={cn(
          'num text-xl font-normal',
          tone === 'up' && 'text-up',
          tone === 'down' && 'text-down-text',
          tone === 'muted' && 'text-neutral',
          tone === 'flat' && 'text-ink',
        )}
      >
        {value}
      </div>
      {hint && <div className="text-xs text-neutral">{hint}</div>}
    </Card>
  );
}
