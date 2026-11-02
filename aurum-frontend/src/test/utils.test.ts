import { describe, expect, it } from 'vitest';
import { bollinger, ema, macd, rsi, sma, vwap } from '@/utils/indicators';
import { forexMarketOpen, intersect, sessionState, zonedTimeToUtc } from '@/utils/sessions';
import { formatPercent, formatPrice, formatDuration, PLACEHOLDER } from '@/utils/format';
import { calculatePositionSize } from '@/utils/risk';
import { deriveCot, percentileRank } from '@/utils/cot';
import { DEFAULT_SESSIONS } from '@/config/sessions';
import type { Candle } from '@/types/market';

const london = DEFAULT_SESSIONS.find((s) => s.id === 'london')!;
const newyork = DEFAULT_SESSIONS.find((s) => s.id === 'newyork')!;

describe('indicators', () => {
  it('computes a simple moving average with null warm-up', () => {
    expect(sma([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
  });

  it('seeds EMA with the SMA and then smooths', () => {
    const out = ema([1, 2, 3, 4, 5], 3);
    expect(out.slice(0, 2)).toEqual([null, null]);
    expect(out[2]).toBe(2);
    expect(out[3]).toBeCloseTo(3);
  });

  it('keeps RSI within 0–100 and returns 100 for monotonic gains', () => {
    const rising = Array.from({ length: 30 }, (_, i) => i + 1);
    const out = rsi(rising, 14);
    expect(out[13]).toBeNull();
    expect(out[29]).toBe(100);
    const noisy = Array.from({ length: 60 }, (_, i) => 100 + Math.sin(i) * 5);
    rsi(noisy).forEach((v) => v !== null && expect(v).toBeGreaterThanOrEqual(0));
    rsi(noisy).forEach((v) => v !== null && expect(v).toBeLessThanOrEqual(100));
  });

  it('produces MACD histogram = macd − signal', () => {
    const values = Array.from({ length: 80 }, (_, i) => 100 + i * 0.5 + Math.sin(i / 3));
    const m = macd(values);
    const i = 70;
    expect(m.histogram[i]).toBeCloseTo((m.macd[i] as number) - (m.signal[i] as number));
  });

  it('builds symmetric Bollinger bands around the SMA', () => {
    const b = bollinger([1, 2, 3, 4, 5, 6], 3);
    expect(b.middle[5]).toBe(5);
    expect((b.upper[5] as number) - 5).toBeCloseTo(5 - (b.lower[5] as number));
  });

  it('resets VWAP at each UTC day', () => {
    const c = (time: number, p: number, v: number): Candle => ({ time, open: p, high: p, low: p, close: p, volume: v });
    const out = vwap([c(0, 10, 1), c(3600, 20, 1), c(86_400, 50, 1)]);
    expect(out[1]).toBe(15);
    expect(out[2]).toBe(50);
  });
});

describe('sessions (DST-safe)', () => {
  it('converts London 08:00 to 07:00 UTC in summer and 08:00 UTC in winter', () => {
    expect(new Date(zonedTimeToUtc(2026, 7, 1, 8, 0, 'Europe/London')).toISOString()).toBe('2026-07-01T07:00:00.000Z');
    expect(new Date(zonedTimeToUtc(2026, 1, 15, 8, 0, 'Europe/London')).toISOString()).toBe('2026-01-15T08:00:00.000Z');
  });

  it('converts New York 08:00 across DST', () => {
    expect(new Date(zonedTimeToUtc(2026, 7, 1, 8, 0, 'America/New_York')).toISOString()).toBe('2026-07-01T12:00:00.000Z');
    expect(new Date(zonedTimeToUtc(2026, 1, 15, 8, 0, 'America/New_York')).toISOString()).toBe('2026-01-15T13:00:00.000Z');
  });

  it('reports London open mid-morning on a weekday and closed at the weekend', () => {
    expect(sessionState(london, Date.UTC(2026, 6, 1, 10)).isOpen).toBe(true);
    const sat = sessionState(london, Date.UTC(2026, 6, 4, 10));
    expect(sat.isOpen).toBe(false);
    expect(sat.isWeekend).toBe(true);
    expect(new Date(sat.next!.start).toISOString()).toBe('2026-07-06T07:00:00.000Z');
  });

  it('finds the London / New York overlap', () => {
    const now = Date.UTC(2026, 6, 1, 0);
    const o = intersect(sessionState(london, now).intervals, sessionState(newyork, now).intervals).find((i) => i.start > now)!;
    expect(new Date(o.start).toISOString()).toBe('2026-07-01T12:00:00.000Z');
    expect(new Date(o.end).toISOString()).toBe('2026-07-01T16:00:00.000Z');
  });

  it('applies the Sunday-to-Friday FX week in New York time', () => {
    expect(forexMarketOpen(Date.UTC(2026, 6, 4, 12))).toBe(false); // Saturday
    expect(forexMarketOpen(Date.UTC(2026, 6, 1, 12))).toBe(true); // Wednesday
    expect(forexMarketOpen(Date.UTC(2026, 6, 5, 20))).toBe(false); // Sunday 16:00 NY
    expect(forexMarketOpen(Date.UTC(2026, 6, 5, 22))).toBe(true); // Sunday 18:00 NY
  });
});

describe('formatting', () => {
  it('uses the placeholder for missing values instead of inventing numbers', () => {
    expect(formatPrice(null, 5)).toBe(PLACEHOLDER);
    expect(formatPercent(undefined)).toBe(PLACEHOLDER);
  });
  it('formats signed percentages and durations', () => {
    expect(formatPercent(1.234, 2, true)).toBe('+1.23%');
    expect(formatDuration(3_723_000)).toBe('1h 2m');
  });
});

describe('risk & COT maths', () => {
  it('sizes positions from balance, risk and stop distance', () => {
    expect(calculatePositionSize({ balance: 10000, riskPct: 1, stopPoints: 200, pointValue: 1 })).toEqual({ riskAmount: 100, lots: 0.5 });
  });
  it('derives net positions, weekly change and percentile', () => {
    expect(percentileRank([1, 2, 3, 4], 4)).toBe(87.5);
    const rows = Array.from({ length: 30 }, (_, i) => ({
      reportDate: `2026-01-${String(i + 1).padStart(2, '0')}`,
      publicationDate: '',
      openInterest: 100,
      commercialLong: 50,
      commercialShort: 40,
      nonCommercialLong: 30 + i,
      nonCommercialShort: 20,
      nonReportableLong: 5,
      nonReportableShort: 5,
    }));
    const d = deriveCot(rows);
    expect(d[0]!.commercialNet).toBe(10);
    expect(d[1]!.nonCommercialNetChange).toBe(1);
    expect(d[29]!.nonCommercialPercentile).toBeGreaterThan(95);
  });
});
