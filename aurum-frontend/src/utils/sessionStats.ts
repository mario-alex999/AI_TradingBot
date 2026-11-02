import type { Candle } from '@/types/market';
import type { Interval } from './sessions';

export interface SessionRange {
  interval: Interval;
  high: number;
  low: number;
  range: number;
  bars: number;
}

/** High/low/range of candles whose open time falls inside the interval. Derived only from stored candles. */
export function rangeFor(candles: Candle[], interval: Interval): SessionRange | null {
  const inside = candles.filter((c) => c.time * 1000 >= interval.start && c.time * 1000 < interval.end);
  if (inside.length === 0) return null;
  const high = Math.max(...inside.map((c) => c.high));
  const low = Math.min(...inside.map((c) => c.low));
  return { interval, high, low, range: high - low, bars: inside.length };
}

export interface SessionStats {
  latest: SessionRange | null;
  previous: SessionRange | null;
  /** latest range ÷ average of up to 10 prior ranges */
  relativeVolatility: number | null;
  changeVsPrevious: number | null;
}

export function sessionStats(candles: Candle[], intervals: Interval[], now: number): SessionStats {
  const started = intervals.filter((i) => i.start <= now).sort((a, b) => b.start - a.start);
  const ranges = started.map((i) => rangeFor(candles, i)).filter((r): r is SessionRange => r !== null);
  const latest = ranges[0] ?? null;
  const previous = ranges[1] ?? null;
  const prior = ranges.slice(1, 11);
  const avg = prior.length ? prior.reduce((s, r) => s + r.range, 0) / prior.length : null;
  return {
    latest,
    previous,
    relativeVolatility: latest && avg ? latest.range / avg : null,
    changeVsPrevious: latest && previous && previous.range > 0 ? ((latest.range - previous.range) / previous.range) * 100 : null,
  };
}
