import type { ReactNode } from 'react';
import { cn } from '@/utils/cn';
import { PLACEHOLDER } from '@/utils/format';

export interface Kpi {
  label: string;
  value: string;
  hint?: ReactNode;
  tone?: 'up' | 'down' | 'flat';
}

interface Props {
  items: Kpi[];
  label: string;
  /** Optional title row (feature cards carry one, like the template's "Asset Allocation"). */
  title?: ReactNode;
  actions?: ReactNode;
  /** `feature` = the dark-green card; `plain` = white card. */
  variant?: 'feature' | 'plain';
  className?: string;
}

/**
 * Peer figures in ONE card split by thin dividers (no tile or icon per figure).
 * A missing value is a small dash, never a full-size "--" that reads as loading.
 */
export function KpiStrip({ items, label, title, actions, variant = 'plain', className }: Props) {
  const feature = variant === 'feature';
  return (
    <section
      aria-label={label}
      className={cn('overflow-hidden rounded-card', feature ? 'bg-feature text-white' : 'bg-base-card', className)}
    >
      {title && (
        <div className={cn('flex items-center justify-between gap-3 px-5 py-4', feature ? 'border-b border-feature-line' : 'border-b border-line-soft')}>
          <h2 className={cn('text-base font-semibold', feature ? 'text-white' : 'text-ink')}>{title}</h2>
          {actions}
        </div>
      )}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
        {items.map((k, i) => {
          const missing = k.value === PLACEHOLDER;
          return (
            <div
              key={k.label}
              className={cn(
                'flex min-w-0 flex-col gap-1 px-5 py-4',
                i > 0 && (feature ? 'border-l border-feature-line' : 'border-l border-line-soft'),
              )}
            >
              <span className={cn('truncate text-sm', feature ? 'text-white' : 'text-ink')}>{k.label}</span>
              <span className={cn('truncate text-xs', feature ? 'text-feature-muted' : 'text-neutral')}>{k.hint}</span>
              {missing ? (
                <span className={cn('mt-1 text-sm', feature ? 'text-feature-muted' : 'text-neutral')} aria-label={`${k.label}: not available`}>
                  —
                </span>
              ) : (
                <span
                  className={cn(
                    'num mt-1 text-lg font-semibold',
                    feature ? 'text-white' : 'text-ink',
                    !feature && k.tone === 'up' && 'text-up',
                    !feature && k.tone === 'down' && 'text-down-text',
                  )}
                >
                  {k.value}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
