import type { ReactNode } from 'react';
import { Hourglass } from 'lucide-react';
import { cn } from '@/utils/cn';

/** Honest placeholder for analysis the backend has not implemented yet. */
export function FeaturePending({ title, requires, children, className }: { title: string; requires?: string; children?: ReactNode; className?: string }) {
  return (
    <div className={cn('flex flex-col gap-2 rounded-lg border border-dashed border-line bg-base-raised/60 p-4', className)}>
      <div className="flex items-center gap-2">
        <Hourglass className="h-3.5 w-3.5 text-neutral" aria-hidden />
        <span className="eyebrow">{title}</span>
      </div>
      <p className="text-sm text-ink-muted">Analysis not yet available</p>
      {requires && <p className="text-2xs text-neutral">Requires: {requires}</p>}
      {children}
    </div>
  );
}
