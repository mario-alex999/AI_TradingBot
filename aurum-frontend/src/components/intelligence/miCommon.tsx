import type { ReactNode } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { FlaskConical } from 'lucide-react';
import { QueryState } from '@/components/common/QueryState';
import { EmptyState } from '@/components/common/EmptyState';
import type { Sourced } from '@/types/api';

/** Shown in demo mode: Module 04 data is never simulated. */
export function MiDemoUnavailable({ compact = true }: { compact?: boolean }) {
  return (
    <EmptyState
      compact={compact}
      icon={<FlaskConical className="h-5 w-5" aria-hidden />}
      title="Unavailable in demo mode"
      description="Market intelligence (sessions, calendar, macro, news, volatility, conditions) comes only from the backend. Demo data is never generated for it. Turn demo mode off to load it."
    />
  );
}

/** Loading / error / demo-unavailable branching for Module 04 queries. */
export function MiQuery<T>({ query, children, compact = true }: { query: UseQueryResult<Sourced<T | null>>; children: (d: T) => ReactNode; compact?: boolean }) {
  return (
    <QueryState query={query} compact={compact} isEmpty={(d) => d.data == null} empty={<MiDemoUnavailable compact={compact} />}>
      {(d) => children(d.data as T)}
    </QueryState>
  );
}

/** Small table helpers in the app's table style. */
export function Th({ children, right }: { children: ReactNode; right?: boolean }) {
  return (
    <th scope="col" className={right ? 'px-3 py-2.5 text-right font-semibold' : 'px-3 py-2.5 text-left font-semibold'}>
      {children}
    </th>
  );
}

export function Td({ children, right, muted, className }: { children: ReactNode; right?: boolean; muted?: boolean; className?: string }) {
  return (
    <td className={`px-3 py-2 ${right ? 'num text-right' : ''} ${muted ? 'text-ink-muted' : 'text-ink'} ${className ?? ''}`}>{children}</td>
  );
}

export function MiTable({ caption, head, children, minWidth = 720 }: { caption: string; head: ReactNode; children: ReactNode; minWidth?: number }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" style={{ minWidth }}>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-line text-xs text-neutral">{head}</tr>
        </thead>
        <tbody className="[&>tr]:border-b [&>tr]:border-line-soft [&>tr:last-child]:border-0">{children}</tbody>
      </table>
    </div>
  );
}

/** Freshness / provenance footer line. */
export function Provenance({ children }: { children: ReactNode }) {
  return <p className="px-4 pb-4 text-2xs leading-relaxed text-neutral">{children}</p>;
}
