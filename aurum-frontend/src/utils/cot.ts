import type { CotPositionRow } from '@/types/analysis';

export interface CotDerived extends CotPositionRow {
  commercialNet: number;
  nonCommercialNet: number;
  nonReportableNet: number;
  nonCommercialNetChange: number | null;
  commercialNetChange: number | null;
  nonCommercialPercentile: number | null;
}

/** Percentile rank (0–100) of the latest value within its trailing window. */
export function percentileRank(window: number[], value: number): number {
  if (window.length === 0) return 0;
  const below = window.filter((v) => v < value).length;
  const equal = window.filter((v) => v === value).length;
  return ((below + 0.5 * equal) / window.length) * 100;
}

export function deriveCot(rows: CotPositionRow[], lookback = 156): CotDerived[] {
  const sorted = [...rows].sort((a, b) => a.reportDate.localeCompare(b.reportDate));
  return sorted.map((r, i) => {
    const commercialNet = r.commercialLong - r.commercialShort;
    const nonCommercialNet = r.nonCommercialLong - r.nonCommercialShort;
    const prev = i > 0 ? sorted[i - 1]! : null;
    const window = sorted
      .slice(Math.max(0, i - lookback + 1), i + 1)
      .map((x) => x.nonCommercialLong - x.nonCommercialShort);
    return {
      ...r,
      commercialNet,
      nonCommercialNet,
      nonReportableNet: r.nonReportableLong - r.nonReportableShort,
      commercialNetChange: prev ? commercialNet - (prev.commercialLong - prev.commercialShort) : null,
      nonCommercialNetChange: prev ? nonCommercialNet - (prev.nonCommercialLong - prev.nonCommercialShort) : null,
      nonCommercialPercentile: window.length >= 26 ? percentileRank(window, nonCommercialNet) : null,
    };
  });
}
