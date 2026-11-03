import type { ReactNode } from 'react';
import { cn } from '@/utils/cn';
import { PLACEHOLDER } from '@/utils/format';

export interface Kpi {
  label: string;
  value: string;
  hint?: ReactNode;
  tone?: 'up' | 'down' | 'flat';
}

/**
 * Peer figures in ONE container split by 1px dividers (no tile per KPI, no icons).
 * A missing value is a small grey dash, never a full-size "--" that reads as loading.
 */
export function KpiStrip({ items, label, className }: { items: Kpi[]; label: string; className?: string }) {
  return (
    <section
      aria-label={label}
      className={cn(
        'grid grid-cols-2 overflow-hidden rounded-card border border-line bg-base-card md:grid-cols-3 xl:grid-cols-6',
        // 1px dividers drawn by the gap over the line-coloured background
        'gap-px bg-line',
        className,
      )}
    >
      {items.map((k) => {
        const missing = k.value === PLACEHOLDER;
        return (
          <div key={k.label} className="flex min-w-0 flex-col gap-1.5 bg-base-card px-4 py-3.5">
            <span className="eyebrow truncate">{k.label}</span>
            {missing ? (
              <span className="text-sm text-neutral" aria-label={`${k.label}: not available`}>
                —
              </span>
            ) : (
              <span
                className={cn(
                  'num text-xl text-ink',
                  k.tone === 'up' && 'text-up',
                  k.tone === 'down' && 'text-down-text',
                )}
              >
                {k.value}
              </span>
            )}
            {k.hint && <span className="truncate text-xs text-neutral">{k.hint}</span>}
          </div>
        );
      })}
    </section>
  );
}
