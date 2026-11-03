import type { ReactNode } from 'react';
import { cn } from '@/utils/cn';
import { Card } from './Card';

interface Props {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  /** @deprecated KPIs carry no icon; accepted for call-site compatibility and ignored. */
  icon?: ReactNode;
  tone?: 'up' | 'down' | 'flat' | 'muted';
  className?: string;
}

/** A label over a figure. No icon chip: the label alone names the figure. */
export function MetricCard({ label, value, hint, tone = 'flat', className }: Props) {
  return (
    <Card className={cn('flex flex-col gap-2 p-4', className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="eyebrow">{label}</span>
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
