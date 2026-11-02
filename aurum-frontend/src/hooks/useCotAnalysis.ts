import { useMemo } from 'react';
import { useCotReport } from './queries';
import { deriveCot, type CotDerived } from '@/utils/cot';

export type CotPeriod = '12m' | '2y' | '3y' | '4y' | 'custom';

export const COT_PERIOD_WEEKS: Record<Exclude<CotPeriod, 'custom'>, number> = { '12m': 52, '2y': 104, '3y': 156, '4y': 208 };

/** Fetches 5 years once (for stable 3-year percentiles) and slices the requested window. */
export function useCotAnalysis(instrument: string, period: CotPeriod, custom: { from: string; to: string }) {
  const q = useCotReport(instrument, 260);
  const report = q.data?.data ?? null;
  const all: CotDerived[] = useMemo(() => (report ? deriveCot(report.history) : []), [report]);
  const rows = useMemo(() => {
    if (period === 'custom') return all.filter((r) => (!custom.from || r.reportDate >= custom.from) && (!custom.to || r.reportDate <= custom.to));
    return all.slice(-COT_PERIOD_WEEKS[period]);
  }, [all, period, custom.from, custom.to]);
  return { query: q, report, rows, latest: all[all.length - 1] ?? null, source: q.data?.source };
}
