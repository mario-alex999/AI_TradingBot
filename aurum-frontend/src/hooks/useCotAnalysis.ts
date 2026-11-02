import type { CotHistory } from '@/types/cot';

export type CotPeriod = '12m' | '2y' | '3y' | '4y' | 'custom';

export const COT_PERIOD_WEEKS: Record<Exclude<CotPeriod, 'custom'>, number> = { '12m': 52, '2y': 104, '3y': 156, '4y': 208 };

/** Slice a COT history series to the selected period (metrics were computed server-side on full history). */
export function filterCotSeries(series: CotHistory['series'], period: CotPeriod, custom: { from: string; to: string }): CotHistory['series'] {
  if (period === 'custom') return series.filter((r) => (!custom.from || r.reportDate >= custom.from) && (!custom.to || r.reportDate <= custom.to));
  return series.slice(-COT_PERIOD_WEEKS[period]);
}
